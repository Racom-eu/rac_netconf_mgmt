"""NetconfDevice for managing NETCONF connections and fetching device configuration and schemas."""

import logging
from types import TracebackType
from typing import Any

import lxml.etree as _lxml_etree
from ncclient import NCClientError, manager
from ncclient.transport.errors import SessionCloseError, TransportError

from .config import NetconfConfig
from .constants import DEFAULT_DATASTORE
from .exceptions import (
    NetconfMgmtConfigError,
    NetconfMgmtConnectionError,
    NetconfMgmtOperationError,
    NetconfMgmtSessionClosedError,
)

etree: Any = _lxml_etree

DEFAULT_PORT = 830
DEFAULT_TIMEOUT = 30
DEFAULT_OPERATION = "merge"
CLOSE_SESSION_TIMEOUT = 2
NC_BASE_NAMESPACE = "urn:ietf:params:xml:ns:netconf:base:1.0"
NC_MONITORING_NAMESPACE = "urn:ietf:params:xml:ns:yang:ietf-netconf-monitoring"

log = logging.getLogger(__name__)


class NetconfDevice:
    """
    NETCONF device interface combining connection lifecycle with config and schema operations.
    Implements SchemaFetcherProtocol for use with NetconfConfig.from_device().

    Not thread-safe — do not share a single instance across threads.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        user: str = "",
        password: str = "",
        timeout: int = DEFAULT_TIMEOUT,
        hostkey_verify: bool = True,
        hostkey_b64: str | None = None,
        use_libssh: bool = True,
        **connect_kwargs: Any,
    ) -> None:
        """
        Initialize device interface with connection parameters.

        ### Parameters:
        - host: str - Device hostname or IP address
        - port: int - NETCONF port number (default: DEFAULT_PORT)
        - user: str - Username for authentication
        - password: str - Password for authentication
        - timeout: int - Connection timeout in seconds (default: DEFAULT_TIMEOUT)
        - hostkey_verify: bool - Verify SSH host key (default: True)
        - hostkey_b64: str | None - Expected host key in base64; implies hostkey_verify=True
        - use_libssh: bool - Use libssh backend for ncclient (default: True)
        - **connect_kwargs: Additional keyword arguments forwarded verbatim to ncclient's
            manager.connect(). See ncclient documentation for details.
        """
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.timeout = timeout
        self.hostkey_verify = hostkey_verify
        self.hostkey_b64 = hostkey_b64
        self.use_libssh = use_libssh
        self._connect_kwargs = connect_kwargs
        self._manager: Any = None

    @property
    def _active_manager(self) -> Any:
        """
        Return the ncclient manager for a connected session.

        ### Returns:
        - Any: The ncclient Manager instance.
        """
        if self._manager is None:
            raise NetconfMgmtConnectionError("Not connected to device, call connect() first.")
        if not self._manager.connected:
            # server-side close (idle timeout, restart, etc.)
            raise NetconfMgmtSessionClosedError("Session was closed by the server, use reconnect() to reestablish.")
        return self._manager

    def connect(self) -> None:
        """
        Establish NETCONF connection to the device.
        """
        if self._manager is not None:
            raise NetconfMgmtConnectionError("Connection already initialized, use reconnect() to reestablish.")

        try:
            self._manager = manager.connect(  # type: ignore[reportUnknownMemberType]
                host=self.host,
                port=self.port,
                username=self.user,
                password=self.password,
                timeout=self.timeout,
                hostkey_verify=self.hostkey_verify,
                hostkey_b64=self.hostkey_b64,
                use_libssh=self.use_libssh,
                **self._connect_kwargs,
            )
        except NCClientError as e:
            raise NetconfMgmtConnectionError(f"Failed to connect to {self.host}:{self.port}: {e}") from e

        log.debug("Connected to device %s:%s", self.host, self.port)

    def reconnect(self) -> None:
        """
        Close any existing connection and establish a new one.

        Useful for recovering from a closed session — allows higher-level code
        to implement automatic reconnect logic on top of NetconfMgmtSessionClosedError.
        """
        self.close()
        self.connect()

    def close(self) -> None:
        """
        Close the NETCONF connection.

        Errors during close are logged and suppressed — close is best-effort.
        """
        if self._manager is not None:
            try:
                # Short RPC timeout — close-session would otherwise block 30s on a
                # dead or unresponsive server (e.g. mid-shutdown, cable pull).
                self._manager.timeout = CLOSE_SESSION_TIMEOUT
                self._manager.close_session()
            except Exception as e:  # pylint: disable=broad-exception-caught  # noqa: BLE001
                log.debug("Graceful close_session failed (%s), tearing down transport.", e)
            # Function close_session() above closes the transport only on RPC success.
            # This explicit call is the fallback for the failure path (no-op otherwise).
            try:
                self._manager._session.close()  # noqa: SLF001  # pylint: disable=protected-access
            except Exception as e:  # pylint: disable=broad-exception-caught  # noqa: BLE001
                log.warning("Transport close failed for %s:%s: %s", self.host, self.port, e)
            log.debug("Disconnected from device %s:%s", self.host, self.port)
            self._manager = None

    def __enter__(self) -> "NetconfDevice":
        """
        Context manager entry — connect to device.

        ### Returns:
        - NetconfDevice: Self reference for use in with-block.
        """
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """
        Context manager exit — disconnect from device.
        """
        self.close()

    def get_config(self, xpath: str | None = None, datastore: str = DEFAULT_DATASTORE) -> NetconfConfig:
        """
        Retrieve configuration from the device.

        Delegates to NetconfConfig.from_device(). If xpath is given, fetches config
        filtered to that XPath expression. If None, fetches the full configuration.

        ### Parameters:
        - xpath: str | None - XPath filter expression (e.g. "/mbm-root:*"). If None, fetches all config.
        - datastore: str - NETCONF datastore to read from (default: DEFAULT_DATASTORE).

        ### Returns:
        - NetconfConfig: Configuration object with XML and all required YANG schemas.
        """
        config = NetconfConfig.from_device(device=self, xpath=xpath, datastore=datastore)
        log.debug("Retrieved config from datastore '%s' (xpath: %s)", datastore, xpath)
        return config

    def set_config(
        self, config: NetconfConfig, operation: str = DEFAULT_OPERATION, datastore: str = DEFAULT_DATASTORE
    ) -> None:
        """
        Write configuration back to the device.

        ### Parameters:
        - config: NetconfConfig - Configuration object to write.
        - operation: str - NETCONF edit operation, "merge" or "replace" (default: DEFAULT_OPERATION).
        - datastore: str - NETCONF target datastore (default: DEFAULT_DATASTORE).
        """
        # config.xml may be a fragment with multiple top-level siblings (one per YANG module
        # when no xpath filter was used). Wrap in a placeholder root so lxml can parse it,
        # then move the children under <nc:config>.
        config_element = etree.Element(f"{{{NC_BASE_NAMESPACE}}}config")
        config_element.extend(etree.fromstring(f"<_>{config.xml}</_>"))

        try:
            response = self._active_manager.edit_config(
                target=datastore,
                config=config_element,
                default_operation=operation,
            )
        except SessionCloseError as e:
            raise NetconfMgmtSessionClosedError(f"Session closed by server during edit-config: {e}") from e
        except TransportError as e:
            raise NetconfMgmtConnectionError(f"Transport error during edit-config: {e}") from e
        except NCClientError as e:
            raise NetconfMgmtConfigError(f"Failed to write config to device: {e}") from e

        if not response.ok:
            raise NetconfMgmtConfigError(f"edit-config operation failed: {response}")

        log.debug("Config written to device with operation '%s'", operation)

    def get_schema(self, identifier: str, revision: str | None = None) -> str:
        """
        Fetch raw YANG schema string from the device.

        ### Parameters:
        - identifier: str - YANG module identifier (e.g. "ra2-root").
        - revision: str | None - YANG module revision date (e.g. "2013-07-15"). If None, device returns latest.

        ### Returns:
        - str: Raw YANG schema string.
        """
        try:
            reply = self._active_manager.get_schema(identifier=identifier, version=revision)
        except SessionCloseError as e:
            raise NetconfMgmtSessionClosedError(
                f"Session closed by server during get-schema '{identifier}': {e}"
            ) from e
        except TransportError as e:
            raise NetconfMgmtConnectionError(f"Transport error during get-schema '{identifier}': {e}") from e
        except NCClientError as e:
            raise NetconfMgmtConfigError(f"Failed to fetch schema '{identifier}': {e}") from e

        log.debug("Fetched schema for module '%s' (revision: %s)", identifier, revision)
        return reply.data

    def fetch_config_xml(
        self, xpath: str | None, xmlns: dict[str, str], datastore: str = DEFAULT_DATASTORE
    ) -> tuple[str, set[str]]:
        """
        Fetch raw XML configuration string from the device.

        ### Parameters:
        - xpath: str | None - XPath filter expression. If given, passed as ncclient XPath filter
          with namespace prefix bindings from xmlns. If None, fetches the full configuration.
        - xmlns: dict[str, str] - Mapping of namespace prefix to URI for the XPath expression.
        - datastore: str - NETCONF datastore to read from.

        ### Returns:
        - tuple[str, set[str]]: Concatenated XML string of all returned root elements,
          and the set of XML namespace URIs of those root elements.
        """
        try:
            if xpath is not None:
                filter_ = ("xpath", (xmlns, xpath))
                reply = self._active_manager.get_config(source=datastore, filter=filter_)
            else:
                reply = self._active_manager.get_config(source=datastore)
        except SessionCloseError as e:
            raise NetconfMgmtSessionClosedError(f"Session closed by server during get-config: {e}") from e
        except TransportError as e:
            raise NetconfMgmtConnectionError(f"Transport error during get-config: {e}") from e
        except NCClientError as e:
            raise NetconfMgmtConfigError(f"Failed to fetch config from device: {e}") from e

        nodes = list(reply.data_ele)
        if not nodes:
            raise NetconfMgmtConfigError("No config nodes returned from device.")

        namespaces = {etree.QName(node).namespace for node in nodes if etree.QName(node).namespace}  # pylint: disable=using-constant-test
        xml = "".join(etree.tostring(node, encoding="unicode") for node in nodes)
        log.debug("Fetched config from datastore '%s' (xpath: %s, %d root nodes)", datastore, xpath, len(nodes))
        return xml, namespaces

    def get_module_list(self) -> list[tuple[str, str | None, str | None]]:
        """
        Return all YANG schemas from NETCONF Monitoring with name, revision, and namespace.

        ### Returns:
        - list[tuple[str, str | None, str | None]]: List of (name, revision, namespace) triples
          for all yang-format schemas. revision and namespace are None if absent.
        """
        ns_nm = NC_MONITORING_NAMESPACE
        filter_elem = etree.Element(f"{{{ns_nm}}}netconf-state")
        etree.SubElement(filter_elem, f"{{{ns_nm}}}schemas")
        yang_schemas_filter = etree.tostring(filter_elem, encoding="unicode")

        try:
            reply = self._active_manager.get(filter=("subtree", yang_schemas_filter))
        except SessionCloseError as e:
            raise NetconfMgmtSessionClosedError(f"Session closed by server during get-module-list: {e}") from e
        except TransportError as e:
            raise NetconfMgmtConnectionError(f"Transport error during get-module-list: {e}") from e
        except NCClientError as e:
            raise NetconfMgmtOperationError(f"Failed to fetch module list: {e}") from e

        schemas_elem = reply.data_ele.find(f"{{{ns_nm}}}netconf-state/{{{ns_nm}}}schemas")
        if schemas_elem is None:
            raise NetconfMgmtOperationError(
                "No <schemas> element provided in /netconf-state response. "
                "Device may not support RFC 6022 NETCONF Monitoring."
            )

        modules: list[tuple[str, str | None, str | None]] = []
        for schema in schemas_elem.findall(f"{{{ns_nm}}}schema"):
            if schema.findtext(f"{{{ns_nm}}}format") != "yang":
                continue
            identifier = schema.findtext(f"{{{ns_nm}}}identifier")
            version = schema.findtext(f"{{{ns_nm}}}version") or None
            namespace = schema.findtext(f"{{{ns_nm}}}namespace") or None
            modules.append((identifier, version, namespace))

        log.debug("Found %d YANG schemas via NETCONF Monitoring.", len(modules))
        return modules

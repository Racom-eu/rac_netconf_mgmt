"""NetconfConfig and SchemaFetcherProtocol for fetching and parsing NETCONF device configuration."""

import json
import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any, Protocol

import libyang
from libyang.xpath import xpath_split

from .constants import DEFAULT_DATASTORE
from .exceptions import NetconfMgmtConfigError

log = logging.getLogger(__name__)


class SchemaFetcherProtocol(Protocol):
    """
    Interface for objects that can supply YANG schemas and raw config XML.
    NetconfDevice satisfies this protocol.
    """

    def get_schema(self, identifier: str, revision: str | None = None) -> str: ...
    def fetch_config_xml(self, xpath: str | None, xmlns: dict[str, str], datastore: str) -> tuple[str, set[str]]: ...
    def get_module_list(self) -> list[tuple[str, str | None, str | None]]: ...


class NetconfConfig:
    """
    Source-agnostic NETCONF configuration object.
    Holds raw XML configuration and all YANG schemas needed to parse it.
    Converts to Python dict on demand via libyang.
    """

    def __init__(self, xml: str, dep_yang: dict[str, str] | None = None) -> None:
        """
        Initialize configuration object from raw XML and YANG schemas.

        For typical use, prefer NetconfConfig.from_device() which fetches both
        from a live device. Use this constructor directly when XML and YANG
        schemas come from another source (e.g. local files).

        ### Parameters:
        - xml: str - Raw XML configuration string.
        - dep_yang: dict[str, str] | None - Mapping of module name to YANG schema string.
            All schemas required to parse the XML must be included. None means no schemas.
        """
        self._xml = xml
        self._dep_yang: dict[str, str] = dep_yang or {}

    @classmethod
    def from_device(  # noqa: C901
        cls,
        device: SchemaFetcherProtocol,
        xpath: str | None = None,
        datastore: str = DEFAULT_DATASTORE,
    ) -> "NetconfConfig":
        """
        Fetch configuration and all required YANG schemas from a live device.

        If xpath is given, fetches XML filtered to that XPath expression and uses
        a temporary libyang context to discover all schema dependencies recursively.
        Namespace prefixes in the XPath are resolved via the device's module list.
        If xpath is None, fetches the full configuration and all schemas available
        on the device.

        ### Parameters:
        - device: SchemaFetcherProtocol - Connected device implementing the protocol.
        - xpath: str | None - XPath filter expression (e.g. "/mbm-root:*"). If None, fetches all config.
        - datastore: str - NETCONF datastore to read from (default: DEFAULT_DATASTORE).

        ### Returns:
        - NetconfConfig: Configuration object ready for as_dict().
        """
        module_list = device.get_module_list()
        # Map of available module names to their revisions (or None if no revision).
        available: dict[str, str | None] = {name: rev for name, rev, _ in module_list}
        # Map of module name to namespace for all modules that have a namespace.
        module_to_ns: dict[str, str] = {name: ns for name, _, ns in module_list if ns}

        # Resolve XPath namespace prefixes to URIs using the device's module list.
        # In YANG, module name == namespace prefix by convention.
        xmlns: dict[str, str] = {}
        if xpath is not None:
            for prefix, _, _ in xpath_split(xpath):
                if prefix:
                    ns = module_to_ns.get(prefix)
                    if ns is not None:
                        xmlns[prefix] = ns
                    else:
                        raise NetconfMgmtConfigError(
                            f"XPath prefix '{prefix}' in '{xpath}' is not a module advertised by "
                            f"the device. Known modules: {sorted(module_to_ns)}"
                        )

        xml, xml_namespaces = device.fetch_config_xml(xpath, xmlns, datastore)
        needed_modules = {name for name, _, ns in module_list if ns and ns in xml_namespaces}

        # Cache for fetched schemas to avoid redundant get_schema calls during dependency resolution.
        schema_cache: dict[str, str] = {}

        def _loader(
            mod_name: str | None,
            mod_rev: str | None,
            submod_name: str | None,
            submod_rev: str | None,
        ) -> tuple[str, str] | None:
            """libyang external module loader — fetches schemas from device on demand."""
            name = mod_name or submod_name
            rev = mod_rev or submod_rev
            if name in schema_cache:
                return ("yang", schema_cache[name])
            if name is None or name not in available:
                return None
            revision = rev or available[name]
            try:
                yang_str = device.get_schema(name, revision)
                schema_cache[name] = yang_str
            except Exception:  # pylint: disable=broad-exception-caught  # noqa: BLE001
                log.debug(
                    "Schema '%s' not available on device, skipping.",
                    name,
                    exc_info=True,
                )
                return None
            return ("yang", yang_str)

        ctx = None
        try:
            ctx = libyang.Context()
            ctx.external_module_loader.set_module_data_clb(_loader)
            for module_name in needed_modules:
                if module_name in schema_cache:
                    continue
                revision = available.get(module_name)
                yang_str = device.get_schema(module_name, revision)
                schema_cache[module_name] = yang_str
                try:
                    ctx.parse_module_str(yang_str, fmt="yang")  # type: ignore[reportUnknownMemberType]
                except libyang.LibyangError as e:
                    raise NetconfMgmtConfigError(f"Failed to parse schema for module '{module_name}': {e}") from e
        finally:
            if ctx is not None:
                ctx.destroy()

        log.debug(
            "Fetched config from '%s'; %d schemas loaded (%d XML modules).",
            datastore,
            len(schema_cache),
            len(needed_modules),
        )
        return cls(xml, schema_cache)

    @contextmanager
    def _libyang_ctx(self) -> Generator[libyang.Context, None, None]:
        """
        Yield a libyang context with all dep_yang schemas loaded.

        Creates a fresh libyang Context, registers an external module loader
        that resolves dependencies from self._dep_yang, and parses all known
        schemas into the context. The context is destroyed when the with-block
        exits — this keeps the C-level memory pool clean across operations.

        ### Yields:
        - libyang.Context: Context with all dep_yang schemas loaded.
        """
        dep_yang = self._dep_yang
        ctx = libyang.Context()
        try:

            def _loader(
                mod_name: str | None,
                mod_rev: str | None,
                submod_name: str | None,
                submod_rev: str | None,
            ) -> tuple[str, str] | None:
                """Return (format, schema_str) for known modules, None to skip."""
                name = mod_name or submod_name
                if name and name in dep_yang:
                    return ("yang", dep_yang[name])
                return None

            ctx.external_module_loader.set_module_data_clb(_loader)
            for yang_str in dep_yang.values():
                try:
                    ctx.parse_module_str(yang_str, fmt="yang")  # type: ignore[reportUnknownMemberType]
                except libyang.LibyangError:  # noqa: PERF203
                    log.debug("Module already loaded or skipped by libyang, continuing.")
            yield ctx
        finally:
            ctx.destroy()

    def _parse_xml_dnode(self, ctx: libyang.Context) -> libyang.DNode:
        """
        Parse the stored XML into a libyang DNode tree using the given context.

        ### Parameters:
        - ctx: libyang.Context - Context with all required YANG schemas loaded.

        ### Returns:
        - libyang.DNode: Root data node.
        """
        dnode = ctx.parse_data_mem(self._xml, fmt="xml", no_state=True, parse_only=True)
        if dnode is None:
            raise NetconfMgmtConfigError("Failed to parse configuration: missing or unresolvable YANG schemas.")
        return dnode

    def as_dict(self) -> dict[str, Any]:
        """
        Parse XML configuration against the YANG schemas using libyang.

        ### Returns:
        - dict: Full configuration as a Python dictionary.
        """
        try:
            with self._libyang_ctx() as ctx:
                dnode = self._parse_xml_dnode(ctx)
                try:
                    return json.loads(dnode.print_mem("json", with_siblings=True))
                finally:
                    dnode.free()
        except libyang.LibyangError as e:
            raise NetconfMgmtConfigError(f"Failed to parse configuration: {e}") from e

    def update_from_dict(self, d: dict[str, Any]) -> None:
        """
        Replace configuration from a modified Python dict.

        The dict must be in the format returned by as_dict() — module-prefixed
        top-level keys (e.g. "mbm-root:config_data"). Unknown schema nodes cause
        an exception.

        ### Parameters:
        - d: dict - Configuration dict to convert to XML.

        ### Returns:
        - None
        """
        try:
            with self._libyang_ctx() as ctx:
                dnode = ctx.parse_data_mem(
                    json.dumps(d),
                    fmt="json",
                    no_state=True,
                    parse_only=True,
                    strict=True,
                )
                if dnode is None:
                    raise NetconfMgmtConfigError("Failed to parse dict: missing or unresolvable YANG schemas.")
                try:
                    self._xml = str(dnode.print_mem("xml", with_siblings=True))
                finally:
                    dnode.free()
        except libyang.LibyangError as e:
            raise NetconfMgmtConfigError(f"Failed to update config from dict: {e}") from e

    def set(self, xpath: str, value: str) -> None:
        """
        Set a leaf value at the given data path.

        Uses libyang data path format — module prefix only at module boundaries:
            /module-name:container/leaf

        ### Parameters:
        - xpath: str - Data path to the target leaf node.
        - value: str - New value for the leaf.

        ### Returns:
        - None
        """
        try:
            with self._libyang_ctx() as ctx:
                dnode = self._parse_xml_dnode(ctx)
                try:
                    dnode.new_path(xpath, value, opt_update=True)
                    self._xml = str(dnode.print_mem("xml", with_siblings=True))
                finally:
                    dnode.free()
        except libyang.LibyangError as e:
            raise NetconfMgmtConfigError(f"Failed to set '{xpath}': {e}") from e

    def get(self, xpath: str) -> str | int | bool | None:
        """
        Get a typed leaf value at the given data path.

        Uses libyang data path format — module prefix only at module boundaries:
            /module-name:container/leaf

        ### Parameters:
        - xpath: str - Data path to the target leaf node.

        ### Returns:
        - str | int | bool | None: Typed value of the leaf as provided by libyang.
        """
        try:
            with self._libyang_ctx() as ctx:
                dnode = self._parse_xml_dnode(ctx)
                try:
                    node = dnode.find_path(xpath)
                    if node is None:
                        raise NetconfMgmtConfigError(f"Path '{xpath}' not found in configuration.")
                    if not isinstance(node, libyang.DLeaf):
                        raise NetconfMgmtConfigError(f"Path '{xpath}' does not point to a leaf node.")
                    return node.value()  # type: ignore[reportUnknownMemberType]
                finally:
                    dnode.free()
        except libyang.LibyangError as e:
            raise NetconfMgmtConfigError(f"Failed to get '{xpath}': {e}") from e

    @property
    def xml(self) -> str:
        """
        Raw XML configuration string.

        ### Returns:
        - str: XML configuration.
        """
        return self._xml

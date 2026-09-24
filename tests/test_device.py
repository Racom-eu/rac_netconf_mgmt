# pylint: disable=missing-module-docstring,missing-class-docstring,missing-function-docstring,protected-access,invalid-name
from typing import Any
from unittest.mock import MagicMock

import lxml.etree as _lxml_etree
import pytest
from ncclient import NCClientError

from rr_netconf_mgmt.device import NetconfDevice
from rr_netconf_mgmt.exceptions import (
    NetconfMgmtConfigError,
    NetconfMgmtConnectionError,
    NetconfMgmtOperationError,
    NetconfMgmtSessionClosedError,
)

etree: Any = _lxml_etree


def _make_device() -> NetconfDevice:
    device = NetconfDevice(host="localhost", port=830, user="admin", password="")
    device._manager = MagicMock()
    return device


class TestGetSchema:
    def test_without_revision(self):
        device = _make_device()
        device._manager.get_schema.return_value.data = "yang-content"

        result = device.get_schema("test-module")

        device._manager.get_schema.assert_called_once_with(identifier="test-module", version=None)
        assert result == "yang-content"

    def test_with_revision(self):
        device = _make_device()
        device._manager.get_schema.return_value.data = "yang-content"

        result = device.get_schema("test-module", revision="2024-01-01")

        device._manager.get_schema.assert_called_once_with(identifier="test-module", version="2024-01-01")
        assert result == "yang-content"

    def test_raises_schema_exception_on_ncclient_error(self):

        device = _make_device()
        device._manager.get_schema.side_effect = NCClientError("rpc error")

        with pytest.raises(NetconfMgmtConfigError):
            device.get_schema("test-module")


class TestFetchConfigXml:
    def _make_reply(self, xml_str: str) -> MagicMock:
        """Build a mock ncclient reply whose data_ele contains the parsed XML elements."""
        root = etree.fromstring(f"<data>{xml_str}</data>")
        reply = MagicMock()
        reply.data_ele = list(root)
        return reply

    def test_with_xpath_passes_ncclient_filter_tuple(self):
        device = _make_device()
        device._manager.get_config.return_value = self._make_reply(
            '<settings xmlns="urn:test:root"><name>hello</name></settings>'
        )

        xml, _ = device.fetch_config_xml("/root-module:*", {"root-module": "urn:test:root"}, "running")

        call_kwargs = device._manager.get_config.call_args.kwargs
        assert call_kwargs["source"] == "running"
        assert call_kwargs["filter"] == (
            "xpath",
            ({"root-module": "urn:test:root"}, "/root-module:*"),
        )
        assert '<settings xmlns="urn:test:root">' in xml

    def test_returns_top_level_namespaces(self):
        device = _make_device()
        device._manager.get_config.return_value = self._make_reply(
            '<settings xmlns="urn:test:root"><name>hello</name></settings><other xmlns="urn:test:other"/>'
        )

        _, namespaces = device.fetch_config_xml(None, {}, "running")

        assert namespaces == {"urn:test:root", "urn:test:other"}

    def test_without_xpath_no_filter(self):
        device = _make_device()
        device._manager.get_config.return_value = self._make_reply(
            '<settings xmlns="urn:test:root"><name>hello</name></settings>'
        )

        device.fetch_config_xml(None, {}, "running")

        call_kwargs = device._manager.get_config.call_args.kwargs
        assert "filter" not in call_kwargs or call_kwargs.get("filter") is None

    def test_raises_config_exception_on_ncclient_error(self):

        device = _make_device()
        device._manager.get_config.side_effect = NCClientError("rpc error")

        with pytest.raises(NetconfMgmtConfigError):
            device.fetch_config_xml(None, {}, "running")

    def test_raises_on_empty_response(self):
        device = _make_device()
        device._manager.get_config.return_value = self._make_reply("")

        with pytest.raises(NetconfMgmtConfigError):
            device.fetch_config_xml("/root-module:*", {"root-module": "urn:test:root"}, "running")


class TestGetModuleList:
    NC_MON_NS = "urn:ietf:params:xml:ns:yang:ietf-netconf-monitoring"

    def _make_get_reply(self, schemas_xml: str) -> MagicMock:
        data_ele = etree.fromstring(
            f'<data><netconf-state xmlns="{self.NC_MON_NS}"><schemas>{schemas_xml}</schemas></netconf-state></data>'
        )
        reply = MagicMock()
        reply.data_ele = data_ele
        return reply

    def _schema_xml(self, identifier: str, version: str, namespace: str, fmt: str = "yang") -> str:
        ns = self.NC_MON_NS
        return (
            f'<schema xmlns="{ns}">'
            f"<identifier>{identifier}</identifier>"
            f"<version>{version}</version>"
            f"<format>{fmt}</format>"
            f"<namespace>{namespace}</namespace>"
            f"<location>NETCONF</location>"
            f"</schema>"
        )

    def test_returns_name_revision_namespace_triples(self):
        device = _make_device()
        device._manager.get.return_value = self._make_get_reply(
            self._schema_xml("test-module", "2024-01-01", "urn:test:module")
        )

        modules = device.get_module_list()

        assert ("test-module", "2024-01-01", "urn:test:module") in modules

    def test_yin_entries_filtered_out(self):
        device = _make_device()
        device._manager.get.return_value = self._make_get_reply(
            self._schema_xml("test-module", "2024-01-01", "urn:test:module", "yang")
            + self._schema_xml("test-module", "2024-01-01", "urn:test:module", "yin")
        )

        modules = device.get_module_list()

        assert len(modules) == 1

    def test_absent_namespace_element_returns_none(self):
        device = _make_device()
        ns = self.NC_MON_NS
        schemas_xml = (
            f'<schema xmlns="{ns}">'
            f"<identifier>no-ns-module</identifier>"
            f"<version>2024-01-01</version>"
            f"<format>yang</format>"
            f"<location>NETCONF</location>"
            f"</schema>"
        )
        device._manager.get.return_value = self._make_get_reply(schemas_xml)

        modules = device.get_module_list()

        assert ("no-ns-module", "2024-01-01", None) in modules

    def test_raises_operation_exception_on_rpc_error(self):

        device = _make_device()
        device._manager.get.side_effect = NCClientError("rpc error")

        with pytest.raises(NetconfMgmtOperationError):
            device.get_module_list()

    def test_raises_operation_exception_when_schemas_element_missing(self):
        device = _make_device()
        reply = MagicMock()
        reply.data_ele = etree.fromstring("<data/>")
        device._manager.get.return_value = reply

        with pytest.raises(NetconfMgmtOperationError):
            device.get_module_list()

    def test_raises_connection_exception_when_not_connected(self):
        device = NetconfDevice(host="localhost", port=830, user="admin", password="")

        with pytest.raises(NetconfMgmtConnectionError):
            device.get_module_list()


class TestClose:
    def test_close_is_best_effort_and_tears_down_transport_on_rpc_failure(self):
        device = _make_device()
        device._manager.close_session.side_effect = RuntimeError("hung")
        session = device._manager._session

        device.close()

        session.close.assert_called_once()
        assert device._manager is None

    def test_raises_session_closed_when_manager_not_connected(self):
        device = _make_device()
        device._manager.connected = False

        with pytest.raises(NetconfMgmtSessionClosedError):
            device.get_module_list()

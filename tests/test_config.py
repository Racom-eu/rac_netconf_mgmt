# pylint: disable=missing-module-docstring,missing-class-docstring,missing-function-docstring,protected-access
import pathlib
import re

import pytest

from rr_netconf_mgmt.config import NetconfConfig
from rr_netconf_mgmt.exceptions import NetconfMgmtConfigError

DATA = pathlib.Path(__file__).parent / "data"


def _read(path: pathlib.Path | str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


TEST_XML = '<settings xmlns="urn:test:module"><name>hello</name><count>5</count></settings>'
TEST_YANG = _read(f"{DATA}/test_module.yang")


class TestConstruction:
    def test_stores_xml(self):
        config = NetconfConfig(TEST_XML, {"test-module": TEST_YANG})
        assert config.xml == TEST_XML

    def test_no_dep_yang_defaults_to_empty(self):
        config = NetconfConfig(TEST_XML)
        assert config.xml == TEST_XML

    def test_dep_yang_stored(self):
        config = NetconfConfig(TEST_XML, {"test-module": TEST_YANG})
        assert "test-module" in config._dep_yang


class TestAsDict:
    def test_basic(self):
        config = NetconfConfig(TEST_XML, {"test-module": TEST_YANG})
        result = config.as_dict()
        assert result == {"test-module:settings": {"name": "hello", "count": 5}}

    def test_raises_on_missing_schema(self):
        config = NetconfConfig(TEST_XML)
        with pytest.raises(NetconfMgmtConfigError):
            config.as_dict()

    def test_with_dep_yang_resolves_imports(self):
        xml = _read(f"{DATA}/root-config.xml")
        root_yang = _read(f"{DATA}/root-module.yang")
        dep_yang = _read(f"{DATA}/dep-module.yang")
        config = NetconfConfig(xml, {"root-module": root_yang, "dep-module": dep_yang})
        result = config.as_dict()
        assert result == {"root-module:settings": {"name": "hello"}}

    def test_multiple_top_level_siblings_all_included(self):
        # Regression test: as_dict() must include ALL top-level data nodes,
        # not just the first one. Before the fix, print_mem() without
        # with_siblings=True silently dropped sibling nodes.
        xml = TEST_XML + _read(f"{DATA}/root-config.xml")
        root_yang = _read(f"{DATA}/root-module.yang")
        dep_yang = _read(f"{DATA}/dep-module.yang")
        config = NetconfConfig(
            xml,
            {
                "test-module": TEST_YANG,
                "root-module": root_yang,
                "dep-module": dep_yang,
            },
        )
        result = config.as_dict()
        assert "test-module:settings" in result
        assert "root-module:settings" in result


class MockSchemaFetcher:
    """Minimal SchemaFetcherProtocol implementation for tests."""

    def __init__(
        self,
        schemas: dict[str, str],
        config_xml: str,
        available: list[tuple[str, str | None]] | None = None,
    ):
        self._schemas = schemas
        self._config_xml = config_xml
        self._available = available if available is not None else [(name, None) for name in schemas]
        self.get_schema_calls: list[tuple[str, str | None]] = []
        self.fetch_config_xml_calls: list[tuple[str | None, dict[str, str], str]] = []

    def get_schema(self, identifier: str, revision: str | None = None) -> str:
        self.get_schema_calls.append((identifier, revision))
        if identifier not in self._schemas:
            raise NetconfMgmtConfigError(f"Schema '{identifier}' not found")
        return self._schemas[identifier]

    def fetch_config_xml(self, xpath: str | None, xmlns: dict[str, str], datastore: str) -> tuple[str, set[str]]:
        self.fetch_config_xml_calls.append((xpath, xmlns, datastore))
        namespaces = set(re.findall(r'\bxmlns\s*=\s*"([^"]+)"', self._config_xml))
        return self._config_xml, namespaces

    def get_module_list(self) -> list[tuple[str, str | None, str | None]]:
        result: list[tuple[str, str | None, str | None]] = []
        for name, revision in self._available:
            namespace = None
            if name in self._schemas:
                ns_match = re.search(r'namespace\s+["\']([^"\']+)["\']', self._schemas[name])
                if ns_match:
                    namespace = ns_match.group(1)
            result.append((name, revision, namespace))
        return result


ROOT_YANG = _read(DATA / "root-module.yang")
DEP_YANG = _read(DATA / "dep-module.yang")
ROOT_XML = _read(DATA / "root-config.xml")


class TestFromDevice:
    def test_xpath_path_dep_yang_contains_root_and_dep(self):
        fetcher = MockSchemaFetcher(
            schemas={"root-module": ROOT_YANG, "dep-module": DEP_YANG},
            config_xml=ROOT_XML,
        )
        config = NetconfConfig.from_device(fetcher, xpath="/root-module:*")
        assert "root-module" in config._dep_yang
        assert "dep-module" in config._dep_yang

    def test_xpath_path_xml_stored(self):
        fetcher = MockSchemaFetcher(
            schemas={"root-module": ROOT_YANG, "dep-module": DEP_YANG},
            config_xml=ROOT_XML,
        )
        config = NetconfConfig.from_device(fetcher, xpath="/root-module:*")
        assert config.xml == ROOT_XML

    def test_xpath_and_xmlns_passed_to_fetch_config_xml(self):
        fetcher = MockSchemaFetcher(
            schemas={"root-module": ROOT_YANG, "dep-module": DEP_YANG},
            config_xml=ROOT_XML,
        )
        NetconfConfig.from_device(fetcher, xpath="/root-module:*")
        assert len(fetcher.fetch_config_xml_calls) == 1
        xpath, xmlns, datastore = fetcher.fetch_config_xml_calls[0]
        assert xpath == "/root-module:*"
        assert xmlns == {"root-module": "urn:test:root"}
        assert datastore == "running"

    def test_xpath_unknown_prefix_raises(self):
        fetcher = MockSchemaFetcher(
            schemas={"root-module": ROOT_YANG, "dep-module": DEP_YANG},
            config_xml=ROOT_XML,
        )
        with pytest.raises(NetconfMgmtConfigError, match="unknown-module"):
            NetconfConfig.from_device(fetcher, xpath="/unknown-module:*")

    def test_xpath_path_missing_dep_raises(self):
        fetcher = MockSchemaFetcher(
            schemas={"root-module": ROOT_YANG},  # dep-module not available
            config_xml=ROOT_XML,
        )
        with pytest.raises(NetconfMgmtConfigError):
            NetconfConfig.from_device(fetcher, xpath="/root-module:*")

    def test_no_xpath_fetches_needed_schemas_with_deps(self):
        fetcher = MockSchemaFetcher(
            schemas={"root-module": ROOT_YANG, "dep-module": DEP_YANG},
            config_xml=ROOT_XML,
        )
        config = NetconfConfig.from_device(fetcher)
        # root-module is needed (its namespace urn:test:root appears in ROOT_XML).
        # dep-module is fetched as a transitive dependency of root-module.
        assert "root-module" in config._dep_yang
        assert "dep-module" in config._dep_yang

    def test_no_xpath_no_filter(self):
        fetcher = MockSchemaFetcher(
            schemas={"root-module": ROOT_YANG, "dep-module": DEP_YANG},
            config_xml=ROOT_XML,
        )
        NetconfConfig.from_device(fetcher)
        xpath, xmlns, _ = fetcher.fetch_config_xml_calls[0]
        assert xpath is None
        assert xmlns == {}

    def test_no_yang_module_path_missing_schema_skipped(self):
        fetcher = MockSchemaFetcher(
            schemas={"root-module": ROOT_YANG, "dep-module": DEP_YANG},
            config_xml=ROOT_XML,
            available=[
                ("root-module", None),
                ("dep-module", None),
                ("missing-module", None),
            ],
        )
        config = NetconfConfig.from_device(fetcher)
        assert "missing-module" not in config._dep_yang

    def test_no_yang_module_path_raises_when_needed_schema_unavailable(self):
        # A module whose namespace appears in the config XML is in needed_modules.
        # If its schema cannot be fetched, from_device() must raise immediately.
        class FailingFetcher:
            def get_schema(self, identifier: str, revision: str | None = None) -> str:
                raise NetconfMgmtConfigError(f"schema unavailable: {identifier}")

            def fetch_config_xml(
                self, xpath: str | None, xmlns: dict[str, str], datastore: str
            ) -> tuple[str, set[str]]:
                return ROOT_XML, {"urn:test:root"}

            def get_module_list(self) -> list[tuple[str, str | None, str | None]]:
                return [("root-module", None, "urn:test:root")]

        with pytest.raises(NetconfMgmtConfigError):
            NetconfConfig.from_device(FailingFetcher())

    def test_from_device_result_as_dict(self):
        fetcher = MockSchemaFetcher(
            schemas={"root-module": ROOT_YANG, "dep-module": DEP_YANG},
            config_xml=ROOT_XML,
        )
        config = NetconfConfig.from_device(fetcher, xpath="/root-module:*")
        result = config.as_dict()
        assert result == {"root-module:settings": {"name": "hello"}}


class TestMutation:
    def test_update_from_dict_changes_value(self):
        config = NetconfConfig(TEST_XML, {"test-module": TEST_YANG})
        d = config.as_dict()
        d["test-module:settings"]["name"] = "changed"
        config.update_from_dict(d)
        assert config.as_dict()["test-module:settings"]["name"] == "changed"

    def test_update_from_dict_raises_on_unknown_key(self):
        config = NetconfConfig(TEST_XML, {"test-module": TEST_YANG})
        with pytest.raises(NetconfMgmtConfigError):
            config.update_from_dict({"test-module:settings": {"nonexistent": "x"}})

    def test_set_changes_leaf_value(self):
        config = NetconfConfig(TEST_XML, {"test-module": TEST_YANG})
        config.set("/test-module:settings/name", "changed")
        assert config.as_dict()["test-module:settings"]["name"] == "changed"

    def test_set_raises_on_invalid_xpath(self):
        config = NetconfConfig(TEST_XML, {"test-module": TEST_YANG})
        with pytest.raises(NetconfMgmtConfigError):
            config.set("/test-module:settings/nonexistent", "x")

    def test_get_returns_string_value(self):
        config = NetconfConfig(TEST_XML, {"test-module": TEST_YANG})
        result = config.get("/test-module:settings/name")
        assert result == "hello"

    def test_get_returns_int_value(self):
        config = NetconfConfig(TEST_XML, {"test-module": TEST_YANG})
        result = config.get("/test-module:settings/count")
        assert result == 5

    def test_get_raises_on_nonexistent_path(self):
        config = NetconfConfig(TEST_XML, {"test-module": TEST_YANG})
        with pytest.raises(NetconfMgmtConfigError):
            config.get("/test-module:settings/nonexistent")

    def test_get_raises_on_container_path(self):
        config = NetconfConfig(TEST_XML, {"test-module": TEST_YANG})
        with pytest.raises(NetconfMgmtConfigError):
            config.get("/test-module:settings")

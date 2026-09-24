"""Load NETCONF config from a local XML file and save it to a device."""

import pathlib
import pprint

from rr_netconf_mgmt.config import NetconfConfig

EXAMPLES_DIR = pathlib.Path(__file__).parent

xml = (EXAMPLES_DIR / "mbm_station_name.xml").read_text()
yang = (EXAMPLES_DIR / "mbm-root@2023-04-29.yang").read_text()

config = NetconfConfig(xml, {"mbm-root": yang})
pprint.pprint(config.as_dict())

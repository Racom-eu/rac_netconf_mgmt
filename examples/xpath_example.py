"""Fetch a filtered config subtree using XPath and set a leaf value by data path."""

import pprint

from rr_netconf_mgmt.device import NetconfDevice

HOST = "192.168.169.169"
USER = "admin"
PASSWORD = "Admin123!"

with NetconfDevice(host=HOST, user=USER, password=PASSWORD, hostkey_verify=False) as device:
    # Fetch only the Main subtree using an XPath filter.
    config = device.get_config(xpath="/mbm-root:config_data/main/Main")

    pprint.pprint(config.as_dict())

    # Read a specific leaf value by its data path.
    name = config.get("/mbm-root:config_data/main/Main/RR_StationName")
    print(f"Current station name: {name}")

    # Set a specific leaf by its data path without converting to dict.
    config.set("/mbm-root:config_data/main/Main/RR_StationName", "MyStation")

    device.set_config(config)
    print("Config saved to device.")

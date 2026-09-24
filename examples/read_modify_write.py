"""Read full config from device, modify a value in dict, and write back."""

import pprint

from rr_netconf_mgmt.device import NetconfDevice

HOST = "192.168.169.169"
USER = "admin"
PASSWORD = "Admin123!"

with NetconfDevice(host=HOST, user=USER, password=PASSWORD, hostkey_verify=False) as device:
    config = device.get_config(xpath="/mbm-root:*")

    data = config.as_dict()
    pprint.pprint(data)

    data["mbm-root:config_data"]["main"]["Main"]["RR_StationName"] = "NewStationName"
    config.update_from_dict(data)

    device.set_config(config)
    print("Config saved to device.")

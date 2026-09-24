"""Demonstrate exception handling for connection, config, and operation errors."""

import pprint

from rr_netconf_mgmt.device import NetconfDevice
from rr_netconf_mgmt.exceptions import (
    NetconfMgmtConfigError,
    NetconfMgmtConnectionError,
    NetconfMgmtError,
    NetconfMgmtOperationError,
)

HOST = "192.168.169.169"
USER = "admin"
PASSWORD = "Admin123!"

try:
    with NetconfDevice(host=HOST, user=USER, password=PASSWORD, hostkey_verify=False) as device:

        # NetconfMgmtOperationError — device does not support NETCONF Monitoring,
        # or the get-module-list RPC fails.
        try:
            config = device.get_config(xpath="/mbm-root:*")
        except NetconfMgmtOperationError as e:
            print(f"Failed to retrieve module list from device: {e}")
            raise

        # NetconfMgmtConfigError — XPath prefix not advertised by the device.
        try:
            config = device.get_config(xpath="/unknown-module:*")
        except NetconfMgmtConfigError as e:
            print(f"Config error: {e}")

        # NetconfMgmtConfigError — YANG schema missing, as_dict() cannot parse XML.
        try:
            pprint.pprint(config.as_dict())
        except NetconfMgmtConfigError as e:
            print(f"Failed to parse config: {e}")

        # NetconfMgmtConfigError — invalid XPath in set(), leaf does not exist.
        try:
            config.set("/mbm-root:config_data/main/Main/NonExistentLeaf", "value")
        except NetconfMgmtConfigError as e:
            print(f"Failed to set value: {e}")

        # NetconfMgmtConfigError — edit-config RPC rejected by device.
        try:
            device.set_config(config)
        except NetconfMgmtConfigError as e:
            print(f"Failed to write config to device: {e}")

except NetconfMgmtConnectionError as e:
    # Raised by connect() or during any RPC if the transport is lost.
    print(f"Connection failed: {e}")

except NetconfMgmtError as e:
    # Base class — catches any unhandled rr_netconf_mgmt exception.
    print(f"Unexpected error: {e}")

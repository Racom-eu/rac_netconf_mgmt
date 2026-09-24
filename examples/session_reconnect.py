"""Recover from closed session."""

import time

from rr_netconf_mgmt.device import NetconfDevice
from rr_netconf_mgmt.exceptions import NetconfMgmtSessionClosedError

HOST = "192.168.169.169"
USER = "admin"
PASSWORD = "Admin123!"
POLL_INTERVAL = 600



with NetconfDevice(host=HOST, user=USER, password=PASSWORD, hostkey_verify=False) as device:
    while True:
        try:
            config = device.get_config(xpath="/mbm-root:config_data/main/Main")
            name = config.get("/mbm-root:config_data/main/Main/RR_StationName")
            print(f"Station name: {name}")
        except NetconfMgmtSessionClosedError as e:
            # Server idle timeout closed our session — detected immediately on the
            # next RPC via Session.is_alive() (the libssh transport otherwise leaves
            # the connection flag set and the call would hang until timeout).
            print(f"Session closed by server: {e}; reconnecting...")
            device.reconnect()
            continue

        time.sleep(POLL_INTERVAL)

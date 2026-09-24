"""Exception hierarchy for rr_netconf_mgmt — connection, config, and operation errors."""


class NetconfMgmtError(Exception):
    pass


class NetconfMgmtConnectionError(NetconfMgmtError):
    pass


class NetconfMgmtSessionClosedError(NetconfMgmtConnectionError):
    pass


class NetconfMgmtConfigError(NetconfMgmtError):
    pass


class NetconfMgmtOperationError(NetconfMgmtError):
    pass

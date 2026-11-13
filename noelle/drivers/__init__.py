"""Device drivers. Each driver module registers a factory with the engine."""
from __future__ import annotations


def register_all(engine, project=None, historian=None):
    from . import datahub, local, modbus, opcda, opcua, s7

    engine.register_factory("modbus", modbus.ModbusDriver)
    engine.register_factory("s7", s7.S7Driver)
    engine.register_factory("opcua", opcua.OpcUaDriver)
    engine.register_factory("opcda", opcda.OpcDaDriver)
    engine.register_factory("datahub", datahub.DataHubDriver)
    engine.register_factory("sim", local.SimDriver)
    engine.register_factory("mem", lambda e, n, c: local.MemDriver(e, n, c, project))
    engine.register_factory("sys", local.SysDriver)
    if historian is not None:
        engine.register_factory("hist", lambda e, n, c: historian.make_driver(e, n, c))

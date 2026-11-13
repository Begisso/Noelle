"""Tag reference parsing (direct addressing).

A *reference* is the string used inside ``{{ ... }}`` in displays, e.g.::

    sim:Sine(0,100,30)                    simulator signal
    mem:Setpoint                          local memory tag
    sys:Time                              system tag
    modbus:PLC1/40001:F                   device from project.nll + address
    modbus:[192.168.1.10:502,1]40001:F    direct addressing (no config needed)
    s7:PLC2/DB10.DBD4                     Siemens addressing
    s7:[192.168.0.1,0,1]M10.2
    opcua:Local/ns=2;s=Channel1.Tag1
    opcua:[opc.tcp://localhost:4840]ns=2;i=1001
    opcda:DataHub/default:Point           OPC DA (Cogent DataHub, ICONICS, Kepware...)
    opcda:[Cogent.DataHub.1]default:Point
    dh:[127.0.0.1:4502]default:Point      Cogent DataHub DHTP (TCP API)
    hist:<description>                    last value from the HTTP historian
    Tank1.Level                           tag database name (tags.csv)

Aliases ``<#Name#>`` are resolved before a reference reaches the server.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

DRIVER_ALIASES = {
    "mb": "modbus", "modbus": "modbus",
    "s7": "s7", "siemens": "s7",
    "ua": "opcua", "opcua": "opcua",
    "da": "opcda", "opcda": "opcda",
    "dh": "datahub", "datahub": "datahub",
    "sim": "sim", "mem": "mem", "sys": "sys", "hist": "hist", "tag": "tag",
}

DEVICE_DRIVERS = {"modbus", "s7", "opcua", "opcda", "datahub"}

_PREFIX = re.compile(r"^([A-Za-z][A-Za-z0-9]*):(?!//)")


@dataclass(frozen=True)
class Ref:
    driver: str          # canonical driver name
    device: str          # device name, or "[direct params]" for direct addressing
    address: str         # driver specific address
    raw: str             # canonical full reference string

    @property
    def direct(self) -> bool:
        return self.device.startswith("[")

    @property
    def direct_params(self) -> str:
        return self.device[1:-1] if self.direct else ""


class RefError(ValueError):
    pass


def parse_ref(ref: str) -> Ref:
    s = ref.strip()
    if not s:
        raise RefError("empty reference")
    m = _PREFIX.match(s)
    if not m or m.group(1).lower() not in DRIVER_ALIASES:
        return Ref("tag", "", s, s)
    drv = DRIVER_ALIASES[m.group(1).lower()]
    rest = s[m.end():]
    if drv not in DEVICE_DRIVERS:
        if drv == "mem" and "=" in rest:          # mem:Name=initial -> key mem:Name
            return Ref(drv, "", rest, f"mem:{rest.split('=', 1)[0].strip()}")
        return Ref(drv, "", rest, f"{drv}:{rest}")
    if rest.startswith("["):
        end = rest.find("]")
        if end < 0:
            raise RefError(f"unterminated '[' in {ref!r}")
        dev, addr = rest[: end + 1], rest[end + 1:]
        addr = addr.lstrip("/")
    else:
        if "/" not in rest:
            raise RefError(f"expected <device>/<address> in {ref!r}")
        dev, addr = rest.split("/", 1)
    if not addr:
        raise RefError(f"missing address in {ref!r}")
    return Ref(drv, dev, addr, f"{drv}:{dev}{'' if dev.startswith('[') else '/'}{addr}")


ALIAS_RE = re.compile(r"<#([^#<>]+)#>")


def resolve_aliases(text: str, *scopes: dict, max_depth: int = 8) -> str:
    """Replace ``<#Name#>`` using the first scope that defines Name (aliases may nest)."""
    for _ in range(max_depth):
        changed = False

        def sub(m):
            nonlocal changed
            name = m.group(1)
            for sc in scopes:
                if sc and name in sc:
                    changed = True
                    return str(sc[name])
            return m.group(0)

        text = ALIAS_RE.sub(sub, text)
        if not changed:
            break
    return text

"""Weintek EasyBuilder Pro importer: Address Tag Library CSV -> tags.csv.

Export it in EasyBuilder Pro: Library -> Address Tag Library -> Export CSV (UTF-8).
Screens (.emtp/.cmtp/.ecmp) are a closed binary format and are not converted.

Address mapping
    Local HMI  LW-100 / LB-5 / LW_Bit-10.3 / RW-0  -> mem:LW100, mem:LB5, ...
    Modbus     0x-1 1x-1 3x-1 4x-1 (and 4x_Bit-1.3, 3x_Double...) -> modbus:<device>/40001 ...
    Siemens    DB-1.4 / DB1.DBW4 / MW-10 / M-10.2 / I-0.0 / Q-0.1  -> s7:<device>/DB1.DBW4 ...
Data types: 16-bit Signed/Unsigned, 32-bit Signed/Unsigned/Float, 64-bit Float, Bit, String
"""
from __future__ import annotations

import os
import re

from ..project import Project, read_csv

TYPE_MB = {"16-bit signed": "I", "16-bit unsigned": "U", "32-bit signed": "DI", "32-bit unsigned": "DU",
           "32-bit float": "F", "64-bit float": "D", "16-bit bcd": "U", "32-bit bcd": "DU"}
TYPE_S7 = {"16-bit signed": ("W", "INT"), "16-bit unsigned": ("W", "WORD"), "32-bit signed": ("D", "DINT"),
           "32-bit unsigned": ("D", "DWORD"), "32-bit float": ("D", "REAL"), "64-bit float": ("D", "LREAL"), "8-bit": ("B", "BYTE")}


def _col(row: dict, *names):
    low = {k.lower().strip(): v for k, v in row.items()}
    for n in names:
        for k, v in low.items():
            if k == n or k.replace(" ", "") == n.replace(" ", ""):
                return (v or "").strip()
    for n in names:
        for k, v in low.items():
            if n in k:
                return (v or "").strip()
    return ""


def _dtype(t: str) -> str:
    t = (t or "").lower().replace("_", "-")
    if "float" in t and "64" in t:
        return "64-bit float"
    if "float" in t:
        return "32-bit float"
    if "bit" == t.strip() or t.startswith("bit"):
        return "bit"
    if "string" in t or "ascii" in t:
        return "string"
    if "32" in t:
        return "32-bit unsigned" if "unsign" in t else "32-bit signed"
    if "8" in t and "16" not in t:
        return "8-bit"
    if "16" in t:
        return "16-bit unsigned" if "unsign" in t else "16-bit signed"
    return t


def convert_address(device_kind: str, dev: str, addr_type: str, addr: str, dtype: str, warn: list) -> str:
    a = (addr or "").strip().replace(" ", "")
    kind = (device_kind or "").lower()
    full = f"{addr_type}-{a}" if addr_type and not a.lower().startswith(addr_type.lower()) else a
    full = full.replace("_bit", "_Bit")
    # ---- Local HMI internal memory
    m = re.fullmatch(r"(LW|LB|RW|RB|LW_Bit|RW_Bit)[-_]?(\d+)(?:\.(\d+))?", full, re.I)
    if m or "local hmi" in kind:
        if m:
            return f"mem:{m.group(1).upper().replace('_BIT', 'B')}{m.group(2)}" + (f"_{m.group(3)}" if m.group(3) else "")
        return f"mem:{re.sub(r'[^A-Za-z0-9_]', '_', full)}"
    # ---- Modbus
    m = re.fullmatch(r"([0134])x(?:_Bit|_Double|_DW|_bit)?[-_](\d+)(?:\.(\d+))?(?:#\d+)?", full, re.I)
    if m:
        table, idx, bit = m.group(1), int(m.group(2)), m.group(3)
        addr5 = f"{table}{idx:05d}" if idx > 9999 else f"{table}{idx:04d}"
        opt = ""
        if bit is not None and table in "34":
            opt = f".{bit}"
        elif table in "34":
            t = TYPE_MB.get(_dtype(dtype))
            if t and t != "I":
                opt = f":{t}"
        return f"modbus:{dev}/{addr5}{opt}"
    # ---- Siemens
    m = re.fullmatch(r"DB[-_]?(\d+)[.\-](\d+)(?:\.(\d+))?", full, re.I)
    if m:
        db, off, bit = m.group(1), m.group(2), m.group(3)
        dt = _dtype(dtype)
        if bit is not None or dt == "bit":
            return f"s7:{dev}/DB{db}.DBX{off}.{bit or 0}"
        k, t = TYPE_S7.get(dt, ("W", "INT"))
        return f"s7:{dev}/DB{db}.DB{k}{off}" + (f":{t}" if t not in ("INT", "REAL") else "")
    m = re.fullmatch(r"(M|I|Q|E|A)(B|W|D)?[-_]?(\d+)(?:\.(\d+))?", full, re.I)
    if m and ("siemens" in kind or "s7" in kind or m.group(4) is not None or m.group(2)):
        area, size, off, bit = m.group(1).upper(), (m.group(2) or "").upper(), m.group(3), m.group(4)
        if bit is not None:
            return f"s7:{dev}/{area}{off}.{bit}"
        if not size:
            size = TYPE_S7.get(_dtype(dtype), ("W", "INT"))[0]
        return f"s7:{dev}/{area}{size}{off}"
    if re.fullmatch(r"DB\d+\.DB[XBWD]\d+(\.\d)?", full, re.I) or re.fullmatch(r"[MIQEA][BWD]?\d+(\.\d)?", full, re.I):
        return f"s7:{dev}/{full.upper()}"
    warn.append(f"unrecognised Weintek address '{addr_type} {addr}' ({device_kind}) - kept as memory tag")
    return f"mem:{re.sub(r'[^A-Za-z0-9_]', '_', full)}"


def import_weintek(project: Project, params: dict) -> dict:
    path = (params.get("file") or "").strip().strip('"')
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    prefix = params.get("prefix", "")
    devmap = {}
    for part in (params.get("devicemap") or "").split(";"):
        if "=" in part:
            a, b = part.split("=", 1)
            devmap[a.strip().lower()] = b.strip()
    rows = read_csv(path)
    warn: list[str] = []
    out = []
    devices = set()
    for r in rows:
        name = _col(r, "name", "tag name", "address tag name", "label")
        if not name:
            continue
        device = _col(r, "device", "plc", "device name", "plc name")
        dkind = _col(r, "device type", "plc type", "driver") or device
        addr_type = _col(r, "address type", "type of address", "register type")
        addr = _col(r, "address", "plc address", "device address")
        dtype = _col(r, "data type", "format", "datatype")
        comment = _col(r, "comment", "description")
        gfdev = devmap.get(device.lower()) or re.sub(r"[^\w]+", "_", device or "Local_HMI").strip("_")
        if not ("local hmi" in (device + dkind).lower()):
            devices.add(gfdev)
        ref = convert_address(dkind, gfdev, addr_type, addr, dtype, warn)
        out.append({"Name": f"{prefix}{name}", "Address": ref, "Description": comment or f"Weintek {device} {addr_type} {addr}",
                    "Group": f"Weintek/{device}" if device else "Weintek"})
    existing = project.tag_rows()
    idx = {r["Name"]: i for i, r in enumerate(existing)}
    for r in out:
        if r["Name"] in idx:
            existing[idx[r["Name"]]].update(r)
        else:
            existing.append(r)
    project.save_tag_rows(existing)
    known = {d["name"] for d in project.cfg.get("devices", [])}
    for d in sorted(devices - known):
        warn.append(f"device '{d}' is referenced by imported tags but not defined in project.nll (Tags & devices -> Devices)")
    return {"summary": f"Weintek: {len(out)} tags imported into tags.csv", "files": ["tags.csv"], "warnings": warn[:500]}

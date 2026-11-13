"""Siemens WinCC (TIA Portal) importer via Openness / SimaticML.

import_tia(project, {"project": ".apXX", "version": "V17", "device": "", "plc": "conn=device;...", "folder": ...})
    copies the TIA project to a temp folder (the original is never opened), runs
    tia_export.ps1 (Openness, no UI) and converts the export.
import_simatic_folder(project, {"path": export folder, ...})
    converts an existing export.

Addressing
----------
HMI tags reference PLC tags. Noelle talks S7 with ABSOLUTE addresses, so:
* PLC tags with a logical address (%I0.0, %MW10, %DB5.DBD4) -> s7:<device>/<address>
* members of *standard* (non-optimized) DBs -> offsets are computed -> s7:<device>/DBn.DBXx.y
* members of *optimized* DBs / symbolic-only access -> NO absolute address exists.
  These are imported as OPC UA references to the S7-1500 OPC UA server
  (opcua:<device>_UA/ns=3;s="DB"."Member") and listed as WARNINGS. Fix either by
  enabling the PLC OPC UA server, or by switching the DB to standard access
  ("Optimized block access" off) and re-importing.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from ..project import Project

HERE = os.path.dirname(os.path.abspath(__file__))
SIZES = {"bool": 0.125, "byte": 1, "char": 1, "sint": 1, "usint": 1, "word": 2, "int": 2, "uint": 2, "date": 2, "s5time": 2,
         "dword": 4, "dint": 4, "udint": 4, "real": 4, "time": 4, "time_of_day": 4, "tod": 4, "lreal": 8, "lint": 8, "ulint": 8,
         "lword": 8, "date_and_time": 8, "dt": 8, "ltime": 8, "ltod": 8, "ldt": 8, "wchar": 2, "dtl": 12,
         # S7-1200/1500 system data types (hardware / connection / block identifiers)
         "hw_any": 2, "hw_device": 2, "hw_dpmaster": 2, "hw_dpslave": 2, "hw_io": 2, "hw_iosystem": 2, "hw_submodule": 2,
         "hw_module": 2, "hw_interface": 2, "hw_ieport": 2, "hw_hsc": 2, "hw_pwm": 2, "hw_pto": 2, "hw_nr": 2,
         "conn_any": 2, "conn_prg": 2, "conn_ouc": 2, "conn_r_id": 4, "db_any": 2, "db_www": 2, "db_dyn": 2,
         "ob_any": 2, "ob_delay": 2, "ob_tod": 2, "ob_cyclic": 2, "ob_att": 2, "ob_pcycle": 2, "ob_hwint": 2, "ob_diag": 2,
         "ob_timeerror": 2, "ob_startup": 2, "port": 2, "rtm": 2, "pip": 2, "event_any": 4, "event_att": 4, "event_hwint": 4,
         # IEC timer/counter instances (documented sizes for S7-1200/1500 standard access; flagged as assumption)
         "iec_timer": 16, "ton_time": 16, "tof_time": 16, "tp_time": 16, "tonr_time": 16,
         "iec_counter": 6, "ctu_int": 6, "ctd_int": 6, "ctud_int": 6}
ASSUMED = {"iec_timer", "ton_time", "tof_time", "tp_time", "tonr_time", "iec_counter", "ctu_int", "ctd_int", "ctud_int"}
S7TYPE = {"bool": None, "byte": "BYTE", "char": "CHAR", "sint": "SINT", "usint": "USINT", "word": "WORD", "int": "INT", "uint": "UINT",
          "dword": "DWORD", "dint": "DINT", "udint": "UDINT", "real": "REAL", "time": "TIME", "lreal": "LREAL"}


def local(tag):
    return tag.split("}", 1)[-1]


def attrs(el) -> dict:
    """AttributeList + LinkList/*/Name of a SimaticML element as a flat dict."""
    out = {}
    for child in el:
        n = local(child.tag)
        if n == "AttributeList":
            for a in child:
                txt = (a.text or "").strip()
                if len(a):
                    txt = txt or " ".join((x.text or "").strip() for x in a.iter() if x is not a and (x.text or "").strip())
                out[local(a.tag)] = txt
        elif n == "LinkList":
            for a in child:
                nm = a.find(".//{*}Name") if a.find(".//{*}Name") is not None else a.find("Name")
                out["@" + local(a.tag)] = (nm.text or "").strip() if nm is not None else (a.text or "").strip()
    return out


def rgb(s, default="none"):
    if not s:
        return default
    m = re.findall(r"\d+", s)
    if len(m) >= 3:
        r, g, b = (int(x) for x in m[:3])
        a = int(m[3]) if len(m) > 3 else 255
        return f"#{r:02x}{g:02x}{b:02x}" if a >= 255 else f"rgba({r},{g},{b},{a / 255:.2f})"
    return default


def num(s, d=0.0):
    try:
        return float(str(s).replace(",", ".").split()[0])
    except (ValueError, IndexError, TypeError):
        return d


def mtext(el, name=None):
    """MultilingualText -> first non empty text (prefers en-US / ru-RU)."""
    best = ""
    for mt in el.iter():
        if local(mt.tag) != "MultilingualText":
            continue
        if name and mt.get("CompositionName") not in (name, None):
            continue
        for item in mt.iter():
            if local(item.tag) == "MultilingualTextItem":
                a = attrs(item)
                t = re.sub(r"<[^>]+>", "", a.get("Text", ""))
                if t and (not best or a.get("Culture", "").startswith(("en", "ru"))):
                    best = t
        if best:
            return best
    return best


# ----------------------------------------------------------------------------------- PLC model
class PlcModel:
    def __init__(self):
        self.tags = {}      # plc tag name -> (address, datatype)
        self.dbs = {}       # db name -> {"number": n, "layout": "Optimized"/"Standard", "members": {path: (offset_bits, type)}}
        self.udts = {}      # UDT name -> Section element
        self.assumed: set[str] = set()   # DBs whose offsets rely on documented IEC timer/counter sizes

    def load(self, folder):
        for f in glob.glob(os.path.join(folder, "types", "*.xml")):
            try:
                root = ET.parse(f).getroot()
            except ET.ParseError:
                continue
            t = next((e for e in root.iter() if local(e.tag) == "SW.Types.PlcStruct"), None)
            if t is None:
                continue
            name = attrs(t).get("Name")
            sec = next((e for e in t.iter() if local(e.tag) == "Section"), None)
            if name and sec is not None:
                self.udts[name] = sec
        for f in glob.glob(os.path.join(folder, "tags", "*.xml")):
            for el in ET.parse(f).getroot().iter():
                if local(el.tag) == "SW.Tags.PlcTag":
                    a = attrs(el)
                    if a.get("Name"):
                        self.tags[a["Name"]] = (a.get("LogicalAddress", ""), a.get("DataTypeName", ""))
        for f in glob.glob(os.path.join(folder, "db", "*.xml")):
            try:
                self._db(ET.parse(f).getroot())
            except Exception:
                pass

    def _db(self, root):
        blk = next((e for e in root.iter() if local(e.tag) in ("SW.Blocks.GlobalDB", "SW.Blocks.InstanceDB", "SW.Blocks.ArrayDB")), None)
        if blk is None:
            return
        a = attrs(blk)
        name, number = a.get("Name"), int(num(a.get("Number"), 0))
        layout = a.get("MemoryLayout", "Optimized")
        members = {}
        sec = next((e for e in blk.iter() if local(e.tag) == "Section" and e.get("Name") == "Static"), None)
        if sec is not None and layout.lower() == "standard":
            self._cur_db = name
            self._layout(sec, "", [0.0], members)
        self.dbs[name] = {"number": number, "layout": layout, "members": members}

    def _layout(self, parent, prefix, pos, out):
        """S7 standard memory layout: bools packed, everything else byte aligned, >1 byte even aligned, structs even."""
        for m in parent:
            if local(m.tag) != "Member":
                continue
            nm, dt = m.get("Name"), (m.get("Datatype") or "").strip('"')
            path = f"{prefix}{nm}"
            low = dt.lower()
            if low == "struct" or any(local(c.tag) == "Member" for c in m):
                pos[0] = _align(pos[0], 2)
                self._layout(m, path + ".", pos, out)
                pos[0] = _align(pos[0], 2)
                continue
            arr = re.match(r'array\s*\[(\-?\d+)\.\.(\-?\d+)\]\s*of\s*"?([\w ]+)"?', low)
            if arr:
                lo, hi, et = int(arr.group(1)), int(arr.group(2)), arr.group(3)
                pos[0] = _align(pos[0], 2)
                for i in range(lo, hi + 1):
                    sz = SIZES.get(et)
                    if sz is None and m.get("Datatype", "").split(" of ")[-1].strip('"') in self.udts:
                        pos[0] = _align(pos[0], 2)
                        self._layout(self.udts[m.get("Datatype").split(" of ")[-1].strip('"')], f"{path}[{i}].", pos, out)
                        pos[0] = _align(pos[0], 2)
                        continue
                    if sz is None:
                        return
                    if sz >= 1:
                        pos[0] = _align(pos[0], 2 if sz > 1 else 1)
                    out[f"{path}[{i}]"] = (pos[0], et)
                    pos[0] += sz
                pos[0] = _align(pos[0], 2)
                continue
            m_str = re.match(r"string(?:\[(\d+)\])?", low)
            if m_str:
                pos[0] = _align(pos[0], 2)
                out[path] = (pos[0], f"string{int(m_str.group(1) or 254)}")
                pos[0] += int(m_str.group(1) or 254) + 2
                continue
            sz = SIZES.get(low)
            if low in ASSUMED:
                self.assumed.add(getattr(self, "_cur_db", ""))
                pos[0] = _align(pos[0], 2)
                out[path] = (pos[0], low)
                pos[0] += sz
                continue
            if sz is None and dt in self.udts:          # UDT: laid out like a struct
                pos[0] = _align(pos[0], 2)
                self._layout(self.udts[dt], path + ".", pos, out)
                pos[0] = _align(pos[0], 2)
                continue
            if sz is None:      # unknown type (FB instance, missing UDT) -> stop, offsets after it would be wrong
                out[path] = (None, dt)
                return
            if sz >= 1:
                pos[0] = _align(pos[0], 2 if sz > 1 else 1)
            out[path] = (pos[0], low)
            pos[0] += sz

    def resolve(self, plc_tag: str):
        """'"DB_Motor".Speed' / 'DB_Motor.Speed' / 'Start' -> (s7 address | None, reason)"""
        t = plc_tag.strip()
        if t.strip('"') in self.tags:
            addr, dt = self.tags[t.strip('"')]
            if addr:
                return _plc_addr(addr, dt), "plc tag"
        m = re.match(r'"?([^".]+)"?\.(.+)', t)
        if not m:
            return None, "unknown PLC tag"
        db, member = m.group(1), m.group(2).replace('"', "")
        info = self.dbs.get(db)
        if info is None:
            return None, f"DB '{db}' not exported"
        if info["layout"].lower() != "standard":
            return None, f"DB '{db}' uses optimized block access (symbolic only)"
        mm = info["members"].get(member)
        if not mm or mm[0] is None:
            return None, f"member '{member}' offset not computable (UDT?)"
        off, dt = mm
        if dt in ASSUMED:
            return None, f"'{member}' is an IEC timer/counter instance (no single value)"
        byte, bit = int(off), int(round((off - int(off)) * 8))
        n = info["number"]
        why = "standard DB" + (" (offset assumes IEC_TIMER=16/IEC_COUNTER=6 bytes - verify)" if db in self.assumed else "")
        if dt == "bool":
            return f"DB{n}.DBX{byte}.{bit}", why
        if dt.startswith("string"):
            return f"DB{n}.DBB{byte}:STRING{dt[6:]}", why
        size = SIZES[dt]
        kind = {1: "B", 2: "W", 4: "D", 8: "D"}[int(size)]
        t7 = S7TYPE.get(dt)
        return f"DB{n}.DB{kind}{byte}" + (f":{t7}" if t7 and t7 not in ("INT", "REAL") else ""), why


def _align(p, a):
    import math
    return math.ceil(p / a) * a if p % a else p


def _plc_addr(addr, dt):
    a = addr.lstrip("%").upper()
    a = re.sub(r"^DB(\d+)\.DB([XBWD])", r"DB\1.DB\2", a)
    t = S7TYPE.get((dt or "").lower())
    if re.match(r"^[MIQEA]D\d+$|^DB\d+\.DBD\d+$", a) and t and t != "REAL":
        a += f":{t}"
    elif re.match(r"^[MIQEA]W\d+$|^DB\d+\.DBW\d+$", a) and t and t not in ("INT",):
        a += f":{t}"
    return a


# ----------------------------------------------------------------------------------- HMI tags
class HmiTags:
    def __init__(self, plcs: dict, conn_map: dict, warn: list):
        self.plcs, self.conn_map, self.warn = plcs, conn_map, warn
        self.map = {}       # hmi tag name -> gf ref
        self.rows = []
        self.symbolic = []

    def load(self, folder):
        for f in sorted(glob.glob(os.path.join(folder, "tags", "*.xml"))):
            for el in ET.parse(f).getroot().iter():
                if local(el.tag) != "Hmi.Tag.Tag":
                    continue
                a = attrs(el)
                name = a.get("Name")
                if not name:
                    continue
                conn = a.get("@Connection") or a.get("Connection", "")
                plc_tag = a.get("@ControllerTag") or a.get("PlcTag") or a.get("@PlcTag") or ""
                address = a.get("Address", "")
                access = a.get("AccessMode", "")
                dtype = a.get("@DataType") or a.get("DataType", "")
                ref = self._ref(name, conn, plc_tag, address, access, dtype)
                self.map[name] = ref
                self.rows.append({"Name": name, "Address": ref, "Description": f"WinCC {conn} {plc_tag or address}".strip(),
                                  "Group": f"WinCC/{os.path.splitext(os.path.basename(f))[0]}"})

    def _ref(self, name, conn, plc_tag, address, access, dtype):
        if not conn or conn.lower().startswith("<internal") or conn.lower() == "internal tag":
            return f"mem:{_safe(name)}"
        dev = self.conn_map.get(conn, _safe(conn))
        if address and "symbolic" not in access.lower():
            return f"s7:{dev}/{_plc_addr(address, dtype)}"
        for plc in self.plcs.values():
            addr, why = plc.resolve(plc_tag) if plc_tag else (None, "no PLC tag")
            if addr:
                if "verify" in why:
                    self.warn.append(f"CHECK OFFSET: HMI tag '{name}' -> {addr}: {why}")
                return f"s7:{dev}/{addr}"
        why = "symbolic access" + (f" — {why}" if plc_tag else "")
        node = _ua_node(plc_tag or name)
        self.symbolic.append(name)
        self.warn.append(f"SYMBOLIC ADDRESSING: HMI tag '{name}' -> PLC tag '{plc_tag}' has no absolute address ({why}). "
                         f"Imported as OPC UA {node} on device '{dev}_UA' — enable the S7-1500 OPC UA server, "
                         f"or turn off 'Optimized block access' for the DB and re-import.")
        return f"opcua:{dev}_UA/{node}"


def _ua_node(plc_tag):
    parts = [p.strip('"') for p in re.split(r'\.(?=(?:[^"]*"[^"]*")*[^"]*$)', plc_tag)]
    return "ns=3;s=" + ".".join(f'"{p}"' if i == 0 or not re.match(r"^\w+$", p) else p for i, p in enumerate(parts))


def _safe(s):
    return re.sub(r"[^\w.]+", "_", s).strip("_")


# ----------------------------------------------------------------------------------- screens
class ScreenConverter:
    def __init__(self, tags: HmiTags, folder: str, warn: list):
        self.tags, self.folder, self.warn = tags, folder, warn
        self.n = 0
        self.func = {"bindings": 0, "converted": 0, "approximated": 0, "unsupported": 0, "symbolic": 0}

    def ref(self, hmi_tag):
        r = self.tags.map.get(hmi_tag)
        if r is None:
            r = hmi_tag
        if r.startswith("opcua:"):
            self.func["symbolic"] += 1
        return r

    def convert(self, path) -> dict:
        root = ET.parse(path).getroot()
        scr = next((e for e in root.iter() if local(e.tag) in ("Hmi.Screen.Screen", "Hmi.Screen.ScreenTemplate")), None)
        if scr is None:
            raise ValueError("no Hmi.Screen.Screen in file")
        a = attrs(scr)
        W, H = int(num(a.get("Width"), 800)), int(num(a.get("Height"), 480))
        objs = []
        for el in scr.iter():
            t = local(el.tag)
            if t.startswith("Hmi.Screen.") and t not in ("Hmi.Screen.Screen", "Hmi.Screen.ScreenLayer", "Hmi.Screen.ScreenTemplate", "Hmi.Screen.Point"):
                o = self.obj(el, t[len("Hmi.Screen."):])
                if o:
                    objs.append(o)
        doc = {"format": "noelle-display", "version": 1, "name": a.get("Name", os.path.basename(path)), "size": [W, H],
               "background": rgb(a.get("BackColor"), "#b6b6b6"), "aliases": {},
               "source": {"importer": "wincc-openness", "file": os.path.basename(path), "template": a.get("@Template")}, "objects": objs}
        return doc

    def obj(self, el, kind):
        a = attrs(el)
        self.n += 1
        x, y, w, h = num(a.get("Left")), num(a.get("Top")), max(1, num(a.get("Width"), 20)), max(1, num(a.get("Height"), 20))
        oid = _safe(a.get("Name") or a.get("ObjectName") or f"{kind}_{self.n}") or f"o{self.n}"
        base = {"id": f"{oid}_{self.n}", "x": x, "y": y, "w": w, "h": h}
        fill = rgb(a.get("BackColor"), "none") if a.get("BackFillStyle", "Solid").lower() != "transparent" else "none"
        stroke = rgb(a.get("BorderColor") or a.get("ForeColor"), "#000000")
        sw = num(a.get("BorderWidth"), 1)
        font = a.get("Font", "")
        fs = num(re.search(r"(\d+(?:[.,]\d+)?)\s*px", font).group(1)) if re.search(r"(\d+(?:[.,]\d+)?)\s*px", font) else 13
        fam = font.split(",")[0].strip() or "Tahoma"
        bold = "bold" in font.lower()
        txtcol = rgb(a.get("ForeColor"), "#000000")
        align = {"left": "left", "center": "center", "right": "right"}.get(a.get("HorizontalTextAlignment", "").lower(), "left")
        r = None
        if kind == "Rectangle":
            r = {**base, "type": "rect", "fill": fill, "stroke": stroke, "sw": sw, "rx": num(a.get("CornerRadius"), 0)}
        elif kind in ("Ellipse", "Circle"):
            if kind == "Circle" and a.get("Radius"):
                rad = num(a.get("Radius"))
                base.update(w=2 * rad, h=2 * rad)
            r = {**base, "type": "ellipse", "fill": fill, "stroke": stroke, "sw": sw}
        elif kind in ("Line",):
            sx, sy = num(a.get("StartLeft"), x), num(a.get("StartTop"), y)
            ex, ey = num(a.get("EndLeft"), x + w), num(a.get("EndTop"), y + h)
            r = {"id": base["id"], "type": "line", "points": [[sx, sy], [ex, ey]], "stroke": rgb(a.get("LineColor") or a.get("ForeColor"), "#000"), "sw": num(a.get("LineWidth"), 1)}
        elif kind in ("Polyline", "Polygon"):
            pts = [[num(attrs(p).get("X")), num(attrs(p).get("Y"))] for p in el.iter() if local(p.tag) == "Hmi.Screen.Point"]
            if len(pts) < 2:
                pts = [[x, y], [x + w, y + h]]
            r = {"id": base["id"], "type": kind.lower(), "points": pts, "stroke": rgb(a.get("LineColor") or a.get("BorderColor"), "#000"),
                 "sw": num(a.get("LineWidth") or a.get("BorderWidth"), 1), "fill": fill if kind == "Polygon" else "none"}
        elif kind in ("TextField",):
            r = {**base, "type": "text", "text": mtext(el, "Text") or a.get("Text", ""), "fs": fs, "font": fam, "fw": bold,
                 "color": txtcol, "align": align, "valign": "middle", "fill": fill}
        elif kind in ("IOField", "SymbolicIOField", "DateTimeField"):
            mode = a.get("Mode", "Output").lower()
            r = {**base, "type": "input" if "input" in mode else "value", "fs": fs, "font": fam, "fw": bold, "color": txtcol,
                 "align": align or "right", "valign": "middle", "fill": fill, "stroke": stroke, "fmt": _fmt(a.get("FormatPattern"))}
            if kind == "SymbolicIOField":
                self.note("approximated", a.get("Name"), "symbolic IO field -> value (text list not converted)")
        elif kind in ("Button",):
            r = {**base, "type": "button", "text": mtext(el, "TextOff") or mtext(el) or a.get("Name", ""), "fs": fs, "font": fam, "fw": bold,
                 "color": txtcol, "fill": rgb(a.get("BackColor"), "#d4d4d4"), "stroke": stroke, "rx": num(a.get("CornerRadius"), 3), "align": "center", "valign": "middle"}
        elif kind in ("Bar",):
            r = {**base, "type": "bar", "min": num(a.get("MinimumValue"), 0), "max": num(a.get("MaximumValue"), 100), "barColor": rgb(a.get("BarColor") or a.get("ForeColor"), "#3fa7ff"),
                 "fill": fill if fill != "none" else "#d4d4d4", "stroke": stroke, "dir": "up" if h >= w else "right"}
        elif kind in ("Gauge",):
            r = {**base, "type": "gauge", "min": num(a.get("MinimumValue"), 0), "max": num(a.get("MaximumValue"), 100), "fill": "#f0f0f0", "stroke": "#444", "color": "#222", "needle": "#c00"}
        elif kind in ("Slider",):
            r = {**base, "type": "slider", "min": num(a.get("MinimumValue"), 0), "max": num(a.get("MaximumValue"), 100), "dir": "vertical" if h > w else "horizontal"}
        elif kind in ("Switch",):
            r = {**base, "type": "toggle"}
        elif kind in ("TrendView", "f(t)TrendView"):
            r = {**base, "type": "trend", "pens": [], "span": 300}
            self.note("approximated", a.get("Name"), "trend view (pens must be re-added)")
        elif kind in ("AlarmView",):
            r = {**base, "type": "alarms"}
        elif kind in ("ScreenWindow",):
            r = {**base, "type": "symbol", "src": f"{self.folder}/{_safe(a.get('@Screen') or a.get('Screen') or '')}.nll", "aliases": {}}
        elif kind in ("GraphicView", "GraphicIOField"):
            r = {**base, "type": "rect", "fill": "none", "stroke": "#888", "dash": "4,3", "tooltip": f"graphic '{a.get('@Graphic') or a.get('Graphic', '')}' (export the image into images/)"}
            self.note("approximated", a.get("Name"), "graphic view -> placeholder")
        else:
            self.note("unsupported", a.get("Name"), kind)
            return None
        self.dynamics(el, r, kind)
        return r

    def note(self, k, src, what):
        self.func[k] += 1

    def dynamics(self, el, r, kind):
        for d in el:
            if local(d.tag) != "ObjectList":
                continue
            for dy in d:
                t = local(dy.tag)
                a = attrs(dy)
                tag = a.get("@Tag") or a.get("Tag")
                prop = a.get("PropertyName") or dy.get("CompositionName") or dy.get("AggregationName") or ""
                if t.startswith("Hmi.Dynamic.") and tag:
                    ref = self.ref(tag)
                    self.func["bindings"] += 1
                    expr = "{{" + ref + "}}"
                    pl = prop.lower()
                    if pl in ("processvalue",) or (t.endswith("TagConnectionDynamic") and r["type"] in ("value", "input", "bar", "gauge", "slider", "toggle")):
                        if r["type"] in ("input", "slider", "toggle"):
                            r["tag"] = ref
                        else:
                            r["expr"] = expr
                        self.note("converted", tag, "process value")
                    elif "visib" in pl or "Visibility" in t:
                        r.setdefault("dyn", {})["visible"] = {"expr": expr}
                        self.note("converted", tag, "visibility")
                    elif "backcolor" in pl or "Appearance" in t or "RangeDynamic" in t:
                        stops = []
                        for rng in dy.iter():
                            if local(rng.tag).endswith("Range") or local(rng.tag).endswith("RangeItem"):
                                ra = attrs(rng)
                                c = rgb(ra.get("BackColor") or ra.get("ForeColor"), None)
                                if c:
                                    stops.append([num(ra.get("Range") or ra.get("From") or ra.get("Value"), 0), c])
                        r.setdefault("dyn", {})["fill"] = {"expr": expr, "type": "range", "stops": stops} if stops else {"expr": expr, "type": "bool", "on": "#00c000", "off": r.get("fill", "#d4d4d4")}
                        self.note("converted" if stops else "approximated", tag, "appearance")
                    else:
                        r.setdefault("dyn", {})["text" if r["type"] in ("text", "button") else "blink"] = {"expr": expr}
                        self.note("approximated", tag, f"{t}/{prop}")
                elif t.startswith("Hmi.Event."):
                    acts = self.events(dy)
                    if acts:
                        r.setdefault("actions", []).extend(acts)
                elif t.startswith("Hmi.Dynamic.") and "Script" in t:
                    self.note("unsupported", "script", t)
                    self.warn.append(f"{r['id']}: VB/C script dynamic not converted")

    def events(self, ev):
        on = {"click": "click", "press": "down", "release": "up"}.get((attrs(ev).get("EventType") or ev.get("CompositionName") or "click").lower().replace("event", ""), "click")
        out = []
        for fn in ev.iter():
            t = local(fn.tag)
            if not t.startswith("Hmi.Function") and t not in ("Hmi.Event.FunctionCall",):
                continue
            a = attrs(fn)
            name = a.get("Name") or a.get("FunctionName") or t.split(".")[-1]
            params = [attrs(p) for p in fn.iter() if local(p.tag).endswith("Parameter")]
            vals = [p.get("Value") or p.get("@Tag") or p.get("Tag") or p.get("@Screen") or "" for p in params]
            vals = [v for v in vals if v]
            nl = name.lower()
            if nl in ("activatescreen", "activatescreenbynumber") and vals:
                out.append({"on": on, "type": "open", "display": f"{self.folder}/{_safe(vals[0])}.nll", "target": "main"})
            elif nl in ("setbit",) and vals:
                out.append({"on": on, "type": "write", "tag": self.ref(vals[0]), "value": "1"})
            elif nl in ("resetbit",) and vals:
                out.append({"on": on, "type": "write", "tag": self.ref(vals[0]), "value": "0"})
            elif nl in ("invertbit", "togglebit") and vals:
                out.append({"on": on, "type": "toggle", "tag": self.ref(vals[0])})
            elif nl in ("settag", "setvalue") and len(vals) >= 2:
                out.append({"on": on, "type": "write", "tag": self.ref(vals[0]), "value": vals[1]})
            elif nl in ("increasevalue", "decreasevalue") and vals:
                inc = vals[1] if len(vals) > 1 else "1"
                sign = "+" if nl.startswith("inc") else "-"
                out.append({"on": on, "type": "write", "tag": self.ref(vals[0]), "value": "{{" + self.ref(vals[0]) + "}} " + sign + " " + inc})
            elif nl in ("acknowledgealarm",):
                out.append({"on": on, "type": "ack"})
            else:
                continue
            self.func["converted"] += 1
        return out


def _fmt(p):
    if not p:
        return "0"
    m = re.search(r"[.,](9+)", p)
    return "0." + "0" * len(m.group(1)) if m else "0"


# ----------------------------------------------------------------------------------- entry points
def import_simatic_folder(project: Project, params: dict) -> dict:
    root = (params.get("path") or "").strip().strip('"')
    if not os.path.isdir(root):
        raise FileNotFoundError(root)
    folder = (params.get("folder") or "displays/wincc").strip("/")
    conn_map = {}
    for part in (params.get("plc") or "").split(";"):
        if "=" in part:
            a, b = part.split("=", 1)
            conn_map[a.strip()] = b.strip()
    warn: list[str] = []
    plcs = {}
    for d in sorted(glob.glob(os.path.join(root, "PLC_*"))):
        pm = PlcModel()
        pm.load(d)
        plcs[os.path.basename(d)[4:]] = pm
        opt = [n for n, i in pm.dbs.items() if i["layout"].lower() != "standard"]
        if opt:
            warn.append(f"PLC {os.path.basename(d)[4:]}: {len(opt)} optimized DB(s) (symbolic access only): {', '.join(opt[:15])}{'…' if len(opt) > 15 else ''}")
    hmis = [d for d in sorted(glob.glob(os.path.join(root, "*"))) if os.path.isdir(d) and not os.path.basename(d).startswith("PLC_")]
    created, tag_rows, identity = [], [], {}
    os.makedirs(project.abspath(folder), exist_ok=True)
    for hd in hmis:
        cons = []
        cp = os.path.join(hd, "connections.json")
        if os.path.exists(cp):
            cons = json.load(open(cp, encoding="utf-8-sig"))
        for c in cons:
            name = c.get("Name")
            if name and name not in conn_map:
                conn_map[name] = _safe(c.get("Partner") or name)
        tags = HmiTags(plcs, conn_map, warn)
        tags.load(hd)
        tag_rows += tags.rows
        conv = ScreenConverter(tags, folder, warn)
        for f in sorted(glob.glob(os.path.join(hd, "screens", "*.xml")) + glob.glob(os.path.join(hd, "templates", "*.xml"))):
            try:
                doc = conv.convert(f)
            except Exception as e:
                warn.append(f"{os.path.basename(f)}: {e}")
                continue
            rel = f"{folder}/{_safe(os.path.splitext(os.path.basename(f))[0])}.nll"
            project.write_text(rel, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
            created.append(rel)
        identity[os.path.basename(hd)] = {"functional": dict(conv.func), "tags": len(tags.rows), "symbolic_tags": len(tags.symbolic)}
        # add S7 devices / OPC UA devices referenced by tags
        devs = {d["name"] for d in project.cfg.get("devices", [])}
        for c in cons:
            dn = conn_map.get(c.get("Name"), "")
            if dn and dn not in devs:
                host = c.get("Address") or c.get("HostAddress") or "192.168.0.1"
                project.cfg.setdefault("devices", []).append({"name": dn, "type": "s7", "host": host.split(",")[0], "rack": 0, "slot": 1, "note": f"from WinCC connection {c.get('Name')}"})
                devs.add(dn)
                if tags.symbolic:
                    project.cfg["devices"].append({"name": f"{dn}_UA", "type": "opcua", "endpoint": f"opc.tcp://{host.split(',')[0]}:4840", "note": "S7-1500 OPC UA server for symbolic tags"})
        project.save_cfg()
    if tag_rows:
        existing = project.tag_rows()
        idx = {r["Name"]: i for i, r in enumerate(existing)}
        for r in tag_rows:
            if r["Name"] in idx:
                existing[idx[r["Name"]]].update(r)
            else:
                existing.append(r)
        project.save_tag_rows(existing)
    sym = sum(v["symbolic_tags"] for v in identity.values())
    summary = f"WinCC: {len(created)} screen(s), {len(tag_rows)} tag(s) imported"
    if sym:
        summary += f" — WARNING: {sym} tag(s) use symbolic addressing (see warnings)"
    warn.sort(key=lambda w: (not w.startswith("SYMBOLIC"), w))
    return {"summary": summary, "files": created, "warnings": warn[:1000], "identity": identity}


def import_tia(project: Project, params: dict) -> dict:
    src = (params.get("project") or "").strip().strip('"')
    if not os.path.isfile(src):
        raise FileNotFoundError(src)
    version = params.get("version") or ("V" + src.rsplit(".ap", 1)[-1] if ".ap" in src else "V17")
    work = tempfile.mkdtemp(prefix="gf-tia-")
    copy_dir = os.path.join(work, "project")
    shutil.copytree(os.path.dirname(src), copy_dir, ignore=shutil.ignore_patterns("*.lock", "tmp", "Logs"))
    out = os.path.join(work, "export")
    args = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", os.path.join(HERE, "tia_export.ps1"),
            "-Project", os.path.join(copy_dir, os.path.basename(src)), "-Out", out, "-Version", version]
    if params.get("device"):
        args += ["-Device", params["device"]]
    p = subprocess.run(args, capture_output=True, timeout=3600, creationflags=0x08000000)
    if not os.path.exists(os.path.join(out, "summary.json")):
        raise RuntimeError("Openness export failed: " + p.stderr.decode("cp866", "replace")[-2000:] + p.stdout.decode("utf-8", "replace")[-1000:])
    rep = import_simatic_folder(project, {**params, "path": out})
    keep = os.path.join(project.root, "runtime", "tia_export")
    shutil.rmtree(keep, ignore_errors=True)
    shutil.copytree(out, keep)
    shutil.rmtree(work, ignore_errors=True)
    rep["summary"] += f" (export kept in runtime/tia_export)"
    return rep

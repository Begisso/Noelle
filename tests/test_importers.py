"""Importer unit tests (no external software needed). Run: python -m tests.test_importers"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, ".")

from noelle.importers.gwx import Converter, TagMapper, color  # noqa: E402
from noelle.importers.weintek import convert_address, import_weintek  # noqa: E402
from noelle.project import create_project  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL") + " " + msg)
    if not cond:
        FAILS.append(msg)


def test_weintek():
    print("weintek address mapping")
    w = []
    cases = [
        (("Local HMI", "Local_HMI", "LW", "100", "16-bit Signed"), "mem:LW100"),
        (("Local HMI", "Local_HMI", "", "LB-5", "Bit"), "mem:LB5"),
        (("MODBUS TCP/IP", "PLC1", "4x", "1", "16-bit Signed"), "modbus:PLC1/40001"),
        (("MODBUS TCP/IP", "PLC1", "4x", "10", "32-bit Float"), "modbus:PLC1/40010:F"),
        (("MODBUS TCP/IP", "PLC1", "", "3x-7", "32-bit Unsigned"), "modbus:PLC1/30007:DU"),
        (("MODBUS TCP/IP", "PLC1", "0x", "12", "Bit"), "modbus:PLC1/00012"),
        (("MODBUS TCP/IP", "PLC1", "4x_Bit", "5.3", "Bit"), "modbus:PLC1/40005.3"),
        (("Siemens S7-1200", "S7", "DB", "1.4", "32-bit Float"), "s7:S7/DB1.DBD4"),
        (("Siemens S7-1200", "S7", "DB", "2.10", "16-bit Unsigned"), "s7:S7/DB2.DBW10:WORD"),
        (("Siemens S7-1200", "S7", "", "DB3.DBX4.1", "Bit"), "s7:S7/DB3.DBX4.1"),
        (("Siemens S7-1200", "S7", "M", "10.2", "Bit"), "s7:S7/M10.2"),
        (("Siemens S7-1200", "S7", "MW", "20", "16-bit Signed"), "s7:S7/MW20"),
    ]
    for args, exp in cases:
        got = convert_address(*args, w)
        check(got == exp, f"{args[2] or ''}{args[3]} -> {got} (expected {exp})")
    tmp = tempfile.mkdtemp()
    try:
        proj = create_project(os.path.join(tmp, "p"))
        csvp = os.path.join(tmp, "eb.csv")
        with open(csvp, "w", encoding="utf-8-sig") as f:
            f.write("Name;Device;Address Type;Address;Data Type;Comment\n")
            f.write("Motor1_Run;MODBUS TCP/IP;0x;1;Bit;motor 1 running\n")
            f.write("Tank_Level;Siemens S7-1200;DB;1.0;32-bit Float;level\n")
            f.write("SP;Local HMI;LW;200;16-bit Signed;setpoint\n")
        rep = import_weintek(proj, {"file": csvp, "devicemap": "MODBUS TCP/IP=PLC1;Siemens S7-1200=S7_1500", "prefix": "WT_"})
        rows = {r["Name"]: r for r in proj.tag_rows()}
        check(rows.get("WT_Motor1_Run", {}).get("Address") == "modbus:PLC1/00001", "weintek CSV import modbus coil")
        check(rows.get("WT_Tank_Level", {}).get("Address") == "s7:S7_1500/DB1.DBD0", "weintek CSV import S7 REAL")
        check(rows.get("WT_SP", {}).get("Address") == "mem:LW200", "weintek CSV import local word")
        check(any("not defined in project.nll" in x for x in rep["warnings"]), "warns about undefined devices")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_gwx_converter():
    print("graphworx converter")
    m = TagMapper("")
    check(m.ref("Cogent.DataHub.1\\default:Tank") == "opcda:[Cogent.DataHub.1]default:Tank", "OPC DA tag mapping")
    check(m.ref("~~Speed~~") == "mem:Speed", "local variable mapping")
    check(m.expr("x={{ICONICS.Simulator.1\\Sine}}*2") == "{{opcda:[ICONICS.Simulator.1]Sine}}*2", "expression mapping")
    check(color(0x020000FF) == "#ff0000", "COLORREF -> #rrggbb")
    dump = {"width": 800, "height": 600, "background": 0x02FFFFFF, "objects": [
        {"_type": "GwxRectangle", "_dims": [10, 20, 100, 50], "fillColor": 0x0200FF00, "isFilled": True, "lineColor": 0, "lineStyle": 0, "lineWidth": 2,
         "_dyn": [{"_type": "GwxHide", "dataSource": "Cogent.DataHub.1\\default:Alarm"}]},
        {"_type": "GwxText", "_dims": [10, 80, 100, 20], "text": "???", "lineColor": 0, "alignment": 2, "Font": {"Name": "Arial", "Size": 10},
         "_dyn": [{"_type": "GwxProcessPoint", "dataSource": "Cogent.DataHub.1\\default:Level", "UseKeypad": True}]},
        {"_type": "GwxButton", "_dims": [10, 120, 80, 30], "text": "Go", "fillColor": 0x02C0C0C0, "lineColor": 0, "Font": {"Name": "Arial", "Size": 9},
         "_dyn": [{"_type": "GwxPick", "pickAction": 0, "NameParameter": "Pumps.gdf"}]},
    ]}
    c = Converter(dump, m, "native", None, "displays/imp")
    doc = c.convert_display("T")
    o = doc["objects"]
    check(doc["size"] == [800, 600] and doc["background"] == "#ffffff", "display size/background")
    check(o[0]["type"] == "rect" and o[0]["fill"] == "#00ff00" and o[0]["dyn"]["visible"]["invert"] is True, "rect + hide dynamic")
    check(o[1]["type"] == "input" and o[1]["tag"] == "opcda:[Cogent.DataHub.1]default:Level" and o[1]["align"] == "right", "process point with data entry -> input")
    check(o[2]["actions"][0] == {"on": "click", "type": "open", "display": "displays/imp/Pumps.nll", "target": "main"}, "pick load display -> open action")
    check(c.func["converted"] == 3 and c.func["unsupported"] == 0, f"functional coverage {c.func['converted']} converted")


if __name__ == "__main__":
    test_weintek()
    test_gwx_converter()


def test_simatic():
    print("wincc simaticml + symbolic addressing")
    from noelle.importers.tia import PlcModel, import_simatic_folder
    import json as _json
    pm = PlcModel()
    pm.load(os.path.join("tests", "fixtures", "simatic", "PLC_PLC_1"))
    check(pm.resolve('"DB_Std".Run')[0] == "DB10.DBX0.0", f"std DB bool offset {pm.resolve(chr(34)+'DB_Std'+chr(34)+'.Run')}")
    check(pm.resolve('"DB_Std".Fault')[0] == "DB10.DBX0.1", "std DB second bool packed")
    check(pm.resolve('"DB_Std".Speed')[0] == "DB10.DBD2", f"std DB REAL aligned to even byte: {pm.resolve(chr(34)+'DB_Std'+chr(34)+'.Speed')}")
    check(pm.resolve('"DB_Std".Count')[0] == "DB10.DBW6", "std DB INT")
    check(pm.resolve('"DB_Std".Mode')[0] == "DB10.DBB8:BYTE", "std DB BYTE")
    check(pm.resolve('"DB_Std".Total')[0] == "DB10.DBD10:DINT", f"std DB DINT even aligned after byte: {pm.resolve(chr(34)+'DB_Std'+chr(34)+'.Total')}")
    check(pm.resolve('"DB_Std".Name')[0] == "DB10.DBB14:STRING10", "std DB STRING")
    check(pm.resolve('"DB_Opt".Level')[0] is None and "optimized" in pm.resolve('"DB_Opt".Level')[1], "optimized DB -> no absolute address")
    check(pm.resolve("StartButton")[0] == "I0.3", "absolute PLC tag")
    # UDT expansion: Flag@0.0, M1 struct@2 (Run 2.0, Speed 4), Pumps[0]@8 (Run 8.0, Speed 10), Pumps[1]@14 (Run 14.0, Speed 16), After@20
    check(pm.resolve('"DB_Udt".M1.Run')[0] == "DB12.DBX2.0", f"UDT member bool {pm.resolve(chr(34)+'DB_Udt'+chr(34)+'.M1.Run')}")
    check(pm.resolve('"DB_Udt".M1.Speed')[0] == "DB12.DBD4", "UDT member real")
    check(pm.resolve('"DB_Udt".Pumps[1].Speed')[0] == "DB12.DBD16", f"array of UDT {pm.resolve(chr(34)+'DB_Udt'+chr(34)+'.Pumps[1].Speed')}")
    check(pm.resolve('"DB_Udt".After')[0] == "DB12.DBW20", f"member after UDT array {pm.resolve(chr(34)+'DB_Udt'+chr(34)+'.After')}")
    tmp = tempfile.mkdtemp()
    try:
        proj = create_project(os.path.join(tmp, "p"))
        rep = import_simatic_folder(proj, {"path": os.path.join("tests", "fixtures", "simatic"), "folder": "displays/wincc"})
        rows = {r["Name"]: r["Address"] for r in proj.tag_rows()}
        check(rows.get("Speed") == "s7:PLC_1/DB10.DBD2", f"HMI tag via std DB: {rows.get('Speed')}")
        check(rows.get("Level", "").startswith("opcua:PLC_1_UA/ns=3;s=\"DB_Opt\""), f"symbolic tag -> OPC UA: {rows.get('Level')}")
        check(rows.get("Start") == "s7:PLC_1/I0.3", "HMI tag via PLC tag table")
        check(rows.get("Fault") == "s7:PLC_1/DB10.DBX0.1", "absolute access HMI tag")
        check(rows.get("Internal_1") == "mem:Internal_1", "internal tag -> memory")
        check(rep["warnings"] and rep["warnings"][0].startswith("SYMBOLIC ADDRESSING") and "'Level'" in rep["warnings"][0], "symbolic addressing warning listed first")
        check("symbolic addressing" in rep["summary"], f"summary warns: {rep['summary']}")
        doc = _json.load(open(os.path.join(proj.root, "displays", "wincc", "Main.nll"), encoding="utf-8"))
        types = [o["type"] for o in doc["objects"]]
        check(types == ["rect", "input", "value", "text"], f"screen objects {types}")
        check(doc["objects"][1]["tag"] == "s7:PLC_1/DB10.DBD2" and doc["objects"][1]["fmt"] == "0.00", "IO field input bound + format")
        check(doc["objects"][0]["dyn"]["visible"]["expr"] == "{{s7:PLC_1/I0.3}}", "visibility dynamic")
        check(doc["objects"][3]["text"] == "Pump speed", "multilingual text")
        devs = {d["name"]: d for d in proj.cfg["devices"]}
        check(devs.get("PLC_1", {}).get("host") == "192.168.0.10" and "PLC_1_UA" in devs, "S7 + OPC UA devices created from connection")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_simatic()


def test_vectorize():
    print("vectorize isolated renders")
    from PIL import Image, ImageDraw
    from noelle.importers.vectorize import vectorize
    tmp = tempfile.mkdtemp()
    try:
        # concave "L" polygon (a convex hull would be wrong)
        im = Image.new("RGBA", (120, 100), (0, 0, 0, 0))
        ImageDraw.Draw(im).polygon([(10, 10), (60, 10), (60, 50), (110, 50), (110, 90), (10, 90)], fill=(80, 80, 80, 255))
        p = os.path.join(tmp, "poly.png"); im.save(p)
        v = vectorize(p, True, 100, 200)
        pts = v["points"]
        near = lambda a, b: abs(a[0] - b[0]) <= 2 and abs(a[1] - b[1]) <= 2
        exp = [(110, 210), (160, 210), (160, 250), (210, 250), (210, 290), (110, 290)]
        check(v["closed"] and 6 <= len(pts) <= 8 and all(any(near(p_, e) for p_ in pts) for e in exp), f"concave polygon corners {pts}")
        # open polyline with 3 segments, width 3
        im = Image.new("RGBA", (200, 120), (0, 0, 0, 0))
        ImageDraw.Draw(im).line([(10, 10), (100, 10), (100, 100), (190, 100)], fill=(0, 0, 0, 255), width=3)
        p = os.path.join(tmp, "line.png"); im.save(p)
        v = vectorize(p, False, 0, 0)
        pts = v["points"]
        check(not v["closed"] and 4 <= len(pts) <= 6 and near(pts[0], (10, 10)) or near(pts[-1], (10, 10)), f"polyline path {pts}")
        check(any(near(q, (100, 10)) for q in pts) and any(near(q, (100, 100)) for q in pts), "polyline corners kept")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_vectorize()
    print("FAILED: %d" % len(FAILS) if FAILS else "ALL PASSED (incl. vectorize)")
    sys.exit(1 if FAILS else 0)

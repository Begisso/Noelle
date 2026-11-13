"""Demo project generator (used on first start of Noelle.exe and by tools/make_demo.py)."""
from __future__ import annotations

import csv
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "projects", "Demo")
SCHEMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema")


def w(rel, obj):
    p = os.path.join(ROOT, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        if isinstance(obj, str):
            f.write(obj)
        else:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")


def disp(name, size, objects, bg="#2b2f36", aliases=None, **kw):
    return {"format": "noelle-display", "version": 1, "name": name, "size": size, "background": bg,
            "aliases": aliases or {}, **kw, "objects": objects}


def text(id, x, y, w, h, t, fs=14, **kw):
    return {"id": id, "type": "text", "x": x, "y": y, "w": w, "h": h, "text": t, "fs": fs, "color": kw.pop("color", "#e6e9ee"),
            "align": kw.pop("align", "left"), "valign": "middle", "fill": kw.pop("fill", "none"), **kw}


def value(id, x, y, expr, fmt="0.0", units="", w=110, h=28, **kw):
    return {"id": id, "type": "value", "x": x, "y": y, "w": w, "h": h, "expr": expr, "fmt": fmt, "units": units, "fs": 15,
            "color": "#7ef0a0", "fill": "#14171b", "stroke": "#3a414b", "align": "right", "valign": "middle", "font": "Consolas", **kw}


def button(id, x, y, w, h, label, actions, fill="#3d6fb6", **kw):
    return {"id": id, "type": "button", "x": x, "y": y, "w": w, "h": h, "text": label, "fs": 14, "color": "#fff", "fill": fill,
            "stroke": "#1d2733", "rx": 4, "align": "center", "valign": "middle", "style": "3d", "actions": actions, **kw}


# ----------------------------------------------------------------------------- symbols
pump = disp("Pump", [80, 80], [
    {"id": "body", "type": "ellipse", "x": 6, "y": 6, "w": 68, "h": 68, "fill": "#59616c", "stroke": "#1a1d21", "sw": 2, "gradient": "",
     "dyn": {"fill": {"expr": "{{<#Pump#>.Run}}", "type": "bool", "on": "#2ebd5f", "off": "#59616c", "bad": "#ff00ff"},
             "blink": {"expr": "{{<#Pump#>.Fault}}"}}},
    {"id": "tri", "type": "polygon", "points": [[28, 22], [60, 40], [28, 58]], "fill": "#e9edf2", "stroke": "#1a1d21", "sw": 1,
     },
    {"id": "fault", "type": "ellipse", "x": 60, "y": 2, "w": 16, "h": 16, "fill": "#e5484d", "stroke": "#000",
     "dyn": {"visible": {"expr": "{{<#Pump#>.Fault}}"}, "blink": {"expr": "1", "fast": True}}},
], bg="transparent", aliases={"Pump": "Pump1"})
pump["objects"][0]["actions"] = [{"on": "click", "type": "open", "display": "symbols/PumpFaceplate.nll", "target": "popup",
                                  "aliases": {"Pump": "<#Pump#>"}, "title": "Pump <#Pump#>"}]
pump["objects"][1]["actions"] = pump["objects"][0]["actions"]
pump["objects"][0]["tooltip"] = "<#Pump#> — click for faceplate"

valve = disp("Valve", [60, 50], [
    {"id": "l", "type": "polygon", "points": [[2, 6], [30, 25], [2, 44]], "fill": "#59616c", "stroke": "#111", "sw": 1.5,
     "dyn": {"fill": {"expr": "{{<#Valve#>.Open}}", "type": "bool", "on": "#2ebd5f", "off": "#c9303a"}}},
    {"id": "r", "type": "polygon", "points": [[58, 6], [30, 25], [58, 44]], "fill": "#59616c", "stroke": "#111", "sw": 1.5,
     "dyn": {"fill": {"expr": "{{<#Valve#>.Open}}", "type": "bool", "on": "#2ebd5f", "off": "#c9303a"}}},
    {"id": "stem", "type": "rect", "x": 27, "y": 0, "w": 6, "h": 25, "fill": "#9aa3ad", "stroke": "#111"},
], bg="transparent", aliases={"Valve": "Valve1"})
for o in valve["objects"]:
    o["actions"] = [{"on": "click", "type": "toggle", "tag": "<#Valve#>.Open", "confirm": "Toggle <#Valve#>?", "role": "operator"}]

tank = disp("Tank", [140, 220], [
    {"id": "shell", "type": "rect", "x": 4, "y": 10, "w": 132, "h": 200, "rx": 14, "fill": "#3b4350", "stroke": "#151a20", "sw": 2,
     "gradient": "0,#2c333d,#5a6575,#2c333d",
     "dyn": {"level": {"expr": "{{<#Tank#>.Level}}", "in": [0, 100], "dir": "up", "color": "#2f8fe0"}}},
    {"id": "lvl", "type": "value", "x": 30, "y": 95, "w": 80, "h": 28, "expr": "{{<#Tank#>.Level}}", "fmt": "0.0", "units": "%", "fs": 15,
     "color": "#ffffff", "fill": "rgba(0,0,0,.45)", "stroke": "none", "align": "center", "valign": "middle", "font": "Consolas", "rx": 4},
    {"id": "hi", "type": "text", "x": 4, "y": 0, "w": 132, "h": 14, "text": "HI", "fs": 11, "color": "#ff6b6b", "align": "center", "valign": "middle",
     "dyn": {"visible": {"expr": "{{<#Tank#>.Level}} > 85"}, "blink": {"expr": "{{<#Tank#>.Level}} > 95"}}},
], bg="transparent", aliases={"Tank": "Tank1"})

face = disp("Pump faceplate", [320, 250], [
    {"id": "bg", "type": "rect", "x": 0, "y": 0, "w": 320, "h": 250, "fill": "#22272e", "stroke": "none"},
    text("t1", 12, 10, 296, 26, "Pump <#Pump#>", 18, fw=True),
    text("l1", 12, 48, 110, 26, "State"),
    {"id": "st", "type": "text", "x": 130, "y": 48, "w": 176, "h": 26, "text": "", "fs": 15, "color": "#fff", "align": "center", "valign": "middle", "fill": "#59616c", "rx": 3,
     "dyn": {"text": {"expr": "iif({{<#Pump#>.Fault}}, 'FAULT', iif({{<#Pump#>.Run}}, 'RUNNING', 'STOPPED'))"},
             "fill": {"expr": "iif({{<#Pump#>.Fault}}, 2, iif({{<#Pump#>.Run}}, 1, 0))", "type": "map", "map": {"0": "#59616c", "1": "#2ebd5f", "2": "#c9303a"}}}},
    text("l2", 12, 84, 110, 26, "Speed"),
    {"id": "spd", "type": "input", "x": 130, "y": 84, "w": 100, "h": 26, "tag": "<#Pump#>.Speed", "fmt": "0.0", "units": "%", "min": 0, "max": 100,
     "fs": 15, "color": "#fff", "fill": "#1d3046", "stroke": "#4b7bb8", "align": "right", "valign": "middle", "font": "Consolas"},
    {"id": "sl", "type": "slider", "x": 12, "y": 118, "w": 296, "h": 34, "tag": "<#Pump#>.Speed", "min": 0, "max": 100, "step": 1, "dir": "horizontal", "fill": "#2a3038", "barColor": "#3fa7ff"},
    button("start", 12, 166, 92, 36, "START", [{"on": "click", "type": "write", "tag": "<#Pump#>.Run", "value": "1", "role": "operator"}], fill="#23803f"),
    button("stop", 114, 166, 92, 36, "STOP", [{"on": "click", "type": "write", "tag": "<#Pump#>.Run", "value": "0", "role": "operator"}], fill="#a12b33"),
    button("reset", 216, 166, 92, 36, "RESET", [{"on": "click", "type": "write", "tag": "<#Pump#>.Fault", "value": "0"}], fill="#59616c"),
    button("close", 216, 210, 92, 30, "Close", [{"on": "click", "type": "close"}], fill="#3a414b"),
    text("l3", 12, 210, 200, 30, "Hours: ", 13, color="#8b949e", dyn={"text": {"expr": "'Run hours: ' + fmt({{<#Pump#>.Hours}}, '0.00')"}}),
], bg="#22272e", aliases={"Pump": "Pump1"})

# ----------------------------------------------------------------------------- main display
nav = lambda x, label, disp_path: button(f"nav_{label}", x, 12, 130, 34, label, [{"on": "click", "type": "open", "display": disp_path}], fill="#2d4f7c")
main_disp = disp("Overview", [1280, 720], [
    {"id": "header", "type": "rect", "x": 0, "y": 0, "w": 1280, "h": 58, "fill": "#1d2127", "stroke": "none"},
    text("title", 16, 12, 420, 34, "Noelle demo — Line <#Line#>", 22, fw=True),
    nav(600, "Overview", "displays/Main.nll"), nav(740, "Trends", "displays/Trends.nll"), nav(880, "Alarms", "displays/Alarms.nll"),
    {"id": "clock", "type": "value", "x": 1100, "y": 15, "w": 160, "h": 28, "expr": "{{sys:DateTime}}", "fmt": "", "fs": 14, "color": "#aab2bd",
     "fill": "none", "stroke": "none", "align": "right", "valign": "middle"},
    # process
    {"id": "pipe1", "type": "pipe", "points": [[230, 330], [330, 330], [330, 470], [470, 470]], "stroke": "#7c8794", "sw": 14, "flow": "{{Pump1.Run}}", "flowColor": "#7fd3ff"},
    {"id": "pipe2", "type": "pipe", "points": [[550, 470], [700, 470], [700, 300], [800, 300]], "stroke": "#7c8794", "sw": 14, "flow": "{{Pump1.Run}} && {{Valve1.Open}}", "flowColor": "#7fd3ff"},
    {"id": "tank1", "type": "symbol", "x": 90, "y": 200, "w": 140, "h": 220, "src": "symbols/Tank.nll", "aliases": {"Tank": "Tank1"}},
    {"id": "tank2", "type": "symbol", "x": 800, "y": 170, "w": 140, "h": 220, "src": "symbols/Tank.nll", "aliases": {"Tank": "Tank2"}},
    {"id": "pump1", "type": "symbol", "x": 470, "y": 430, "w": 80, "h": 80, "src": "symbols/Pump.nll", "aliases": {"Pump": "Pump1"}},
    {"id": "valve1", "type": "symbol", "x": 640, "y": 445, "w": 60, "h": 50, "src": "symbols/Valve.nll", "aliases": {"Valve": "Valve1"}},
    text("lt1", 90, 430, 140, 22, "Tank 1", 14, align="center"), text("lt2", 800, 400, 140, 22, "Tank 2", 14, align="center"),
    text("lp1", 455, 515, 110, 22, "Pump 1", 14, align="center"), text("lv1", 620, 500, 100, 22, "Valve 1", 14, align="center"),
    # data panel
    {"id": "panel", "type": "rect", "x": 990, "y": 80, "w": 270, "h": 330, "rx": 6, "fill": "#22272e", "stroke": "#3a414b", "shadow": True},
    text("ph", 1005, 88, 240, 26, "Process values", 15, fw=True),
    text("l_t", 1005, 122, 130, 26, "Temperature"), value("v_t", 1140, 122, "{{Temp1}}", "0.0", "°C",
                                                           dyn={"color": {"expr": "{{Temp1}}", "type": "range", "stops": [[0, "#7ef0a0"], [60, "#f0b429"], [70, "#ff5a5a"]]}}),
    text("l_f", 1005, 156, 130, 26, "Flow"), value("v_f", 1140, 156, "{{Flow1}}", "0.0", "m³/h"),
    text("l_s", 1005, 190, 130, 26, "Setpoint"),
    {"id": "sp", "type": "input", "x": 1140, "y": 190, "w": 110, "h": 28, "tag": "Setpoint", "fmt": "0.0", "units": "%", "min": 0, "max": 100, "fs": 15,
     "color": "#fff", "fill": "#1d3046", "stroke": "#4b7bb8", "align": "right", "valign": "middle", "font": "Consolas"},
    text("l_c", 1005, 224, 130, 26, "Counter (py)"), value("v_c", 1140, 224, "{{mem:Counter}}", "0", ""),
    {"id": "bar_t", "type": "bar", "x": 1005, "y": 262, "w": 34, "h": 130, "expr": "{{Temp1}}", "min": 0, "max": 100, "dir": "up", "barColor": "#ff8c42", "fill": "#14171b", "stroke": "#3a414b", "ticks": True},
    {"id": "g1", "type": "gauge", "x": 1060, "y": 258, "w": 140, "h": 140, "expr": "{{Flow1}}", "min": 0, "max": 200, "fmt": "0", "units": "m³/h",
     "ranges": "150,180,#f0b429;180,200,#e5484d", "fill": "#1b1f24", "stroke": "#3a414b", "color": "#e6e9ee", "needle": "#ff5a36"},
    {"id": "led_alm", "type": "led", "x": 1214, "y": 262, "w": 30, "h": 30, "expr": "{{sys:AlarmsUnacked}} > 0", "on": "#ff3b3b", "off": "#3a3f46", "stroke": "#111", "blinkOn": True},
    text("l_alm", 1205, 294, 50, 20, "alarm", 11, align="center", color="#8b949e"),
    # controls
    button("b_start", 300, 600, 120, 40, "Start pump", [{"on": "click", "type": "write", "tag": "Pump1.Run", "value": "1", "role": "operator"}], fill="#23803f"),
    button("b_stop", 430, 600, 120, 40, "Stop pump", [{"on": "click", "type": "write", "tag": "Pump1.Run", "value": "0"}], fill="#a12b33"),
    button("b_fault", 560, 600, 120, 40, "Simulate fault", [{"on": "click", "type": "write", "tag": "Pump1.Fault", "value": "1", "confirm": "Simulate a pump fault?"}], fill="#8a5a00"),
    button("b_face", 690, 600, 140, 40, "Pump faceplate", [{"on": "click", "type": "open", "display": "symbols/PumpFaceplate.nll", "target": "popup", "aliases": {"Pump": "Pump1"}}]),
    button("b_py", 840, 600, 140, 40, "Python: reset", [{"on": "click", "type": "py", "fn": "reset_counter"}], fill="#5a3d8a"),
    button("b_js", 990, 600, 130, 40, "JS: highlight", [{"on": "click", "type": "script", "fn": "highlight"}], fill="#5a3d8a"),
    {"id": "tog", "type": "toggle", "x": 1140, "y": 604, "w": 70, "h": 32, "tag": "Valve1.Open", "on": "#2ee66b", "off": "#59616c", "onValue": 1, "offValue": 0},
    text("ltog", 1214, 604, 60, 32, "Valve", 13),
    button("b_rec", 40, 600, 120, 40, "Recipe A", [{"on": "click", "type": "recipe", "name": "RecipeA", "mode": "apply"}], fill="#3a414b"),
    button("b_line", 170, 600, 120, 40, "Line 1 / 2", [{"on": "click", "type": "aliasSet", "set": "Line2"}], fill="#3a414b"),
    {"id": "trend1", "type": "trend", "x": 300, "y": 80, "w": 380, "h": 200, "span": 120,
     "pens": [{"expr": "{{Tank1.Level}}", "name": "Tank 1 level", "color": "#3fa7ff"}, {"expr": "{{Temp1}}", "name": "Temp", "color": "#ff8c42"}],
     "fill": "#111418", "grid": "#2a3038", "color": "#aab2bd", "legend": True},
    {"id": "alarms1", "type": "alarms", "x": 20, "y": 655, "w": 1240, "h": 58, "fill": "#14171b", "color": "#dfe3e8", "fs": 12},
], script="scripts/Main.js")

trends = disp("Trends", [1280, 720], [
    {"id": "header", "type": "rect", "x": 0, "y": 0, "w": 1280, "h": 58, "fill": "#1d2127", "stroke": "none"},
    text("title", 16, 12, 420, 34, "Trends", 22, fw=True),
    nav(600, "Overview", "displays/Main.nll"), nav(740, "Trends", "displays/Trends.nll"), nav(880, "Alarms", "displays/Alarms.nll"),
    {"id": "rt", "type": "trend", "x": 20, "y": 70, "w": 1240, "h": 300, "span": 300,
     "pens": [{"expr": "{{Tank1.Level}}", "name": "Tank 1", "color": "#3fa7ff"}, {"expr": "{{Tank2.Level}}", "name": "Tank 2", "color": "#2ee66b"},
              {"expr": "{{Temp1}}", "name": "Temp", "color": "#ff8c42"}, {"expr": "{{Flow1}} / 2", "name": "Flow/2", "color": "#d36cff"}],
     "fill": "#111418", "grid": "#2a3038", "color": "#aab2bd", "legend": True},
    text("hl", 20, 380, 600, 26, "Historian trend (http historian) — pens configurable in the editor", 14, color="#8b949e"),
    {"id": "ht", "type": "histtrend", "x": 20, "y": 410, "w": 1240, "h": 290, "span": 3600, "live": True,
     "pens": [{"desc": "1мс Расход Воздуха", "color": "#3fa7ff"}, {"desc": "Давление ТЭЦ", "color": "#ff8c42"}, {"desc": "Мельница №17 п", "color": "#2ee66b"}],
     "fill": "#111418", "grid": "#2a3038", "color": "#aab2bd", "legend": True},
])

alarms = disp("Alarms", [1280, 720], [
    {"id": "header", "type": "rect", "x": 0, "y": 0, "w": 1280, "h": 58, "fill": "#1d2127", "stroke": "none"},
    text("title", 16, 12, 420, 34, "Alarms", 22, fw=True),
    nav(600, "Overview", "displays/Main.nll"), nav(740, "Trends", "displays/Trends.nll"), nav(880, "Alarms", "displays/Alarms.nll"),
    button("ackall", 1100, 12, 160, 34, "ACK ALL", [{"on": "click", "type": "ack"}], fill="#a12b33"),
    {"id": "al", "type": "alarms", "x": 20, "y": 70, "w": 1240, "h": 630, "fill": "#14171b", "color": "#dfe3e8", "fs": 14},
])

PROJECT = {
    "format": "noelle-project", "version": 1, "name": "Demo",
    "startDisplay": "displays/Main.nll",
    "server": {"host": "127.0.0.1", "port": 8800, "flush_ms": 100},
    "historian": {"url": ""},
    "security": {"enabled": False, "guest_role": "viewer"},
    "logger": {"enabled": True},
    "alarms": {"sound": True},
    "devices": [
        {"name": "PLC1", "type": "modbus", "host": "127.0.0.1", "port": 5020, "unit": 1, "poll_ms": 500, "note": "example Modbus TCP device"},
        {"name": "S7_1500", "type": "s7", "host": "192.168.0.1", "rack": 0, "slot": 1, "poll_ms": 250, "note": "example S7 device"},
        {"name": "UA", "type": "opcua", "endpoint": "opc.tcp://localhost:4840", "publish_ms": 250},
        {"name": "DataHub", "type": "opcda", "server": "Cogent.DataHub.1", "rate_ms": 250},
        {"name": "DH_TCP", "type": "datahub", "host": "127.0.0.1", "port": 4502, "domain": "default"},
    ],
}

TAGS = [
    # Name, Address, Description, Units, Format, RawLo, RawHi, EngLo, EngHi, Deadband, ReadOnly, Default, Log, HiHi, Hi, Lo, LoLo, AlarmDeadband, Digital, Priority, Message, Group
    ["Tank1.Level", "sim:Sine(5,98,90)", "Tank 1 level", "%", "0.0", "", "", "", "", "0.05", "1", "", "change", "95", "85", "10", "", "1", "", "", "Tank 1 level", "Tanks"],
    ["Tank2.Level", "sim:Triangle(0,100,140)", "Tank 2 level", "%", "0.0", "", "", "", "", "", "1", "", "change", "", "90", "", "", "1", "", "", "Tank 2 level", "Tanks"],
    ["Temp1", "sim:Walk(20,80,0.6)", "Temperature", "°C", "0.0", "", "", "", "", "0.05", "1", "", "1000", "", "70", "", "", "2", "", "600", "High temperature", "Process"],
    ["Flow1", "=({{Tank1.Level}} * 1.8 if {{Pump1.Run}} else 0) + {{sim:Noise(0,1.5)}}", "Flow (expression tag)", "m³/h", "0.0", "", "", "", "", "", "1", "", "", "", "", "", "", "", "", "", "", "Process"],
    ["Pump1.Run", "", "Pump 1 running (memory)", "", "", "", "", "", "", "", "", "0", "change", "", "", "", "", "", "", "", "", "Pumps"],
    ["Pump1.Fault", "", "Pump 1 fault", "", "", "", "", "", "", "", "", "0", "change", "", "", "", "", "", "1", "900", "Pump 1 FAULT", "Pumps"],
    ["Pump1.Speed", "mem:Pump1Speed=75", "Pump 1 speed", "%", "0.0", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "Pumps"],
    ["Pump1.Hours", "mem:Pump1Hours", "Pump 1 run hours", "h", "0.00", "", "", "", "", "", "1", "", "", "", "", "", "", "", "", "", "", "Pumps"],
    ["Pump2.Run", "", "Pump 2 running", "", "", "", "", "", "", "", "", "1", "", "", "", "", "", "", "", "", "", "Pumps"],
    ["Pump2.Fault", "", "Pump 2 fault", "", "", "", "", "", "", "", "", "0", "", "", "", "", "", "", "1", "900", "Pump 2 FAULT", "Pumps"],
    ["Pump2.Speed", "mem:Pump2Speed=40", "Pump 2 speed", "%", "0.0", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "Pumps"],
    ["Pump2.Hours", "mem:Pump2Hours", "Pump 2 run hours", "h", "0.00", "", "", "", "", "", "1", "", "", "", "", "", "", "", "", "", "", "Pumps"],
    ["Valve1.Open", "mem:Valve1Open=1", "Valve 1 open", "", "", "", "", "", "", "", "", "", "change", "", "", "", "", "", "", "", "", "Valves"],
    ["Valve2.Open", "mem:Valve2Open=0", "Valve 2 open", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "Valves"],
    ["Setpoint", "mem:Setpoint=50", "Level setpoint", "%", "0.0", "", "", "", "", "", "", "", "change", "", "", "", "", "", "", "", "", "Process"],
    ["PLC1.Counts", "modbus:PLC1/40001", "Example raw Modbus register scaled 0..27648 -> 0..100", "%", "0.0", "0", "27648", "0", "100", "", "", "", "", "", "", "", "", "", "", "", "", "Examples"],
    ["S7.Speed", "s7:S7_1500/DB10.DBD4", "Example S7 REAL", "rpm", "0", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "Examples"],
]
COLS = ["Name", "Address", "Description", "Units", "Format", "RawLo", "RawHi", "EngLo", "EngHi", "Deadband", "ReadOnly", "Default", "Log",
        "HiHi", "Hi", "Lo", "LoLo", "AlarmDeadband", "Digital", "Priority", "Message", "Group"]

ALIASES = [["Default", "Line", "1"], ["Default", "Pump", "Pump1"], ["Default", "Tank", "Tank1"], ["Default", "Valve", "Valve1"],
           ["Line2", "Line", "2"], ["Line2", "Pump", "Pump2"], ["Line2", "Tank", "Tank2"], ["Line2", "Valve", "Valve2"]]

DEMO_PY = '''# Demo server script. Hot reloaded when you save it (VS Code / Notepad).
# Type hints for VS Code: scripts/hmi.pyi
import time


@hmi.every(1000)
def counter():
    """Count seconds and accumulate pump run hours."""
    hmi.write_nowait("mem:Counter", (hmi.read("mem:Counter") or 0) + 1)
    for p in ("Pump1", "Pump2"):
        if hmi.read(f"{p}.Run"):
            hmi.write_nowait(f"mem:{p}Hours", round((hmi.read(f"mem:{p}Hours") or 0) + 1 / 3600, 5))


@hmi.on_change("Pump1.Fault")
def fault(ref, value, old):
    if value:
        hmi.notify("Pump 1 fault! Pump stopped.", "error")
        hmi.write_nowait("Pump1.Run", 0)


@hmi.function
def reset_counter():
    """Called from the 'Python: reset' button (action type py)."""
    hmi.write_nowait("mem:Counter", 0)
    hmi.notify("counter reset from Python")
    return "ok"
'''

MAIN_JS = '''// Display script for displays/Main.nll ("script" property). Exported functions can be
// called by actions of type "script". Types: scripts/hmi.d.ts (VS Code IntelliSense).

/** @param {import("./hmi").DisplayContext} ctx */
export function onOpen(ctx) {
  ctx.subscribe("{{Temp1}}", (v) => ctx.set("panel", { stroke: v > 70 ? "#ff5a5a" : "#3a414b" }));
}

let on = false;
/** @param {import("./hmi").DisplayContext} ctx */
export function highlight(ctx) {
  on = !on;
  ctx.set("panel", { fill: on ? "#2d3a4d" : "#22272e" });
  ctx.toast(on ? "panel highlighted (JS)" : "highlight off");
}
'''

HMI_PYI = '''"""Type stub for the `hmi` object available in project scripts (VS Code IntelliSense)."""
from typing import Any, Awaitable, Callable

class _Hmi:
    def read(self, ref: str, default: Any = None) -> Any: ...
    def quality(self, ref: str) -> int: ...
    async def write(self, ref: str, value: Any) -> None: ...
    def write_nowait(self, ref: str, value: Any) -> None: ...
    async def wait_for(self, ref: str, timeout: float = 5.0) -> Any: ...
    def on_change(self, *refs: str) -> Callable[[Callable[[str, Any, Any], Any]], Callable]: ...
    def every(self, ms: int) -> Callable[[Callable[[], Any]], Callable]: ...
    def on_start(self, fn: Callable) -> Callable: ...
    def on_stop(self, fn: Callable) -> Callable: ...
    def function(self, fn: Callable) -> Callable: ...
    def log(self, *msg: Any) -> None: ...
    def notify(self, msg: str, level: str = "info") -> None: ...
    def alarm(self, name: str, active: bool, message: str = "", priority: int = 500) -> None: ...
    @property
    def historian(self) -> Any: ...

hmi: _Hmi
'''

HMI_DTS = '''// Types for display scripts (VS Code IntelliSense via JSDoc: @param {import("./hmi").DisplayContext} ctx)
export interface DisplayContext {
  /** current value of a tag reference ("{{...}}" not needed: "Tank1.Level") */
  read(ref: string): any;
  write(ref: string, value: any): Promise<void>;
  /** subscribe to an expression/tag; callback(value, quality) */
  subscribe(ref: string, fn: (value: any, quality: number) => void): void;
  open(path: string, opts?: { target?: "main" | "popup" | "window"; aliases?: Record<string, string>; title?: string }): Promise<any>;
  close(): void;
  alias(name: string): string;
  /** call a server Python function decorated with @hmi.function */
  py(name: string, args?: any): Promise<any>;
  toast(msg: string, level?: "info" | "warn" | "error"): void;
  /** SVG element of an object by id */
  get(id: string): SVGGElement | undefined;
  /** change visual properties of an object at runtime */
  set(id: string, props: { fill?: string; stroke?: string; text?: string; visible?: boolean; opacity?: number }): void;
  every(ms: number, fn: () => void): number;
  obj: any;
}
'''

RECIPE = [["Tag", "Value", "Description"], ["Setpoint", "65", "Level setpoint"], ["Pump1.Speed", "80", "Pump speed"], ["Valve1.Open", "1", "Open valve"]]


def install_vscode(root):
    """VS Code IntelliSense for .nll files: schemas are copied into <project>/.vscode."""
    import shutil
    vs = os.path.join(root, ".vscode")
    os.makedirs(vs, exist_ok=True)
    for f in os.listdir(SCHEMA_DIR):
        if f.endswith(".json"):
            shutil.copy(os.path.join(SCHEMA_DIR, f), os.path.join(vs, f))
    with open(os.path.join(vs, "settings.json"), "w", encoding="utf-8") as fh:
        json.dump({"files.associations": {"*.nll": "json"},
                   "json.schemas": [{"fileMatch": ["*.nll"], "url": "./.vscode/nll.schema.json"}],
                   "python.analysis.extraPaths": ["scripts"]}, fh, indent=2)


def create_demo(root):
    global ROOT
    ROOT = root
    return main()


def main():
    os.makedirs(ROOT, exist_ok=True)
    w("project.nll", PROJECT)
    with open(os.path.join(ROOT, "tags.csv"), "w", encoding="utf-8-sig", newline="") as f:
        cw = csv.writer(f)
        cw.writerow(COLS)
        cw.writerows(TAGS)
    with open(os.path.join(ROOT, "aliases.csv"), "w", encoding="utf-8-sig", newline="") as f:
        cw = csv.writer(f)
        cw.writerow(["Set", "Alias", "Value"])
        cw.writerows(ALIASES)
    os.makedirs(os.path.join(ROOT, "recipes"), exist_ok=True)
    with open(os.path.join(ROOT, "recipes", "RecipeA.csv"), "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(RECIPE)
    w("displays/Main.nll", main_disp)
    w("displays/Trends.nll", trends)
    w("displays/Alarms.nll", alarms)
    w("symbols/Pump.nll", pump)
    w("symbols/Valve.nll", valve)
    w("symbols/Tank.nll", tank)
    w("symbols/PumpFaceplate.nll", face)
    w("scripts/demo.py", DEMO_PY)
    w("scripts/Main.js", MAIN_JS)
    w("scripts/hmi.pyi", HMI_PYI)
    w("scripts/hmi.d.ts", HMI_DTS)
    install_vscode(ROOT)
    w("scripts/jsconfig.json", {"compilerOptions": {"checkJs": True, "module": "ES2022", "target": "ES2022"}, "include": ["*.js", "*.d.ts"]})
    for d in ("images", "logs"):
        os.makedirs(os.path.join(ROOT, d), exist_ok=True)
    print("demo project written to", ROOT)


if __name__ == "__main__":
    sys.exit(main())

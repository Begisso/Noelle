"""Local drivers: simulator (sim:), memory tags (mem:), system tags (sys:)."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
import os
import random
import time

from ..address import RefError
from ..engine import BAD_CONFIG, GOOD, Tag, split_call, to_number
from .base import Driver

SIM_FUNCS = {
    # name: (defaults, fn(t, args, state))
    "Sine": ((0.0, 100.0, 60.0), lambda t, a, s: a[0] + (a[1] - a[0]) * (0.5 + 0.5 * math.sin(2 * math.pi * t / a[2]))),
    "Cosine": ((0.0, 100.0, 60.0), lambda t, a, s: a[0] + (a[1] - a[0]) * (0.5 + 0.5 * math.cos(2 * math.pi * t / a[2]))),
    "Ramp": ((0.0, 100.0, 60.0), lambda t, a, s: a[0] + (a[1] - a[0]) * ((t % a[2]) / a[2])),
    "Triangle": ((0.0, 100.0, 60.0), lambda t, a, s: a[0] + (a[1] - a[0]) * (1 - abs(((t % a[2]) / a[2]) * 2 - 1))),
    "Square": ((0.0, 1.0, 10.0), lambda t, a, s: a[1] if (t % a[2]) < a[2] / 2 else a[0]),
    "Bool": ((10.0,), lambda t, a, s: (t % a[0]) < a[0] / 2),
    "Random": ((0.0, 100.0), lambda t, a, s: random.uniform(a[0], a[1])),
    "Noise": ((50.0, 5.0), lambda t, a, s: a[0] + random.uniform(-a[1], a[1])),
    "Walk": ((0.0, 100.0, 1.0), None),
    "Count": ((1.0,), None),
}


class SimItem:
    __slots__ = ("fn", "args", "rate", "state", "name")


class SimDriver(Driver):
    """Simulated signals, e.g. ``sim:Sine(0,100,30)``, ``sim:Ramp@1000``, ``sim:Bool(4)``."""
    kind = "sim"
    writable = False

    def __init__(self, engine, name, cfg):
        super().__init__(engine, name, cfg)
        self.connected = True
        self.rate_ms = 250

    def parse(self, tag: Tag):
        name, args, rate = split_call(tag.parsed.address)
        if name not in SIM_FUNCS:
            raise RefError(f"unknown sim function '{name}' (use {', '.join(SIM_FUNCS)})")
        defaults, fn = SIM_FUNCS[name]
        a = [float(x) if isinstance(x, (int, float)) else float(to_number(x)) for x in args]
        a = a + list(defaults[len(a):])
        if name in ("Sine", "Cosine", "Ramp", "Triangle", "Square", "Bool"):
            a[-1] = a[-1] or 1.0
        it = SimItem()
        it.name, it.fn, it.args, it.rate = name, fn, a, rate or self.rate_ms
        it.state = (a[0] + a[1]) / 2 if name == "Walk" else 0.0
        return it

    async def start(self):
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self):
        tick = 0
        while not self._stopping:
            await asyncio.sleep(0.1)
            tick += 100
            now = t = time.time()  # absolute time keeps all clients in phase
            for tag in tuple(self.tags):
                it = tag.item
                if tick % it.rate >= 100 and it.rate > 100:
                    continue
                if it.name == "Walk":
                    it.state = min(it.args[1], max(it.args[0], it.state + random.uniform(-it.args[2], it.args[2])))
                    v = it.state
                elif it.name == "Count":
                    it.state += it.args[0]
                    v = it.state
                else:
                    v = it.fn(t, it.args, it.state)
                if isinstance(v, float):
                    v = round(v, 4)
                self.engine.update(tag, v, GOOD, now)


class MemDriver(Driver):
    """In-memory tags (``mem:Name`` or ``mem:Name=initial``). Retentive: values are
    persisted to ``<project>/runtime/retain.json``."""
    kind = "mem"

    def __init__(self, engine, name, cfg, project=None):
        super().__init__(engine, name, cfg)
        self.connected = True
        self.values: dict[str, object] = {}
        self.path = os.path.join(project.root, "runtime", "retain.json") if project else None
        self._save_handle = None
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    self.values = json.load(f)
            except Exception:
                self.values = {}

    def parse(self, tag: Tag):
        addr = tag.parsed.address
        name, _, init = addr.partition("=")
        name = name.strip()
        if not name:
            raise RefError("empty memory tag name")
        if name not in self.values:
            self.values[name] = _parse_literal(init) if init else 0
        return name

    def add(self, tag: Tag):
        super().add(tag)
        self.engine.update(tag, self.values.get(tag.item), GOOD, force=True)

    async def write(self, tag: Tag, value):
        self.values[tag.item] = value
        self.engine.update(tag, value, GOOD)
        self.stats["writes"] += 1
        self._schedule_save()

    def set(self, name: str, value):
        """Used by scripts / recipes to set memory tags directly."""
        self.values[name] = value
        tag = self.engine.tags.get(f"mem:{name}")
        if tag:
            self.engine.update(tag, value, GOOD)
        self._schedule_save()

    def _schedule_save(self):
        if not self.path or self._save_handle:
            return
        loop = asyncio.get_running_loop()
        self._save_handle = loop.call_later(2.0, self._save)

    def _save(self):
        self._save_handle = None
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.values, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except Exception as e:
            self.set_error(f"retain save: {e}")

    async def stop(self):
        if self._save_handle:
            self._save_handle.cancel()
            self._save()
        await super().stop()

    async def browse(self, path=""):
        return [{"name": k, "ref": f"mem:{k}", "value": v} for k, v in sorted(self.values.items())]


def _parse_literal(s: str):
    s = s.strip()
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s.strip('"\'')


class SysDriver(Driver):
    """System tags: Time, Date, DateTime, Epoch, Uptime, Clients, AlarmsActive,
    AlarmsUnacked, Driver.<device>.Connected / .Error / .RttMs"""
    kind = "sys"
    writable = False
    providers: dict = {}   # name -> callable, filled by app (alarms, clients)

    def __init__(self, engine, name, cfg):
        super().__init__(engine, name, cfg)
        self.connected = True
        self.t_start = time.time()

    def parse(self, tag: Tag):
        return tag.parsed.address

    def add(self, tag):
        super().add(tag)
        self._eval(tag, time.time())

    async def start(self):
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self):
        while not self._stopping:
            now = time.time()
            await asyncio.sleep(1.0 - (now % 1.0) + 0.002)  # align to second boundary
            now = time.time()
            for tag in tuple(self.tags):
                self._eval(tag, now)

    def _eval(self, tag, now):
        n = tag.item
        d = dt.datetime.fromtimestamp(now)
        try:
            if n == "Time":
                v = d.strftime("%H:%M:%S")
            elif n == "Date":
                v = d.strftime("%Y-%m-%d")
            elif n == "DateTime":
                v = d.strftime("%Y-%m-%d %H:%M:%S")
            elif n == "Epoch":
                v = int(now)
            elif n == "Second":
                v = d.second
            elif n == "Uptime":
                v = int(now - self.t_start)
            elif n.startswith("Driver."):
                _, rest = n.split(".", 1)
                dev, _, field = rest.rpartition(".")
                drv = self.engine.drivers.get(dev)
                if drv is None:
                    v = None
                else:
                    st = drv.status()
                    v = {"Connected": int(bool(st.get("connected"))), "Error": st.get("error", ""),
                         "RttMs": st.get("rtt_ms", 0), "Tags": st.get("tags", 0)}.get(field)
            elif n in self.providers:
                v = self.providers[n]()
            else:
                self.engine.update(tag, None, BAD_CONFIG)
                return
            self.engine.update(tag, v, GOOD, now)
        except Exception:
            self.engine.update(tag, None, BAD_CONFIG)

    async def browse(self, path=""):
        names = ["Time", "Date", "DateTime", "Epoch", "Second", "Uptime", *sorted(self.providers)]
        out = [{"name": n, "ref": f"sys:{n}"} for n in names]
        for key in self.engine.drivers:
            if key not in ("sim", "mem", "sys", "hist"):
                out.append({"name": f"Driver.{key}.Connected", "ref": f"sys:Driver.{key}.Connected"})
        return out

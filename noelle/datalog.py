"""Data logger (tags.csv 'Log' column -> logs/data/YYYY-MM-DD.csv) and recipes."""
from __future__ import annotations

import asyncio
import bisect
import csv
import datetime as dt
import logging
import os
import time

from .engine import GOOD, TagEngine
from .project import Project, num, read_csv, write_csv

log = logging.getLogger("noelle.datalog")


class DataLogger:
    """Log = "change" logs every change, Log = <ms> samples periodically."""

    def __init__(self, engine: TagEngine, project: Project):
        self.engine = engine
        self.project = project
        self.folder = os.path.join(project.root, "logs", "data")
        self.buf: list[tuple] = []
        self.held = []
        self.tasks = []

    def load(self):
        self.stop_logging()
        if not self.project.cfg.get("logger", {}).get("enabled", True):
            return
        for r in self.project.tag_rows():
            mode = (r.get("Log") or "").strip().lower()
            if not mode or mode in ("0", "no", "false"):
                continue
            tag = self.engine.acquire(r["Name"])
            self.engine.enable_history(tag)
            if mode in ("change", "1", "yes", "true", "x"):
                def on_change(t, _old):
                    if t.quality >= GOOD:
                        self.buf.append((t.ts, t.ref, t.value, t.quality))
                tag.listeners.append(on_change)
                self.held.append((tag, on_change))
            else:
                ms = max(100, int(num(mode, 1000)))
                self.held.append((tag, None))
                self.tasks.append(asyncio.get_running_loop().create_task(self._sample(tag, ms)))
        self.tasks.append(asyncio.get_running_loop().create_task(self._flusher()))

    def stop_logging(self):
        for t in self.tasks:
            t.cancel()
        self.tasks.clear()
        for tag, fn in self.held:
            if fn and fn in tag.listeners:
                tag.listeners.remove(fn)
            self.engine.release(tag)
        self.held.clear()
        self.flush()

    async def _sample(self, tag, ms):
        while True:
            await asyncio.sleep(ms / 1000 - (time.time() % (ms / 1000)))
            if tag.quality >= GOOD:
                self.buf.append((time.time(), tag.ref, tag.value, tag.quality))

    async def _flusher(self):
        while True:
            await asyncio.sleep(2.0)
            self.flush()

    def flush(self):
        if not self.buf:
            return
        rows, self.buf = self.buf, []
        try:
            os.makedirs(self.folder, exist_ok=True)
            by_day: dict[str, list] = {}
            for r in rows:
                by_day.setdefault(dt.datetime.fromtimestamp(r[0]).strftime("%Y-%m-%d"), []).append(r)
            for day, lst in by_day.items():
                p = os.path.join(self.folder, f"{day}.csv")
                new = not os.path.exists(p)
                with open(p, "a", encoding="utf-8-sig" if new else "utf-8", newline="") as f:
                    w = csv.writer(f)
                    if new:
                        w.writerow(["Time", "Tag", "Value", "Quality"])
                    for t, ref, v, q in lst:
                        w.writerow([dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], ref, v, q])
        except Exception:
            log.exception("data logger flush")

    def query(self, ref: str, t0: float, t1: float, max_points=20000):
        """Read logged values back (for trends beyond the in-memory buffer)."""
        self.flush()
        out_t, out_v = [], []
        d = dt.date.fromtimestamp(t0)
        end = dt.date.fromtimestamp(t1)
        while d <= end:
            p = os.path.join(self.folder, f"{d:%Y-%m-%d}.csv")
            if os.path.exists(p):
                with open(p, encoding="utf-8-sig") as f:
                    for row in csv.reader(f):
                        if len(row) < 3 or row[1] != ref:
                            continue
                        try:
                            t = dt.datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S.%f").timestamp()
                        except ValueError:
                            continue
                        if t0 <= t <= t1:
                            v = num(row[2], row[2])
                            out_t.append(t)
                            out_v.append(v)
            d += dt.timedelta(days=1)
        if len(out_t) > max_points:
            step = len(out_t) / max_points
            idx = [int(i * step) for i in range(max_points)]
            out_t = [out_t[i] for i in idx]
            out_v = [out_v[i] for i in idx]
        return out_t, out_v


class Recipes:
    def __init__(self, engine: TagEngine, project: Project):
        self.engine = engine
        self.project = project

    @property
    def folder(self):
        return os.path.join(self.project.root, "recipes")

    def list(self):
        if not os.path.isdir(self.folder):
            return []
        return sorted(os.path.splitext(f)[0] for f in os.listdir(self.folder) if f.lower().endswith(".csv"))

    def get(self, name):
        return read_csv(os.path.join(self.folder, f"{name}.csv"))

    def save(self, name, rows):
        os.makedirs(self.folder, exist_ok=True)
        write_csv(os.path.join(self.folder, f"{name}.csv"), rows, ["Tag", "Value", "Description"])

    async def snapshot(self, name, refs):
        rows = []
        for ref in refs:
            t = self.engine.acquire(ref)
            for _ in range(30):
                if t.quality >= GOOD:
                    break
                await asyncio.sleep(0.1)
            rows.append({"Tag": ref, "Value": "" if t.value is None else t.value})
            self.engine.release(t)
        self.save(name, rows)
        return rows

    async def apply(self, name, user="system"):
        results = []
        for r in self.get(name):
            ref, val = r.get("Tag"), r.get("Value", "")
            if not ref:
                continue
            v = num(val, val)
            try:
                await self.engine.write(ref, v, user=user, source=f"recipe:{name}")
                results.append({"tag": ref, "ok": True})
            except Exception as e:
                results.append({"tag": ref, "ok": False, "error": str(e)})
        return results

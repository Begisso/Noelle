"""Alarm manager: limit (HiHi/Hi/Lo/LoLo) and digital alarms from tags.csv,
plus script raised alarms. History is written to logs/alarms/YYYY-MM-DD.csv."""
from __future__ import annotations

import asyncio
import csv
import logging
import os
import time

from .engine import GOOD, Tag, TagEngine
from .project import Project, num

log = logging.getLogger("noelle.alarms")

CONDITIONS = [("HiHi", ">", 900), ("Hi", ">", 700), ("Lo", "<", 700), ("LoLo", "<", 900)]


class Alarm:
    __slots__ = ("id", "tag", "cond", "limit", "message", "priority", "active", "acked",
                 "t_active", "t_ack", "t_clear", "value", "ack_user", "group")

    def __init__(self, aid, tag, cond, limit, message, priority, group=""):
        self.id, self.tag, self.cond, self.limit = aid, tag, cond, limit
        self.message, self.priority, self.group = message, priority, group
        self.active = False
        self.acked = True
        self.t_active = self.t_ack = self.t_clear = 0.0
        self.value = None
        self.ack_user = ""

    def to_json(self):
        return {"id": self.id, "tag": self.tag, "cond": self.cond, "limit": self.limit, "msg": self.message,
                "prio": self.priority, "active": self.active, "acked": self.acked, "group": self.group,
                "t": round(self.t_active * 1000), "tack": round(self.t_ack * 1000),
                "tclr": round(self.t_clear * 1000), "value": self.value, "user": self.ack_user}


class AlarmManager:
    def __init__(self, engine: TagEngine, project: Project):
        self.engine = engine
        self.project = project
        self.alarms: dict[str, Alarm] = {}
        self._held: list[tuple[Tag, object]] = []
        self.listeners = []          # fn() on any change (debounced by server)
        self.folder = os.path.join(project.root, "logs", "alarms")

    # ------------------------------------------------------------ configure
    def load(self):
        for tag, fn in self._held:
            if fn in tag.listeners:
                tag.listeners.remove(fn)
            self.engine.release(tag)
        self._held.clear()
        old = self.alarms
        self.alarms = {}
        for r in self.project.tag_rows():
            name = r.get("Name")
            if not name:
                continue
            prio_given = (r.get("Priority") or "").strip() != ""
            prio = int(num(r.get("Priority"), 500))
            msg = r.get("Message") or r.get("Description") or name
            db = num(r.get("AlarmDeadband"), 0.0) or 0.0
            defs = []
            for cond, op, p in CONDITIONS:
                lim = num(r.get(cond))
                if lim is not None:
                    defs.append(Alarm(f"{name}.{cond}", name, cond, lim, f"{msg} {cond}", prio if prio_given else p, r.get("Group", "")))
            dig = (r.get("Digital") or "").strip()
            if dig != "":
                defs.append(Alarm(f"{name}.Digital", name, "Digital", num(dig, dig), msg, prio, r.get("Group", "")))
            if not defs:
                continue
            for a in defs:
                prev = old.get(a.id)
                if prev:   # keep state across reloads
                    a.active, a.acked, a.t_active, a.t_ack, a.value = prev.active, prev.acked, prev.t_active, prev.t_ack, prev.value
                self.alarms[a.id] = a
            tag = self.engine.acquire(name)

            def on_change(t, _old, defs=defs, db=db):
                self._evaluate(t, defs, db)

            tag.listeners.append(on_change)
            self._held.append((tag, on_change))
            if tag.quality >= GOOD:
                self._evaluate(tag, defs, db)
        self._notify()

    # ------------------------------------------------------------ evaluation
    def _evaluate(self, tag: Tag, defs, db):
        if tag.quality < GOOD or tag.value is None:
            return
        v = tag.value
        changed = False
        for a in defs:
            if a.cond == "Digital":
                try:
                    active = float(v) == float(a.limit)
                except (TypeError, ValueError):
                    active = str(v) == str(a.limit)
            else:
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                hi = a.cond in ("Hi", "HiHi")
                if a.active:   # hysteresis on clear
                    active = fv > a.limit - db if hi else fv < a.limit + db
                else:
                    active = fv > a.limit if hi else fv < a.limit
            a.value = v
            if active and not a.active:
                a.active, a.acked, a.t_active = True, False, time.time()
                self._history(a, "ACTIVE")
                changed = True
            elif not active and a.active:
                a.active, a.t_clear = False, time.time()
                self._history(a, "CLEAR")
                changed = True
        if changed:
            self._notify()

    def raise_alarm(self, name: str, active: bool, message: str = "", priority: int = 500):
        """Script API: hmi.alarm(...)"""
        a = self.alarms.get(name)
        if a is None:
            a = Alarm(name, name, "Script", None, message or name, priority)
            self.alarms[name] = a
        if message:
            a.message = message
        if active and not a.active:
            a.active, a.acked, a.t_active = True, False, time.time()
            self._history(a, "ACTIVE")
        elif not active and a.active:
            a.active, a.t_clear = False, time.time()
            self._history(a, "CLEAR")
        self._notify()

    def ack(self, ids: list[str] | None, user: str):
        n = 0
        for a in self.alarms.values():
            if not a.acked and (ids is None or a.id in ids):
                a.acked, a.t_ack, a.ack_user = True, time.time(), user
                self._history(a, "ACK", user)
                n += 1
        if n:
            self._notify()
        return n

    # ------------------------------------------------------------ queries
    def current(self):
        lst = [a for a in self.alarms.values() if a.active or not a.acked]
        lst.sort(key=lambda a: (-a.priority, -a.t_active))
        return [a.to_json() for a in lst]

    def counts(self):
        act = sum(1 for a in self.alarms.values() if a.active)
        unack = sum(1 for a in self.alarms.values() if not a.acked)
        return act, unack

    def history_rows(self, day: str, limit=5000):
        p = os.path.join(self.folder, f"{day}.csv")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        return rows[-limit:]

    # ------------------------------------------------------------ internals
    def _history(self, a: Alarm, event: str, user: str = ""):
        try:
            os.makedirs(self.folder, exist_ok=True)
            p = os.path.join(self.folder, time.strftime("%Y-%m-%d") + ".csv")
            new = not os.path.exists(p)
            with open(p, "a", encoding="utf-8-sig" if new else "utf-8", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["Time", "Event", "Alarm", "Tag", "Condition", "Limit", "Value", "Priority", "Message", "User"])
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), event, a.id, a.tag, a.cond, a.limit, a.value,
                            a.priority, a.message, user])
        except Exception:
            log.exception("alarm history")

    def _notify(self):
        for fn in self.listeners:
            try:
                fn()
            except Exception:
                log.exception("alarm listener")

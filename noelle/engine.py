"""Tag engine: one Tag object per unique reference, reference counted.

Performance notes
-----------------
* A tag is attached to its driver only while something uses it (a display,
  an alarm, the logger, a script).  Unused tags are detached after a short
  grace period, so drivers poll *only* what is on screen / needed.
* Drivers call :meth:`TagEngine.update`; deadband + "no change" filtering is
  done here, once, before fan-out.
* Fan-out to websocket sessions only marks ``session.pending[cid]`` - the
  session flushes coalesced deltas at a fixed rate (default 10 Hz), so a tag
  changing 1000x/s still costs one message slot per flush.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections import deque
from typing import Any, Callable

from .address import Ref, RefError, parse_ref

log = logging.getLogger("noelle.engine")

# OPC style quality codes
GOOD = 192
UNCERTAIN = 64
BAD = 0
BAD_CONFIG = 4       # bad address / unknown tag
BAD_NOT_CONNECTED = 8
BAD_COMM = 24        # communication failure

DETACH_GRACE_S = 5.0
HISTORY_MAX = 7200   # samples kept per trended tag (in-memory backfill)


class Tag:
    __slots__ = ("ref", "parsed", "value", "quality", "ts", "subs", "listeners", "users",
                 "driver", "item", "deadband", "history", "meta", "detach_handle", "error")

    def __init__(self, ref: str, parsed: Ref | None):
        self.ref = ref
        self.parsed = parsed
        self.value: Any = None
        self.quality = BAD_NOT_CONNECTED
        self.ts = 0.0
        self.subs: dict[Any, int] = {}           # websocket session -> client id
        self.listeners: list[Callable] = []      # in-process listeners fn(tag, old)
        self.users = 0                           # reference count
        self.driver = None
        self.item: Any = None                    # driver private data (parsed address)
        self.deadband = 0.0
        self.history: deque | None = None
        self.meta: dict = {}
        self.detach_handle = None
        self.error = ""

    def __repr__(self):
        return f"<Tag {self.ref}={self.value!r} q={self.quality}>"


class TagEngine:
    def __init__(self):
        self.tags: dict[str, Tag] = {}
        self.drivers: dict[str, Any] = {}        # device key -> driver instance
        self.factories: dict[str, Callable] = {}  # driver kind -> factory(engine, name, cfg)
        self.device_cfg: dict[str, dict] = {}    # configured devices by name
        self.tagdb = None                        # set by project (TagDatabase)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.write_hooks: list[Callable] = []     # audit / security hooks
        self.stats = {"updates": 0, "writes": 0}

    # ------------------------------------------------------------------ drivers
    def register_factory(self, kind: str, factory: Callable):
        self.factories[kind] = factory

    def configure_devices(self, devices: list[dict]):
        self.device_cfg = {d["name"]: d for d in devices if d.get("name")}

    def _driver_for(self, parsed: Ref):
        kind = parsed.driver
        if kind in ("sim", "mem", "sys", "hist"):
            key = kind
            cfg = {}
        elif parsed.direct:
            key = f"{kind}:{parsed.device}"
            cfg = {"direct": parsed.direct_params}
        else:
            key = parsed.device
            cfg = self.device_cfg.get(parsed.device)
            if cfg is None:
                raise RefError(f"unknown device '{parsed.device}' (define it in project.nll or use direct [..] addressing)")
            ctype = cfg.get("type", "")
            if ctype and ctype != kind and not (ctype == "datahub" and kind == "datahub"):
                raise RefError(f"device '{parsed.device}' is type '{ctype}', not '{kind}'")
        drv = self.drivers.get(key)
        if drv is None:
            factory = self.factories.get(kind)
            if factory is None:
                raise RefError(f"no driver for '{kind}'")
            drv = factory(self, key, cfg)
            self.drivers[key] = drv
            if self.loop:
                self.loop.create_task(drv.start())
        return drv

    # --------------------------------------------------------------- tag access
    def get(self, ref: str) -> Tag:
        tag = self.tags.get(ref)
        if tag is not None:
            return tag
        try:
            parsed = parse_ref(ref)
        except RefError as e:
            tag = Tag(ref, None)
            tag.quality, tag.error = BAD_CONFIG, str(e)
            self.tags[ref] = tag
            return tag
        if parsed.raw != ref and parsed.raw in self.tags:
            self.tags[ref] = self.tags[parsed.raw]
            return self.tags[ref]
        tag = Tag(parsed.raw, parsed)
        self.tags[parsed.raw] = tag
        if parsed.raw != ref:
            self.tags[ref] = tag
        return tag

    def acquire(self, ref: str) -> Tag:
        tag = self.get(ref)
        tag.users += 1
        if tag.detach_handle:
            tag.detach_handle.cancel()
            tag.detach_handle = None
        if tag.driver is None and tag.parsed is not None:
            self._attach(tag)
        return tag

    def release(self, tag: Tag):
        tag.users -= 1
        if tag.users <= 0:
            tag.users = 0
            if self.loop and tag.driver is not None and tag.detach_handle is None:
                tag.detach_handle = self.loop.call_later(DETACH_GRACE_S, self._detach, tag)

    def _attach(self, tag: Tag):
        p = tag.parsed
        try:
            if p.driver == "tag":
                if self.tagdb is None:
                    raise RefError("no tag database")
                drv = self.tagdb
            else:
                drv = self._driver_for(p)
            tag.driver = drv
            tag.error = ""
            drv.add(tag)
        except Exception as e:  # bad address etc.
            tag.driver = None
            tag.error = str(e)
            self.update(tag, None, BAD_CONFIG)
            log.debug("attach %s failed: %s", tag.ref, e)

    def _detach(self, tag: Tag):
        tag.detach_handle = None
        if tag.users > 0 or tag.driver is None:
            return
        try:
            tag.driver.remove(tag)
        except Exception:
            log.exception("detach %s", tag.ref)
        tag.driver = None
        tag.quality = BAD_NOT_CONNECTED
        tag.history = None
        # keep the Tag object only if someone still listens (cheap), else drop
        if not tag.subs and not tag.listeners:
            for k in [k for k, v in self.tags.items() if v is tag]:
                del self.tags[k]

    # ------------------------------------------------------------------ updates
    def update(self, tag: Tag, value: Any, quality: int = GOOD, ts: float | None = None, force: bool = False):
        """Called by drivers. Filters no-change/deadband, then fans out."""
        if not force and quality == tag.quality:
            old = tag.value
            if value == old:
                return
            db = tag.deadband
            if db and isinstance(value, (int, float)) and isinstance(old, (int, float)) and not isinstance(value, bool):
                if abs(value - old) < db:
                    return
        old = tag.value
        tag.value = value
        tag.quality = quality
        tag.ts = ts or time.time()
        self.stats["updates"] += 1
        if tag.history is not None and quality >= GOOD and isinstance(value, (int, float)):
            tag.history.append((tag.ts, float(value)))
        if tag.subs:
            entry = (value, quality, tag.ts)
            for sess, cid in tag.subs.items():
                sess.pending[cid] = entry
                sess.kick()
        if tag.listeners:
            for fn in tuple(tag.listeners):
                try:
                    fn(tag, old)
                except Exception:
                    log.exception("listener on %s", tag.ref)

    def set_quality_all(self, driver, quality: int, tags=None):
        for t in (tags if tags is not None else list(getattr(driver, "tags", ()))):
            self.update(t, t.value, quality)

    def enable_history(self, tag: Tag):
        if tag.history is None:
            tag.history = deque(maxlen=HISTORY_MAX)
            if tag.quality >= GOOD and isinstance(tag.value, (int, float)):
                tag.history.append((tag.ts or time.time(), float(tag.value)))

    # ------------------------------------------------------------------- writes
    async def write(self, ref: str, value: Any, user: str = "system", source: str = "") -> None:
        tag = self.get(ref)
        if tag.parsed is None:
            raise RefError(tag.error or "bad reference")
        temp = tag.driver is None
        if temp:
            self.acquire(ref)
        try:
            if tag.driver is None:
                raise RefError(tag.error or "tag not available")
            if tag.meta.get("readonly"):
                raise PermissionError(f"{ref} is read-only")
            old = tag.value
            await tag.driver.write(tag, value)
            self.stats["writes"] += 1
            for hook in self.write_hooks:
                try:
                    hook(user, ref, old, value, source)
                except Exception:
                    log.exception("write hook")
        finally:
            if temp:
                self.release(tag)

    def read(self, ref: str):
        t = self.tags.get(ref)
        return None if t is None else t.value

    # ------------------------------------------------------------------- status
    def status(self) -> dict:
        drivers = {}
        for key, d in self.drivers.items():
            try:
                drivers[key] = d.status()
            except Exception as e:
                drivers[key] = {"error": str(e)}
        active = sum(1 for t in set(self.tags.values()) if t.driver is not None)
        return {"tags": len(set(self.tags.values())), "active": active, "drivers": drivers, **self.stats}

    async def stop(self):
        for d in list(self.drivers.values()):
            try:
                await d.stop()
            except Exception:
                log.exception("stop driver")


# --------------------------------------------------------------------------
# value coercion helpers shared by drivers
# --------------------------------------------------------------------------
def to_number(v: Any) -> float:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower()
    if s in ("true", "on", "yes"):
        return 1.0
    if s in ("false", "off", "no", ""):
        return 0.0
    return float(s.replace(",", "."))


def to_bool(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() not in ("0", "false", "off", "no", "")
    return bool(v)


_ARGS = re.compile(r"^([A-Za-z_][\w.]*)\s*(?:\((.*)\))?\s*(?:@(\d+))?$")


def split_call(addr: str):
    """'Sine(0,100,30)@500' -> ('Sine', [0.0,100.0,30.0], 500)"""
    m = _ARGS.match(addr.strip())
    if not m:
        raise RefError(f"bad address '{addr}'")
    name, args, rate = m.group(1), m.group(2), m.group(3)
    out = []
    if args:
        for a in args.split(","):
            a = a.strip()
            if not a:
                continue
            try:
                out.append(float(a))
            except ValueError:
                out.append(a)
    return name, out, int(rate) if rate else None


def finite(v):
    return v if not isinstance(v, float) or math.isfinite(v) else None

"""Driver base classes."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict

from ..engine import BAD_COMM, BAD_NOT_CONNECTED, GOOD, Tag, TagEngine

log = logging.getLogger("noelle.driver")


class Driver:
    kind = "base"
    writable = True

    def __init__(self, engine: TagEngine, name: str, cfg: dict | None):
        self.engine = engine
        self.name = name
        self.cfg = cfg or {}
        self.tags: set[Tag] = set()
        self.connected = False
        self.last_error = ""
        self.stats = {"reads": 0, "writes": 0, "errors": 0, "rtt_ms": 0.0}
        self._task: asyncio.Task | None = None
        self._stopping = False

    # lifecycle ---------------------------------------------------------------
    async def start(self):
        pass

    async def stop(self):
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    # tags --------------------------------------------------------------------
    def parse(self, tag: Tag):
        """Parse tag.parsed.address into a driver item. Raise on error."""
        return tag.parsed.address

    def add(self, tag: Tag):
        tag.item = self.parse(tag)
        self.tags.add(tag)
        self.on_tags_changed()

    def remove(self, tag: Tag):
        self.tags.discard(tag)
        self.on_tags_changed()

    def on_tags_changed(self):
        pass

    async def write(self, tag: Tag, value):
        raise PermissionError(f"{self.kind} driver is read-only")

    async def browse(self, path: str = "") -> list[dict]:
        return []

    def set_error(self, err: str):
        if err and err != self.last_error:
            log.warning("[%s] %s", self.name, err)
        self.last_error = err
        if err:
            self.stats["errors"] += 1

    def status(self) -> dict:
        return {"kind": self.kind, "connected": self.connected, "tags": len(self.tags),
                "error": self.last_error, **self.stats}


class PollingDriver(Driver):
    """Driver with scan classes (poll rates). Subclasses implement
    ``connect()``, ``disconnect()``, ``build_plan(tags)`` and ``execute(plan)``.

    The read plan is rebuilt only when the tag set changes; each scan class
    (``@ms`` suffix on an address, default = device poll_ms) is polled at its
    own rate over a single connection.
    """

    default_poll_ms = 500

    def __init__(self, engine, name, cfg):
        super().__init__(engine, name, cfg)
        self.poll_ms = int(self.cfg.get("poll_ms", self.default_poll_ms))
        self._plans: dict[int, object] = {}
        self._dirty = True
        self._wake = asyncio.Event()
        self._poll_now = False
        self.lock = asyncio.Lock()   # serializes reads and writes on the link

    def poll_soon(self):
        self._poll_now = True
        self._wake.set()

    def rate_of(self, tag: Tag) -> int:
        return getattr(tag.item, "rate", None) or self.poll_ms

    def on_tags_changed(self):
        self._dirty = True
        self._wake.set()

    async def start(self):
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self):
        await super().stop()
        await self._safe_disconnect()

    async def connect(self):
        raise NotImplementedError

    async def disconnect(self):
        pass

    def build_plan(self, tags: list[Tag]):
        raise NotImplementedError

    async def execute(self, plan) -> None:
        raise NotImplementedError

    async def _run(self):
        backoff = 1.0
        next_due: dict[int, float] = {}
        while not self._stopping:
            if not self.tags:
                if self.connected:
                    await self._safe_disconnect()
                self._wake.clear()
                await self._wake.wait()
                continue
            if not self.connected:
                try:
                    await self.connect()
                    self.connected = True
                    self.set_error("")
                    backoff = 1.0
                    self._dirty = True
                except Exception as e:
                    self.set_error(f"connect failed: {e}")
                    self.engine.set_quality_all(self, BAD_NOT_CONNECTED)
                    await self._sleep(backoff)
                    backoff = min(backoff * 2, 15.0)
                    continue
            if self._dirty:
                self._dirty = False
                groups: dict[int, list[Tag]] = defaultdict(list)
                for t in self.tags:
                    groups[self.rate_of(t)].append(t)
                self._plans = {}
                for rate, tags in groups.items():
                    try:
                        self._plans[rate] = self.build_plan(tags)
                    except Exception as e:
                        log.exception("[%s] plan", self.name)
                        self.set_error(f"plan: {e}")
                now = time.monotonic()
                next_due = {r: now for r in self._plans}
            now = time.monotonic()
            if self._poll_now:          # read-back right after a write
                self._poll_now = False
                for r in next_due:
                    next_due[r] = now
            due = [r for r, t in next_due.items() if t <= now]
            try:
                for rate in due:
                    plan = self._plans.get(rate)
                    if plan is None:
                        continue
                    t0 = time.perf_counter()
                    async with self.lock:
                        await self.execute(plan)
                    self.stats["rtt_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                    self.stats["reads"] += 1
                    next_due[rate] = max(next_due[rate] + rate / 1000.0, time.monotonic())
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.set_error(f"poll failed: {e}")
                self.connected = False
                await self._safe_disconnect()
                self.engine.set_quality_all(self, BAD_COMM)
                await self._sleep(backoff)
                backoff = min(backoff * 2, 15.0)
                continue
            if next_due:
                await self._sleep(max(0.0, min(next_due.values()) - time.monotonic()))

    async def _sleep(self, seconds: float):
        """Sleep, but wake early if the tag set changes."""
        if self._poll_now or self._dirty:
            return
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _safe_disconnect(self):
        try:
            await self.disconnect()
        except Exception:
            pass
        self.connected = False

    def publish(self, tag: Tag, value, quality=GOOD):
        self.engine.update(tag, value, quality)

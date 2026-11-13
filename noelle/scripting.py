"""Server side Python scripts (project/scripts/*.py), hot reloaded on save.

Inside a script the global ``hmi`` object is available (see scripts/hmi.pyi
for VS Code IntelliSense)::

    @hmi.on_change("mem:Start")
    def start_changed(ref, value, old):
        hmi.log(f"start -> {value}")

    @hmi.every(1000)
    async def tick():
        await hmi.write("mem:Counter", (hmi.read("mem:Counter") or 0) + 1)

    @hmi.function
    def reset_counter():          # callable from displays: action "py" / ctx.py()
        hmi.write_nowait("mem:Counter", 0)
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import time
import traceback

from .engine import GOOD, TagEngine

log = logging.getLogger("noelle.scripts")


class ScriptContext:
    """The ``hmi`` object given to one script file."""

    def __init__(self, host: "ScriptHost", file: str):
        self._host = host
        self._file = file
        self._held = []        # (tag, listener)
        self._tasks = []
        self._on_stop = []
        self.engine: TagEngine = host.engine

    # ---------------------------------------------------------------- tags
    def _hold(self, ref):
        for t, _ in self._held:
            if t.ref == ref:
                return t
        t = self.engine.acquire(ref)
        self._held.append((t, None))
        return t

    def read(self, ref: str, default=None):
        t = self._hold(ref)
        return t.value if t.quality >= GOOD and t.value is not None else default

    def quality(self, ref: str) -> int:
        return self._hold(ref).quality

    async def write(self, ref: str, value):
        await self.engine.write(ref, value, user=f"script:{os.path.basename(self._file)}", source="script")

    def write_nowait(self, ref: str, value):
        self._spawn(self.write(ref, value))

    async def wait_for(self, ref: str, timeout: float = 5.0):
        t = self._hold(ref)
        t0 = time.time()
        while t.quality < GOOD and time.time() - t0 < timeout:
            await asyncio.sleep(0.05)
        return t.value

    # ---------------------------------------------------------------- decorators
    def on_change(self, *refs):
        def deco(fn):
            for ref in refs:
                t = self.engine.acquire(ref)

                def listener(tag, old, fn=fn, ref=ref):
                    self._call(fn, ref, tag.value, old)

                t.listeners.append(listener)
                self._held.append((t, listener))
            return fn
        return deco

    def every(self, ms: int):
        def deco(fn):
            async def loop():
                period = ms / 1000
                while True:
                    await asyncio.sleep(period - (time.time() % period))
                    await self._acall(fn)
            self._tasks.append(asyncio.get_running_loop().create_task(loop()))
            return fn
        return deco

    def on_start(self, fn):
        self._call(fn)
        return fn

    def on_stop(self, fn):
        self._on_stop.append(fn)
        return fn

    def function(self, fn):
        self._host.functions[fn.__name__] = (self, fn)
        return fn

    # ---------------------------------------------------------------- services
    def log(self, *msg):
        text = " ".join(str(m) for m in msg)
        log.info("[%s] %s", os.path.basename(self._file), text)
        self._host.emit("log", f"[{os.path.basename(self._file)}] {text}")

    def notify(self, msg: str, level: str = "info"):
        self._host.emit("toast", msg, level)

    def alarm(self, name: str, active: bool, message: str = "", priority: int = 500):
        if self._host.alarms:
            self._host.alarms.raise_alarm(name, active, message, priority)

    @property
    def historian(self):
        return self._host.historian

    # ---------------------------------------------------------------- internals
    def _spawn(self, coro):
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.append(task)
        task.add_done_callback(lambda t: self._tasks.remove(t) if t in self._tasks else None)
        task.add_done_callback(self._report)

    def _report(self, task):
        if not task.cancelled() and task.exception():
            self._host.error(self._file, task.exception())

    def _call(self, fn, *args):
        try:
            res = fn(*args)
            if inspect.isawaitable(res):
                self._spawn(res)
        except Exception as e:
            self._host.error(self._file, e)

    async def _acall(self, fn, *args):
        try:
            res = fn(*args)
            if inspect.isawaitable(res):
                await res
        except Exception as e:
            self._host.error(self._file, e)

    def _teardown(self):
        for fn in self._on_stop:
            self._call(fn)
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        for tag, fn in self._held:
            if fn and fn in tag.listeners:
                tag.listeners.remove(fn)
            self.engine.release(tag)
        self._held.clear()
        for name, (ctx, _) in list(self._host.functions.items()):
            if ctx is self:
                del self._host.functions[name]


class ScriptHost:
    def __init__(self, engine, project, alarms=None, historian=None):
        self.engine = engine
        self.project = project
        self.alarms = alarms
        self.historian = historian
        self.contexts: dict[str, ScriptContext] = {}
        self.functions: dict[str, tuple] = {}
        self.emitters = []       # fn(kind, *args) -> broadcast to clients
        self.errors: dict[str, str] = {}

    @property
    def folder(self):
        return os.path.join(self.project.root, "scripts")

    def emit(self, kind, *args):
        for fn in self.emitters:
            try:
                fn(kind, *args)
            except Exception:
                pass

    def error(self, file, exc):
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self.errors[os.path.basename(file)] = tb
        log.error("script %s: %s", file, tb)
        self.emit("log", f"[{os.path.basename(file)}] ERROR {exc}\n{tb}")

    def load_all(self):
        if not os.path.isdir(self.folder):
            return
        for fn in sorted(os.listdir(self.folder)):
            if fn.endswith(".py") and not fn.startswith("_"):
                self.load(os.path.join(self.folder, fn))

    def load(self, path: str):
        self.unload(path)
        if not os.path.exists(path):
            return
        ctx = ScriptContext(self, path)
        self.contexts[path] = ctx
        self.errors.pop(os.path.basename(path), None)
        try:
            with open(path, encoding="utf-8-sig") as f:
                code = compile(f.read(), path, "exec")
            exec(code, {"__name__": f"script_{os.path.basename(path)[:-3]}", "__file__": path, "hmi": ctx,
                        "asyncio": asyncio})
            ctx.log("loaded")
        except Exception as e:
            self.error(path, e)

    def unload(self, path: str):
        ctx = self.contexts.pop(path, None)
        if ctx:
            ctx._teardown()

    def on_file_changed(self, rel: str):
        if rel.startswith("scripts/") and rel.endswith(".py"):
            self.load(os.path.join(self.project.root, rel))

    async def call(self, name: str, args: dict | list | None):
        entry = self.functions.get(name)
        if not entry:
            raise KeyError(f"no script function '{name}' (decorate it with @hmi.function)")
        ctx, fn = entry
        res = fn(**args) if isinstance(args, dict) else fn(*(args or []))
        if inspect.isawaitable(res):
            res = await res
        return res

    def stop(self):
        for p in list(self.contexts):
            self.unload(p)

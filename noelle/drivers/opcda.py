"""OPC DA (classic) client via a 32-bit PowerShell bridge process.

Use it for Cogent DataHub (``Cogent.DataHub.1`` - works without the DataHub TCP
API license), ICONICS, Kepware, WinCC, CoDeSys and other OPC DA 2.0 servers.

Addresses are OPC item IDs, e.g. ``opcda:DataHub/default:Tank1``
(device ``{"name":"DataHub","type":"opcda","server":"Cogent.DataHub.1"}``) or
direct ``opcda:[Cogent.DataHub.1]default:Tank1`` / ``opcda:[host,ProgID]item``.
Device options: server (ProgID), node (remote host), rate_ms=250.
The bridge reads the server cache at rate_ms and only sends changed values.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import os
import sys

from .. import jsonutil
from ..engine import BAD_COMM, BAD_CONFIG, BAD_NOT_CONNECTED, Tag
from .base import Driver

log = logging.getLogger("noelle.opcda")
BRIDGE = os.path.join(os.path.dirname(__file__), "opcda_bridge.ps1")


def _powershell32() -> str:
    win = os.environ.get("windir", r"C:\Windows")
    p = os.path.join(win, "SysWOW64", "WindowsPowerShell", "v1.0", "powershell.exe")
    return p if os.path.exists(p) else os.path.join(win, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


class OpcDaDriver(Driver):
    kind = "opcda"

    def __init__(self, engine, name, cfg):
        cfg = dict(cfg or {})
        if "direct" in cfg:
            p = [x.strip() for x in cfg["direct"].split(",")]
            if len(p) == 2:
                cfg["node"], cfg["server"] = p
            else:
                cfg["server"] = p[0]
        super().__init__(engine, name, cfg)
        self.server = cfg.get("server", "Cogent.DataHub.1")
        self.node = cfg.get("node", "")
        self.rate = int(cfg.get("rate_ms", cfg.get("poll_ms", 250)))
        self.proc = None
        self.handles = itertools.count(1)
        self.by_handle: dict[int, list[Tag]] = {}
        self.by_item: dict[str, int] = {}
        self.req_ids = itertools.count(1)
        self.requests: dict[int, asyncio.Future] = {}
        self.ready = asyncio.Event()
        self.kick = asyncio.Event()

    def parse(self, tag):
        return tag.parsed.address.strip()

    def add(self, tag):
        tag.item = self.parse(tag)
        self.tags.add(tag)
        h = self.by_item.get(tag.item)
        if h is None:
            h = next(self.handles)
            self.by_item[tag.item] = h
            self.by_handle[h] = [tag]
            self._send({"op": "add", "items": [[h, tag.item]]})
        else:
            self.by_handle[h].append(tag)
        if not self.connected:
            self.engine.update(tag, None, BAD_NOT_CONNECTED)
        self.kick.set()

    def remove(self, tag):
        self.tags.discard(tag)
        h = self.by_item.get(tag.item)
        if h is None:
            return
        lst = self.by_handle.get(h, [])
        if tag in lst:
            lst.remove(tag)
        if not lst:
            self.by_handle.pop(h, None)
            self.by_item.pop(tag.item, None)
            self._send({"op": "remove", "items": [h]})

    def _send(self, msg):
        if self.proc and self.proc.stdin and self.connected:
            try:
                self.proc.stdin.write((jsonutil.dumps(msg) + "\n").encode("utf-8"))
            except Exception:
                pass

    async def start(self):
        if sys.platform != "win32":
            self.set_error("OPC DA requires Windows")
            return
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self):
        backoff = 2.0
        while not self._stopping:
            if not self.by_handle:
                self.kick.clear()
                await self.kick.wait()
                continue
            try:
                self.proc = await asyncio.create_subprocess_exec(
                    _powershell32(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-File", BRIDGE, "-Server", self.server, "-Node", self.node or "", "-Rate", str(self.rate),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    limit=64 * 1024 * 1024,
                    creationflags=0x08000000)  # CREATE_NO_WINDOW
                err_task = asyncio.get_running_loop().create_task(self._stderr())
                await self._reader()
                err_task.cancel()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.set_error(f"bridge: {e}")
            self.connected = False
            self.engine.set_quality_all(self, BAD_COMM)
            for f in self.requests.values():
                if not f.done():
                    f.set_exception(ConnectionError("bridge stopped"))
            self.requests.clear()
            if self.proc and self.proc.returncode is None:
                self.proc.kill()
            if not self._stopping:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _stderr(self):
        buf = []
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                break
            buf.append(line.decode("utf-8", "replace").strip())
            if len(buf) > 5:
                self.set_error(" ".join(buf[-5:])[:400])

    async def _reader(self):
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                return
            try:
                msg = jsonutil.loads(line)
            except Exception:
                continue
            op = msg.get("op")
            if op == "d":
                upd = self.engine.update
                for h, v, q in msg["v"]:
                    for tag in self.by_handle.get(h, ()):
                        upd(tag, v, q)
            elif op == "connected":
                self.connected = True
                self.set_error("")
                items = [[h, tags[0].item] for h, tags in self.by_handle.items() if tags]
                if items:
                    self._send({"op": "add", "items": items})
            elif op == "bad":
                for h, err in msg["items"]:
                    for tag in self.by_handle.get(h, ()):
                        tag.error = err
                        self.engine.update(tag, None, BAD_CONFIG)
            elif op == "res":
                f = self.requests.pop(msg.get("id"), None)
                if f and not f.done():
                    if msg.get("ok"):
                        f.set_result(msg.get("result"))
                    else:
                        f.set_exception(RuntimeError(msg.get("error", "error")))
            elif op == "fatal":
                self.set_error(msg.get("msg", "fatal"))
                return

    async def _call(self, msg, timeout=15.0):
        if not self.connected:
            raise ConnectionError(self.last_error or "OPC DA bridge not connected")
        rid = next(self.req_ids)
        fut = asyncio.get_running_loop().create_future()
        self.requests[rid] = fut
        msg["id"] = rid
        self._send(msg)
        return await asyncio.wait_for(fut, timeout)

    async def write(self, tag, value):
        h = self.by_item.get(tag.item)
        if h is None:
            raise RuntimeError("item not subscribed")
        await self._call({"op": "write", "h": h, "value": value})
        self.stats["writes"] += 1

    async def browse(self, path=""):
        if not self.connected:   # browsing should work even with no tags yet
            self.by_handle.setdefault(0, [])
            self.kick.set()
            for _ in range(100):
                if self.connected:
                    break
                await asyncio.sleep(0.1)
        res = await self._call({"op": "browse", "path": path}, timeout=60)
        dev = f"[{self.server}]" if self.name.startswith("opcda:[") else f"{self.name}/"
        out = []
        for r in res or []:
            if r.get("branch"):
                out.append({"name": r["name"], "path": r["path"], "branch": True})
            else:
                out.append({"name": r["name"], "ref": f"opcda:{dev}{r['item']}"})
        return out

    async def stop(self):
        self._stopping = True
        self._send({"op": "exit"})
        await asyncio.sleep(0.2)
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
        await super().stop()

"""Cogent DataHub native TCP client (DHTP, default port 4502).

Event driven: points are registered with ``(report name)`` and the DataHub
pushes ``(point ...)`` messages on every change - no polling.

NOTE: the DataHub TCP API needs a DataHub license that includes TCP/API
clients. If the DataHub answers "No license", use the OPC DA driver instead:
``opcda:[Cogent.DataHub.1]default:Point``.

Addresses: ``domain:point`` (e.g. ``default:Tank1``).
Device options: host=127.0.0.1, port=4502, domain=default.
Direct: ``dh:[127.0.0.1:4502]default:Tank1``
"""
from __future__ import annotations

import asyncio
import time

from ..engine import BAD_COMM, BAD_CONFIG, BAD_NOT_CONNECTED, GOOD, Tag
from .base import Driver


def sexpr_tokens(s: str):
    """Tokenize a DHTP line into nested lists of strings."""
    stack, cur, i, n = [], None, 0, len(s)
    out = []
    while i < n:
        c = s[i]
        if c == "(":
            new = []
            if cur is not None:
                cur.append(new)
                stack.append(cur)
            cur = new
            i += 1
        elif c == ")":
            if stack:
                cur = stack.pop()
            else:
                if cur is not None:
                    out.append(cur)
                cur = None
            i += 1
        elif c == '"':
            j, buf = i + 1, []
            while j < n and s[j] != '"':
                if s[j] == "\\" and j + 1 < n:
                    j += 1
                buf.append(s[j])
                j += 1
            if cur is not None:
                cur.append("".join(buf))
            i = j + 1
        elif c.isspace():
            i += 1
        else:
            j = i
            while j < n and not s[j].isspace() and s[j] not in '()"':
                j += 1
            if cur is not None:
                cur.append(s[i:j])
            i = j
    return out


def _q(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


class DataHubDriver(Driver):
    kind = "datahub"

    def __init__(self, engine, name, cfg):
        cfg = dict(cfg or {})
        if "direct" in cfg:
            host, _, port = cfg["direct"].partition(":")
            cfg.update(host=host or "127.0.0.1", port=int(port or 4502))
        super().__init__(engine, name, cfg)
        self.host = cfg.get("host", "127.0.0.1")
        self.port = int(cfg.get("port", 4502))
        self.domain = cfg.get("domain", "default")
        self.reader = self.writer = None
        self.by_point: dict[str, list[Tag]] = {}
        self.kick = asyncio.Event()

    def parse(self, tag):
        a = tag.parsed.address.strip()
        return a if ":" in a else f"{self.domain}:{a}"

    def add(self, tag):
        tag.item = self.parse(tag)
        self.tags.add(tag)
        lst = self.by_point.setdefault(tag.item, [])
        lst.append(tag)
        if len(lst) == 1:
            self._cmd(f"(report {_q(tag.item)})")
        if not self.connected:
            self.engine.update(tag, None, BAD_NOT_CONNECTED)
        self.kick.set()

    def remove(self, tag):
        self.tags.discard(tag)
        lst = self.by_point.get(tag.item, [])
        if tag in lst:
            lst.remove(tag)
        if not lst:
            self.by_point.pop(tag.item, None)
            self._cmd(f"(unreport {_q(tag.item)})")

    def _cmd(self, s):
        if self.writer and self.connected:
            self.writer.write((s + "\n").encode("utf-8"))

    async def start(self):
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self):
        backoff = 1.0
        while not self._stopping:
            if not self.by_point:
                self.kick.clear()
                await self.kick.wait()
                continue
            try:
                self.reader, self.writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), 5)
                self.connected = True
                self.set_error("")
                backoff = 1.0
                for p in self.by_point:
                    self._cmd(f"(report {_q(p)})")
                ka = asyncio.get_running_loop().create_task(self._keepalive())
                try:
                    await self._read_loop()
                finally:
                    ka.cancel()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.set_error(f"{type(e).__name__}: {e}")
            self.connected = False
            self.engine.set_quality_all(self, BAD_COMM)
            if self.writer:
                self.writer.close()
            self.writer = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 15)

    async def _keepalive(self):
        while True:
            await asyncio.sleep(10)
            self._cmd("(alive)")

    async def _read_loop(self):
        buf = ""
        while True:
            data = await self.reader.read(65536)
            if not data:
                raise ConnectionError("DataHub closed the connection")
            buf += data.decode("utf-8", "replace")
            # split complete top level expressions (quotes aware); rescan the
            # incomplete tail from scratch on the next chunk
            start, i, inq, depth = 0, 0, False, 0
            msgs = []
            while i < len(buf):
                c = buf[i]
                if inq:
                    if c == "\\":
                        i += 1
                    elif c == '"':
                        inq = False
                elif c == '"':
                    inq = True
                elif c == "(":
                    if depth == 0:
                        start = i
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        msgs.append(buf[start:i + 1])
                        start = i + 1
                i += 1
            buf = buf[start:] if depth else ""
            for m in msgs:
                for expr in sexpr_tokens(m):
                    self._handle(expr)

    def _handle(self, e):
        if not e:
            return
        head = e[0]
        if head == "point" and len(e) >= 4:
            name, typ, val = e[1], e[2], e[3]
            tags = self.by_point.get(name)
            if not tags and ":" not in name:
                tags = self.by_point.get(f"{self.domain}:{name}")
            if not tags:
                return
            v = _convert(typ, val)
            q = GOOD
            ts = None
            try:
                if len(e) >= 9:
                    ts = int(e[7]) + int(e[8]) / 1e9
                if len(e) >= 11:
                    q = int(e[10])
                elif len(e) == 10:
                    q = int(e[9])
            except ValueError:
                pass
            for t in tags:
                self.engine.update(t, v, q, ts or time.time())
        elif head == "error":
            msg = " ".join(str(x) for x in e[1:])
            if "No license" in msg:
                msg += " - DataHub TCP API is not licensed; use opcda:[Cogent.DataHub.1]<domain:point>"
                for tags in self.by_point.values():
                    for t in tags:
                        t.error = msg
                        self.engine.update(t, None, BAD_CONFIG)
            self.set_error(msg)

    async def write(self, tag, value):
        if not self.connected:
            raise ConnectionError("not connected")
        if isinstance(value, bool):
            value = int(value)
        self._cmd(f"(set {_q(tag.item)} {_q(value)})")
        self.stats["writes"] += 1

    async def browse(self, path=""):
        return [{"name": p, "ref": f"dh:{self.name}/{p}" if not self.name.startswith("datahub:[") else f"dh:{self.name[8:]}{p}"}
                for p in sorted(self.by_point)]


def _convert(typ, val):
    try:
        t = int(typ)
    except ValueError:
        t = -1
    try:
        if t == 1:
            return float(val)
        if t == 2:
            return int(val)
    except ValueError:
        pass
    return val

"""Siemens S7 (S7-300/400/1200/1500, LOGO!, WinAC) native ISO-on-TCP client.

Siemens addressing (German I/E, Q/A mnemonics both accepted)::

    DB10.DBX4.0   BOOL          DB10.DBB4   BYTE      DB10.DBW4  INT
    DB10.DBD4     REAL          DB10.DBD4:DINT / :DWORD / :UDINT / :TIME
    DB10.DBW4:WORD / :UINT      DB10.DBD4:LREAL (8 bytes)   DB10.DBB20:STRING30
    M10.2  MB10  MW10  MD10(REAL)   I0.0/E0.0  IB0 IW0 ID0   Q0.0/A0.0  QB QW QD
    DB1,REAL4  DB1,INT2  DB1,X0.1  (Step7 / NetToPLCsim style)   ...@1000 scan rate

Device options: host, rack=0, slot=1 (S7-300: slot 2), port=102, poll_ms=500,
max_gap=32 (bytes), local_tsap/remote_tsap (hex, for LOGO! / special CPs).
Direct addressing: ``s7:[192.168.0.1,0,1]DB1.DBD0``

Performance: adjacent addresses are merged into byte blocks, and many blocks are
packed into a single multi-item "read var" PDU (respecting the negotiated PDU
size), so a whole screen usually costs one request/response round trip.
For S7-1200/1500 enable PUT/GET and disable "optimized block access" on DBs.
"""
from __future__ import annotations

import asyncio
import re
import struct
from dataclasses import dataclass, field

from ..address import RefError
from ..engine import BAD_CONFIG, GOOD, Tag, to_bool, to_number
from .base import PollingDriver

AREA = {"I": 0x81, "E": 0x81, "Q": 0x82, "A": 0x82, "M": 0x83, "DB": 0x84, "C": 0x1C, "Z": 0x1C, "T": 0x1D}
SIZE = {"BOOL": 1, "BYTE": 1, "CHAR": 1, "SINT": 1, "USINT": 1, "WORD": 2, "INT": 2, "UINT": 2,
        "DWORD": 4, "DINT": 4, "UDINT": 4, "REAL": 4, "TIME": 4, "LREAL": 8, "LINT": 8, "ULINT": 8}
FMT = {"BYTE": "B", "USINT": "B", "SINT": "b", "CHAR": "c", "WORD": "H", "UINT": "H", "INT": "h",
       "DWORD": "I", "UDINT": "I", "DINT": "i", "TIME": "i", "REAL": "f", "LREAL": "d", "LINT": "q", "ULINT": "Q"}
DEFAULT_TYPE = {"X": "BOOL", "B": "BYTE", "W": "INT", "D": "REAL"}

_DB = re.compile(r"^DB(\d+)\.DB([XBWD])(\d+)(?:\.(\d))?$", re.I)
_DBC = re.compile(r"^DB(\d+),\s*([A-Z]+)(\d+)(?:\.(\d))?$", re.I)
_AREA = re.compile(r"^(I|E|Q|A|M)([BWD]?)(\d+)(?:\.(\d))?$", re.I)
_TC = re.compile(r"^(T|C|Z)(\d+)$", re.I)


@dataclass(eq=False)
class S7Item:
    area: int
    db: int
    start: int            # byte offset
    bit: int              # -1 = not a bit
    dtype: str
    size: int
    strlen: int = 0
    rate: int | None = None


def parse_address(addr: str) -> S7Item:
    rate = None
    a = addr.strip()
    if "@" in a:
        a, r = a.rsplit("@", 1)
        rate = int(r)
    typ = None
    if ":" in a:
        a, typ = a.split(":", 1)
        typ = typ.strip().upper()
    a = a.strip().replace(" ", "")
    area, db, start, bit, kind = None, 0, 0, -1, None
    if m := _DB.match(a):
        area, db, kind, start = 0x84, int(m.group(1)), m.group(2).upper(), int(m.group(3))
        if kind == "X":
            if m.group(4) is None:
                raise RefError(f"bit number required in '{addr}'")
            bit = int(m.group(4))
    elif m := _DBC.match(a):
        area, db, t, start = 0x84, int(m.group(1)), m.group(2).upper(), int(m.group(3))
        if t in ("X", "BOOL"):
            kind, bit = "X", int(m.group(4) or 0)
        elif t in SIZE or t.startswith("STRING"):
            kind = "B"
            typ = typ or t
        else:
            raise RefError(f"unknown type '{t}' in '{addr}'")
    elif m := _AREA.match(a):
        area = AREA[m.group(1).upper()]
        kind = (m.group(2) or "X").upper()
        start = int(m.group(3))
        if kind == "X":
            if m.group(4) is None:
                raise RefError(f"bit number required in '{addr}' (e.g. M10.2)")
            bit = int(m.group(4))
    elif m := _TC.match(a):
        area = AREA[m.group(1).upper()]
        start, kind = int(m.group(2)) * 2, "W"   # 2 bytes per timer/counter
        typ = typ or "WORD"
    else:
        raise RefError(f"bad S7 address '{addr}'")
    if bit > 7:
        raise RefError("bit number must be 0..7")
    strlen = 0
    if typ and typ.startswith("STRING"):
        strlen = int(typ[6:] or 254)
        return S7Item(area, db, start, -1, "STRING", strlen + 2, strlen, rate)
    dtype = typ or DEFAULT_TYPE[kind]
    if dtype not in SIZE:
        raise RefError(f"unknown S7 type '{dtype}'")
    if kind == "X":
        dtype = "BOOL"
    return S7Item(area, db, start, bit, dtype, SIZE[dtype] if dtype != "BOOL" else 1, 0, rate)


def decode(it: S7Item, raw: bytes):
    if it.dtype == "BOOL":
        return bool((raw[0] >> it.bit) & 1) if it.bit >= 0 else bool(raw[0])
    if it.dtype == "STRING":
        n = min(raw[1], it.strlen)
        return raw[2: 2 + n].decode("latin-1", "replace")
    if it.dtype == "CHAR":
        return raw[:1].decode("latin-1")
    v = struct.unpack(">" + FMT[it.dtype], raw[: it.size])[0]
    if it.dtype == "REAL":
        v = round(v, 7) if abs(v) < 1e15 else v
    return v


def encode(it: S7Item, value) -> bytes:
    if it.dtype == "STRING":
        b = str(value).encode("latin-1", "replace")[: it.strlen]
        return bytes([it.strlen, len(b)]) + b
    if it.dtype == "CHAR":
        return str(value).encode("latin-1")[:1] or b"\0"
    n = to_number(value)
    f = FMT[it.dtype]
    return struct.pack(">" + f, n if f in "fd" else int(round(n)))


@dataclass
class Block:
    area: int
    db: int
    start: int
    size: int
    items: list = field(default_factory=list)


def build_blocks(pairs, max_gap=32, max_block=200) -> list[Block]:
    groups: dict[tuple, list] = {}
    for tag, it in pairs:
        groups.setdefault((it.area, it.db), []).append((tag, it))
    out = []
    for (area, db), lst in groups.items():
        lst.sort(key=lambda p: p[1].start)
        cur = None
        for tag, it in lst:
            end = it.start + it.size
            if cur and it.start - (cur.start + cur.size) <= max_gap and end - cur.start <= max_block:
                cur.size = max(cur.size, end - cur.start)
                cur.items.append((tag, it))
            else:
                cur = Block(area, db, it.start, it.size, [(tag, it)])
                out.append(cur)
    return out


class S7Error(Exception):
    pass


RET = {0xFF: "ok", 0x01: "hardware fault", 0x03: "access denied (PUT/GET? optimized DB?)",
       0x05: "address out of range", 0x06: "data type not supported", 0x07: "data type inconsistent",
       0x0A: "object does not exist"}


class S7Driver(PollingDriver):
    kind = "s7"

    def __init__(self, engine, name, cfg):
        cfg = dict(cfg or {})
        if "direct" in cfg:
            p = [x.strip() for x in cfg["direct"].split(",")]
            host, _, port = p[0].partition(":")
            cfg.update(host=host, port=int(port or 102))
            if len(p) > 1:
                cfg["rack"] = int(p[1])
            if len(p) > 2:
                cfg["slot"] = int(p[2])
        super().__init__(engine, name, cfg)
        self.timeout = float(cfg.get("timeout_ms", 2000)) / 1000
        self.max_gap = int(cfg.get("max_gap", 32))
        self.pdu = 240
        self.ref = 0
        self.reader = self.writer = None

    def parse(self, tag: Tag):
        return parse_address(tag.parsed.address)

    # --------------------------------------------------------------- protocol
    async def connect(self):
        c = self.cfg
        host, port = c.get("host", "127.0.0.1"), int(c.get("port", 102))
        rack, slot = int(c.get("rack", 0)), int(c.get("slot", 1))
        local_tsap = int(str(c.get("local_tsap", "0100")), 16)
        remote_tsap = int(str(c.get("remote_tsap", "")), 16) if c.get("remote_tsap") else 0x0100 | (rack * 0x20 + slot)
        self.reader, self.writer = await asyncio.wait_for(asyncio.open_connection(host, port), self.timeout)
        # COTP connection request
        cotp = bytes([0x11, 0xE0, 0x00, 0x00, 0x00, 0x01, 0x00,
                      0xC0, 0x01, 0x0A,
                      0xC1, 0x02]) + struct.pack(">H", local_tsap) + bytes([0xC2, 0x02]) + struct.pack(">H", remote_tsap)
        await self._send_tpkt(cotp)
        resp = await self._recv_tpkt()
        if len(resp) < 2 or resp[1] != 0xD0:
            raise S7Error("COTP connection refused (check rack/slot)")
        # S7 setup communication, request PDU 960
        params = bytes([0xF0, 0x00, 0x00, 0x01, 0x00, 0x01]) + struct.pack(">H", 960)
        r = await self._job(params, b"")
        self.pdu = struct.unpack(">H", r[0][6:8])[0] if len(r[0]) >= 8 else 240

    async def disconnect(self):
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
        self.writer = None

    async def _send_tpkt(self, payload: bytes):
        self.writer.write(struct.pack(">BBH", 3, 0, len(payload) + 4) + payload)
        await self.writer.drain()

    async def _recv_tpkt(self) -> bytes:
        hdr = await asyncio.wait_for(self.reader.readexactly(4), self.timeout)
        ln = struct.unpack(">H", hdr[2:4])[0]
        return await asyncio.wait_for(self.reader.readexactly(ln - 4), self.timeout)

    async def _job(self, params: bytes, data: bytes):
        """Send an S7 job, return (params, data) of the ack_data reply."""
        if self.writer is None:
            raise ConnectionError("not connected")
        self.ref = (self.ref + 1) & 0xFFFF
        hdr = struct.pack(">BBHHHH", 0x32, 0x01, 0, self.ref, len(params), len(data))
        await self._send_tpkt(b"\x02\xF0\x80" + hdr + params + data)
        while True:
            pkt = await self._recv_tpkt()
            s7 = pkt[3:]                    # skip COTP DT header
            if len(s7) < 12 or s7[0] != 0x32:
                raise S7Error("bad S7 reply")
            rosctr = s7[1]
            pref = struct.unpack(">H", s7[4:6])[0]
            plen, dlen = struct.unpack(">HH", s7[6:10])
            if rosctr == 3:
                ecls, ecode = s7[10], s7[11]
                if ecls or ecode:
                    raise S7Error(f"S7 error class 0x{ecls:02X} code 0x{ecode:02X}")
                body = s7[12:]
            else:
                body = s7[10:]
            if pref != self.ref:
                continue
            return body[:plen], body[plen: plen + dlen]

    @staticmethod
    def _item_spec(area, db, start, count, bit=-1):
        if bit >= 0:   # single bit
            ts, addr = 0x01, start * 8 + bit
        else:
            ts, addr = 0x02, start * 8
        if area in (0x1C, 0x1D):      # timers/counters: element addressing, 2 bytes each
            ts, addr, count = area, start // 2, max(1, count // 2)
        return bytes([0x12, 0x0A, 0x10, ts]) + struct.pack(">HHB", count, db, area) + addr.to_bytes(3, "big")

    def _pack(self, blocks: list[Block]):
        """Split blocks into multi-item requests that fit the PDU (req & resp)."""
        reqs, cur, req_sz, resp_sz = [], [], 12 + 2, 12 + 2
        limit = self.pdu
        for b in blocks:
            # split oversized blocks
            max_data = limit - 18 - 4
            pieces = []
            off = 0
            while off < b.size:
                n = min(b.size - off, max_data)
                pieces.append((b, b.start + off, n))
                off += n
            for p in pieces:
                if cur and (len(cur) >= 20 or req_sz + 12 > limit or resp_sz + 4 + p[2] + 1 > limit):
                    reqs.append(cur)
                    cur, req_sz, resp_sz = [], 14, 14
                cur.append(p)
                req_sz += 12
                resp_sz += 4 + p[2] + (p[2] & 1)
        if cur:
            reqs.append(cur)
        return reqs

    async def _read_multi(self, pieces):
        params = bytes([0x04, len(pieces)]) + b"".join(
            self._item_spec(b.area, b.db, start, n) for b, start, n in pieces)
        _, data = await self._job(params, b"")
        out, i = [], 0
        for k in range(len(pieces)):
            rc, ts = data[i], data[i + 1]
            ln = struct.unpack(">H", data[i + 2: i + 4])[0]
            if ts in (0x03, 0x04, 0x05):
                ln = (ln + 7) // 8
            payload = data[i + 4: i + 4 + ln]
            out.append((rc, payload))
            i += 4 + ln + (ln & 1 if k < len(pieces) - 1 else 0)
        return out

    # --------------------------------------------------------------- driver API
    def build_plan(self, tags):
        blocks = build_blocks([(t, t.item) for t in tags], self.max_gap, max(32, self.pdu - 22))
        return blocks, self._pack(blocks)

    async def execute(self, plan):
        blocks, reqs = plan
        buf: dict[int, bytearray] = {id(b): bytearray(b.size) for b in blocks}
        bad: dict[int, str] = {}
        for pieces in reqs:
            results = await self._read_multi(pieces)
            for (b, start, n), (rc, payload) in zip(pieces, results):
                if rc != 0xFF:
                    bad[id(b)] = RET.get(rc, f"return code 0x{rc:02X}")
                    continue
                off = start - b.start
                buf[id(b)][off: off + len(payload)] = payload
        upd = self.engine.update
        for b in blocks:
            err = bad.get(id(b))
            data = buf[id(b)]
            for tag, it in b.items:
                if err:
                    tag.error = err
                    upd(tag, None, BAD_CONFIG)
                    continue
                o = it.start - b.start
                try:
                    upd(tag, decode(it, bytes(data[o: o + it.size])), GOOD)
                except Exception as e:
                    tag.error = f"decode: {e}"
                    upd(tag, None, BAD_CONFIG)

    async def write(self, tag: Tag, value):
        it: S7Item = tag.item
        if it.dtype == "BOOL":
            spec = self._item_spec(it.area, it.db, it.start, 1, it.bit if it.bit >= 0 else 0)
            data = bytes([0x00, 0x03, 0x00, 0x01, 1 if to_bool(value) else 0])
        else:
            raw = encode(it, value)
            spec = self._item_spec(it.area, it.db, it.start, len(raw))
            data = bytes([0x00, 0x04]) + struct.pack(">H", len(raw) * 8) + raw
        async with self.lock:
            _, rdata = await self._job(bytes([0x05, 0x01]) + spec, data)
        if rdata and rdata[0] != 0xFF:
            raise S7Error(f"write failed: {RET.get(rdata[0], hex(rdata[0]))}")
        self.stats["writes"] += 1
        self.poll_soon()

    def status(self):
        st = super().status()
        st["pdu"] = self.pdu
        if self._plans:
            st["requests_per_scan"] = sum(len(p[1]) for p in self._plans.values())
        return st

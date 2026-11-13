"""Modbus TCP / RTU-over-TCP / RTU serial master (native asyncio, no dependencies).

Addressing (1-based, Modicon style)::

    40001         holding register 1 (INT16)      400001 = 6-digit form
    30001         input register 1                10001  = discrete input 1
    00001         coil 1                          HR1 / IR1 / DI1 / C1 = prefix forms
    40001.3       bit 3 of HR1
    40001:F       FLOAT32 (2 regs)   :D  FLOAT64   :I INT16 (default)  :U UINT16
    40001:DI      INT32   :DU UINT32  :I64 / :U64   :S20 string of 20 chars
    40001:F:SW    word swap  (:BS byte swap)       ...@1000  scan every 1000 ms

Device options (project.nll): host, port=502, unit=1, framing=tcp|rtu, serial=COM3,
baud=9600, parity=N, stopbits=1, word_swap=false, byte_swap=false, timeout_ms=1000,
poll_ms=500, max_gap=16, max_regs=125, max_bits=2000, max_inflight=1, mask_write=false

Direct addressing: ``modbus:[host[:port][,unit][,rtu]]40001`` or ``modbus:[COM3:9600:8N1,1]40001``

Performance: tags are merged into the fewest possible block reads (gaps up to
``max_gap`` registers are read through), and with ``max_inflight>1`` blocks are
pipelined over one TCP connection using transaction ids.
"""
from __future__ import annotations

import asyncio
import re
import struct
from dataclasses import dataclass, field

from ..address import RefError
from ..engine import BAD_CONFIG, GOOD, Tag, to_bool, to_number
from .base import PollingDriver

TABLES = {"0": "coil", "1": "di", "3": "ir", "4": "hr"}
PREFIX = {"C": "coil", "DI": "di", "IR": "ir", "HR": "hr", "MW": "hr"}
READ_FC = {"coil": 1, "di": 2, "hr": 3, "ir": 4}

TYPES = {  # name -> (registers, struct code)
    "I": (1, "h"), "INT": (1, "h"), "INT16": (1, "h"),
    "U": (1, "H"), "UINT": (1, "H"), "UINT16": (1, "H"), "W": (1, "H"),
    "DI": (2, "i"), "L": (2, "i"), "INT32": (2, "i"), "DINT": (2, "i"),
    "DU": (2, "I"), "UL": (2, "I"), "UINT32": (2, "I"), "UDINT": (2, "I"),
    "F": (2, "f"), "R": (2, "f"), "REAL": (2, "f"), "FLOAT": (2, "f"),
    "D": (4, "d"), "LREAL": (4, "d"), "F64": (4, "d"), "DOUBLE": (4, "d"),
    "I64": (4, "q"), "U64": (4, "Q"),
}

_ADDR = re.compile(r"^(?:(?P<pre>HR|IR|DI|C|MW)(?P<pnum>\d+)|(?P<num>\d{1,6}))(?:\.(?P<bit>\d{1,2}))?"
                   r"(?P<opts>(?::[A-Za-z0-9]+)*)(?:@(?P<rate>\d+))?$", re.I)


@dataclass(eq=False)
class MbItem:
    table: str
    offset: int          # 0-based
    count: int           # registers or bits
    dtype: str = "I"
    code: str = "h"
    bit: int | None = None
    strlen: int = 0
    word_swap: bool = False
    byte_swap: bool = False
    rate: int | None = None


def parse_address(addr: str, dev_ws=False, dev_bs=False, zero_based=False) -> MbItem:
    m = _ADDR.match(addr.strip())
    if not m:
        raise RefError(f"bad Modbus address '{addr}'")
    if m.group("pre"):
        table = PREFIX[m.group("pre").upper()]
        num = int(m.group("pnum"))
    else:
        s = m.group("num")
        if len(s) <= 4:
            s = s.rjust(5, "0")
        table = TABLES.get(s[0])
        if table is None:
            raise RefError(f"bad Modbus table digit in '{addr}' (use 0,1,3,4)")
        num = int(s[1:])
    offset = num if zero_based else num - 1
    if offset < 0 or offset > 65535:
        raise RefError(f"Modbus address out of range: '{addr}'")
    opts = [o for o in (m.group("opts") or "").upper().split(":") if o]
    ws, bs = dev_ws, dev_bs
    dtype, regs, code, strlen = "I", 1, "h", 0
    bit = int(m.group("bit")) if m.group("bit") is not None else None
    for o in opts:
        if o == "SW":
            ws = True
        elif o == "BS":
            bs = True
        elif o in ("NOSW",):
            ws = False
        elif o in TYPES:
            dtype = o
            regs, code = TYPES[o]
        elif re.fullmatch(r"(S|STR)\d+", o):
            dtype = "S"
            strlen = int(re.sub(r"\D", "", o))
            regs, code = (strlen + 1) // 2, ""
        elif o in ("B", "BOOL"):
            dtype = "B"
        else:
            raise RefError(f"unknown Modbus option ':{o}' in '{addr}'")
    if table in ("coil", "di"):
        dtype, regs, code, bit = "BIT", 1, "", None
    if bit is not None:
        if not 0 <= bit <= 15:
            raise RefError("register bit must be 0..15")
        dtype, regs = "RBIT", 1
    return MbItem(table, offset, regs, dtype, code, bit, strlen, ws, bs,
                  int(m.group("rate")) if m.group("rate") else None)


@dataclass
class Block:
    table: str
    start: int
    count: int
    items: list = field(default_factory=list)   # (tag, item)


def build_blocks(pairs, max_gap=16, max_regs=125, max_bits=2000) -> list[Block]:
    """Merge items into contiguous blocks (fewest requests)."""
    by_table: dict[str, list] = {}
    for tag, it in pairs:
        by_table.setdefault(it.table, []).append((tag, it))
    blocks = []
    for table, lst in by_table.items():
        lst.sort(key=lambda p: p[1].offset)
        limit = max_bits if table in ("coil", "di") else max_regs
        gap = max_gap * (8 if table in ("coil", "di") else 1)
        cur = None
        for tag, it in lst:
            end = it.offset + it.count
            if cur and it.offset - (cur.start + cur.count) <= gap and end - cur.start <= limit:
                cur.count = max(cur.count, end - cur.start)
                cur.items.append((tag, it))
            else:
                cur = Block(table, it.offset, it.count, [(tag, it)])
                blocks.append(cur)
    return blocks


def decode(it: MbItem, data, base: int):
    """data: bytes of registers (big endian) or list[bool] for bit tables."""
    if it.dtype == "BIT":
        return data[it.offset - base]
    i = (it.offset - base) * 2
    raw = data[i: i + it.count * 2]
    if it.dtype == "RBIT":
        return bool((struct.unpack(">H", raw)[0] >> it.bit) & 1)
    if it.byte_swap:
        raw = b"".join(raw[k + 1: k + 2] + raw[k: k + 1] for k in range(0, len(raw), 2))
    if it.word_swap and it.count > 1:
        raw = b"".join(raw[k: k + 2] for k in range(len(raw) - 2, -2, -2))
    if it.dtype == "S":
        return raw[: it.strlen].split(b"\0", 1)[0].decode("latin-1", "replace").rstrip()
    if it.dtype == "B":
        return any(raw)
    v = struct.unpack(">" + it.code, raw)[0]
    if isinstance(v, float):
        v = round(v, 7) if abs(v) < 1e15 else v
    return v


def encode(it: MbItem, value) -> bytes:
    if it.dtype == "S":
        b = str(value).encode("latin-1", "replace")[: it.strlen].ljust(it.count * 2, b"\0")
        raw = b
    elif it.dtype == "B":
        raw = struct.pack(">H", 1 if to_bool(value) else 0)
    else:
        n = to_number(value)
        raw = struct.pack(">" + it.code, n if it.code in "fd" else int(round(n)))
    if it.word_swap and it.count > 1:
        raw = b"".join(raw[k: k + 2] for k in range(len(raw) - 2, -2, -2))
    if it.byte_swap:
        raw = b"".join(raw[k + 1: k + 2] + raw[k: k + 1] for k in range(0, len(raw), 2))
    return raw


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class ModbusError(Exception):
    EXC = {1: "illegal function", 2: "illegal data address", 3: "illegal data value",
           4: "device failure", 6: "device busy", 10: "gateway path unavailable",
           11: "gateway target failed to respond"}

    def __init__(self, code):
        super().__init__(f"Modbus exception {code}: {self.EXC.get(code, '?')}")
        self.code = code


# ----------------------------------------------------------------------------- transports
class TcpTransport:
    def __init__(self, host, port, timeout):
        self.host, self.port, self.timeout = host, port, timeout
        self.reader = self.writer = None
        self.tid = 0
        self.pending: dict[int, asyncio.Future] = {}
        self.rx_task = None

    async def open(self):
        self.reader, self.writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), self.timeout)
        sock = self.writer.get_extra_info("socket")
        if sock is not None:
            import socket
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.rx_task = asyncio.get_running_loop().create_task(self._rx())

    async def _rx(self):
        try:
            while True:
                hdr = await self.reader.readexactly(7)
                tid, _pid, length, _unit = struct.unpack(">HHHB", hdr)
                pdu = await self.reader.readexactly(length - 1)
                fut = self.pending.pop(tid, None)
                if fut and not fut.done():
                    fut.set_result(pdu)
        except Exception as e:
            for fut in self.pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError(f"connection lost: {e}"))
            self.pending.clear()

    async def request(self, unit: int, pdu: bytes) -> bytes:
        if self.writer is None or self.rx_task.done():
            raise ConnectionError("not connected")
        self.tid = (self.tid + 1) & 0xFFFF
        tid = self.tid
        fut = asyncio.get_running_loop().create_future()
        self.pending[tid] = fut
        self.writer.write(struct.pack(">HHHB", tid, 0, len(pdu) + 1, unit) + pdu)
        try:
            return await asyncio.wait_for(fut, self.timeout)
        except asyncio.TimeoutError:
            self.pending.pop(tid, None)
            raise ConnectionError("timeout")

    async def close(self):
        if self.rx_task:
            self.rx_task.cancel()
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
        self.writer = None


class RtuTcpTransport(TcpTransport):
    """RTU framing over a TCP socket (serial gateways in transparent mode)."""

    async def open(self):
        self.reader, self.writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), self.timeout)
        self.lock = asyncio.Lock()

    async def request(self, unit, pdu):
        frame = bytes([unit]) + pdu
        frame += struct.pack("<H", crc16(frame))
        async with self.lock:
            self.writer.write(frame)
            try:
                return await asyncio.wait_for(self._read_frame(), self.timeout)
            except asyncio.TimeoutError:
                raise ConnectionError("timeout")

    async def _read_frame(self):
        hdr = await self.reader.readexactly(3)
        fc = hdr[1]
        rest = await self.reader.readexactly(_rtu_rest(fc, hdr[2]))
        frame = hdr + rest
        if crc16(frame[:-2]) != struct.unpack("<H", frame[-2:])[0]:
            raise ConnectionError("CRC error")
        return frame[1:-2]

    async def close(self):
        if self.writer:
            self.writer.close()
        self.writer = None


def _rtu_rest(fc: int, third: int) -> int:
    """Remaining bytes after the first 3 bytes (unit, fc, third)."""
    if fc & 0x80:
        return 2                 # third = exception code, then CRC
    if fc in (1, 2, 3, 4):
        return third + 2         # third = byte count
    if fc == 22:
        return 7                 # mask write echo: addr(2) and(2) or(2) crc(2), 1st addr byte read
    return 5                     # FC5/6/15/16 echo: addr(2) value(2) crc(2), 1st addr byte read


class SerialTransport:
    """Modbus RTU over a serial port (requires pyserial)."""

    def __init__(self, port, baud, parity, stopbits, timeout):
        self.port, self.baud, self.parity, self.stopbits, self.timeout = port, baud, parity, stopbits, timeout
        self.ser = None
        self.lock = asyncio.Lock()

    async def open(self):
        try:
            import serial  # type: ignore
        except ImportError:
            raise ConnectionError("pyserial not installed (pip install pyserial)")
        loop = asyncio.get_running_loop()
        self.ser = await loop.run_in_executor(None, lambda: serial.Serial(
            self.port, self.baud, parity=self.parity, stopbits=self.stopbits, bytesize=8, timeout=self.timeout))

    def _xfer(self, frame):
        import time
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        hdr = self.ser.read(3)
        if len(hdr) < 3:
            raise ConnectionError("timeout")
        rest = self.ser.read(_rtu_rest(hdr[1], hdr[2]))
        time.sleep(max(0.002, 3.5 * 11 / self.baud))   # inter-frame gap
        out = hdr + rest
        if len(out) < 5 or crc16(out[:-2]) != struct.unpack("<H", out[-2:])[0]:
            raise ConnectionError("CRC error / short frame")
        return out[1:-2]

    async def request(self, unit, pdu):
        frame = bytes([unit]) + pdu
        frame += struct.pack("<H", crc16(frame))
        async with self.lock:
            return await asyncio.get_running_loop().run_in_executor(None, self._xfer, frame)

    async def close(self):
        if self.ser:
            self.ser.close()
        self.ser = None


# ----------------------------------------------------------------------------- driver
class ModbusDriver(PollingDriver):
    kind = "modbus"

    def __init__(self, engine, name, cfg):
        cfg = dict(cfg or {})
        if "direct" in cfg:
            cfg.update(self._parse_direct(cfg["direct"]))
        super().__init__(engine, name, cfg)
        self.unit = int(cfg.get("unit", 1))
        self.timeout = float(cfg.get("timeout_ms", 1000)) / 1000
        self.word_swap = bool(cfg.get("word_swap", False))
        self.byte_swap = bool(cfg.get("byte_swap", False))
        self.zero_based = bool(cfg.get("zero_based", False))
        self.max_gap = int(cfg.get("max_gap", 16))
        self.max_regs = min(125, int(cfg.get("max_regs", 125)))
        self.max_bits = min(2000, int(cfg.get("max_bits", 2000)))
        self.inflight = max(1, int(cfg.get("max_inflight", 1)))
        self.mask_write = bool(cfg.get("mask_write", False))
        self.transport = None

    @staticmethod
    def _parse_direct(s: str) -> dict:
        parts = [p.strip() for p in s.split(",")]
        out: dict = {}
        target = parts[0]
        if target.upper().startswith("COM") or target.startswith("/dev/"):
            sp = target.split(":")
            out["serial"] = sp[0]
            if len(sp) > 1:
                out["baud"] = int(sp[1])
            if len(sp) > 2 and len(sp[2]) == 3:
                out["parity"], out["stopbits"] = sp[2][1].upper(), int(sp[2][2])
        else:
            host, _, port = target.partition(":")
            out["host"], out["port"] = host, int(port or 502)
        for p in parts[1:]:
            if p.isdigit():
                out["unit"] = int(p)
            elif p.lower() in ("rtu", "tcp"):
                out["framing"] = p.lower()
        return out

    def parse(self, tag: Tag):
        return parse_address(tag.parsed.address, self.word_swap, self.byte_swap, self.zero_based)

    async def connect(self):
        c = self.cfg
        if c.get("serial"):
            t = SerialTransport(c["serial"], int(c.get("baud", 9600)), c.get("parity", "N"),
                                int(c.get("stopbits", 1)), self.timeout)
            self.inflight = 1
        elif c.get("framing", "tcp") == "rtu":
            t = RtuTcpTransport(c.get("host", "127.0.0.1"), int(c.get("port", 502)), self.timeout)
            self.inflight = 1
        else:
            t = TcpTransport(c.get("host", "127.0.0.1"), int(c.get("port", 502)), self.timeout)
        await t.open()
        self.transport = t

    async def disconnect(self):
        if self.transport:
            await self.transport.close()
        self.transport = None

    def build_plan(self, tags):
        return build_blocks([(t, t.item) for t in tags], self.max_gap, self.max_regs, self.max_bits)

    async def _pdu(self, pdu: bytes) -> bytes:
        resp = await self.transport.request(self.unit, pdu)
        if resp[0] & 0x80:
            raise ModbusError(resp[1])
        return resp

    async def _read_block(self, b: Block):
        fc = READ_FC[b.table]
        try:
            resp = await self._pdu(struct.pack(">BHH", fc, b.start, b.count))
        except ModbusError as e:
            for tag, _ in b.items:
                tag.error = str(e)
                self.engine.update(tag, None, BAD_CONFIG)
            return
        payload = resp[2:]
        if fc in (1, 2):
            data = [bool((payload[i // 8] >> (i % 8)) & 1) for i in range(b.count)]
        else:
            data = payload
        upd = self.engine.update
        for tag, it in b.items:
            try:
                upd(tag, decode(it, data, b.start), GOOD)
            except Exception as e:
                tag.error = f"decode: {e}"
                upd(tag, None, BAD_CONFIG)

    async def execute(self, plan):
        if self.inflight <= 1:
            for b in plan:
                await self._read_block(b)
        else:
            sem = asyncio.Semaphore(self.inflight)

            async def run(b):
                async with sem:
                    await self._read_block(b)
            await asyncio.gather(*(run(b) for b in plan))

    async def write(self, tag: Tag, value):
        it: MbItem = tag.item
        if it.table in ("di", "ir"):
            raise PermissionError("input tables are read-only")
        async with self.lock:
            if self.transport is None:
                raise ConnectionError("not connected")
            if it.table == "coil":
                await self._pdu(struct.pack(">BHH", 5, it.offset, 0xFF00 if to_bool(value) else 0))
            elif it.dtype == "RBIT":
                if self.mask_write:
                    and_mask = 0xFFFF ^ (1 << it.bit)
                    or_mask = (1 << it.bit) if to_bool(value) else 0
                    await self._pdu(struct.pack(">BHHH", 22, it.offset, and_mask, or_mask))
                else:  # read-modify-write
                    r = await self._pdu(struct.pack(">BHH", 3, it.offset, 1))
                    cur = struct.unpack(">H", r[2:4])[0]
                    new = cur | (1 << it.bit) if to_bool(value) else cur & ~(1 << it.bit)
                    await self._pdu(struct.pack(">BHH", 6, it.offset, new))
            else:
                raw = encode(it, value)
                if it.count == 1 and not self.cfg.get("always_fc16"):
                    await self._pdu(struct.pack(">BH", 6, it.offset) + raw)
                else:
                    await self._pdu(struct.pack(">BHHB", 16, it.offset, it.count, len(raw)) + raw)
        self.stats["writes"] += 1
        self.poll_soon()

    async def browse(self, path=""):
        return [{"name": f"{t.parsed.address}", "ref": t.ref, "value": t.value} for t in self.tags]

    def status(self):
        st = super().status()
        st["blocks"] = sum(len(p) for p in self._plans.values()) if self._plans else 0
        return st

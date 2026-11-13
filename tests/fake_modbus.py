"""Minimal Modbus TCP slave used by the tests (FC1-6, 15, 16, 22)."""
from __future__ import annotations

import asyncio
import struct


class FakeModbus:
    def __init__(self):
        self.hr = [0] * 2000
        self.ir = [0] * 2000
        self.coils = [False] * 2000
        self.di = [False] * 2000
        self.requests = 0
        self.server = None

    async def start(self, port=0):
        self.server = await asyncio.start_server(self._client, "127.0.0.1", port)
        return self.server.sockets[0].getsockname()[1]

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    async def _client(self, r, w):
        try:
            while True:
                hdr = await r.readexactly(7)
                tid, pid, ln, unit = struct.unpack(">HHHB", hdr)
                pdu = await r.readexactly(ln - 1)
                self.requests += 1
                resp = self.handle(pdu)
                w.write(struct.pack(">HHHB", tid, 0, len(resp) + 1, unit) + resp)
                await w.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            w.close()

    def handle(self, pdu: bytes) -> bytes:
        fc = pdu[0]
        if fc in (1, 2):
            start, cnt = struct.unpack(">HH", pdu[1:5])
            src = self.coils if fc == 1 else self.di
            bits = src[start:start + cnt]
            out = bytearray((cnt + 7) // 8)
            for i, b in enumerate(bits):
                if b:
                    out[i // 8] |= 1 << (i % 8)
            return bytes([fc, len(out)]) + bytes(out)
        if fc in (3, 4):
            start, cnt = struct.unpack(">HH", pdu[1:5])
            src = self.hr if fc == 3 else self.ir
            if start + cnt > len(src):
                return bytes([fc | 0x80, 2])
            regs = src[start:start + cnt]
            return bytes([fc, cnt * 2]) + struct.pack(f">{cnt}H", *regs)
        if fc == 5:
            a, v = struct.unpack(">HH", pdu[1:5])
            self.coils[a] = v == 0xFF00
            return pdu
        if fc == 6:
            a, v = struct.unpack(">HH", pdu[1:5])
            self.hr[a] = v
            return pdu
        if fc == 16:
            a, cnt, _bc = struct.unpack(">HHB", pdu[1:6])
            self.hr[a:a + cnt] = struct.unpack(f">{cnt}H", pdu[6:6 + cnt * 2])
            return pdu[:5]
        if fc == 22:
            a, andm, orm = struct.unpack(">HHH", pdu[1:7])
            self.hr[a] = (self.hr[a] & andm) | (orm & ~andm & 0xFFFF)
            return pdu
        return bytes([fc | 0x80, 1])

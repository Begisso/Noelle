"""Driver tests: Modbus against an in-process fake slave, S7 against the
python-snap7 server (pip install python-snap7). Run: python -m tests.test_drivers"""
from __future__ import annotations

import asyncio
import struct
import sys
import time

sys.path.insert(0, ".")

from noelle.address import parse_ref  # noqa: E402
from noelle.drivers import modbus, s7  # noqa: E402
from noelle.drivers import register_all  # noqa: E402
from noelle.engine import GOOD, TagEngine  # noqa: E402
from tests.fake_modbus import FakeModbus  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL") + " " + msg)
    if not cond:
        FAILS.append(msg)


async def wait_value(tag, pred, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if tag.quality == GOOD and pred(tag.value):
            return True
        await asyncio.sleep(0.02)
    return False


def test_parsing():
    print("address parsing")
    r = parse_ref("modbus:[10.0.0.5:5020,3]40001:F:SW@1000")
    check(r.driver == "modbus" and r.device == "[10.0.0.5:5020,3]" and r.address == "40001:F:SW@1000", "modbus direct ref")
    r = parse_ref("opcua:[opc.tcp://h:4840]ns=2;s=A/B")
    check(r.device == "[opc.tcp://h:4840]" and r.address == "ns=2;s=A/B", "opcua direct ref with slash")
    check(parse_ref("Tank1.Level").driver == "tag", "bare name -> tag db")
    check(parse_ref("mem:X=5").raw == "mem:X", "mem default canonical")
    it = modbus.parse_address("40010:F")
    check((it.table, it.offset, it.count, it.code) == ("hr", 9, 2, "f"), "40010:F")
    it = modbus.parse_address("400010.7")
    check((it.table, it.offset, it.dtype, it.bit) == ("hr", 9, "RBIT", 7), "6-digit + bit")
    it = modbus.parse_address("IR5:DU")
    check((it.table, it.offset, it.count) == ("ir", 4, 2), "IR5:DU")
    for a, exp in {"DB10.DBX4.3": (0x84, 10, 4, 3, "BOOL"), "DB1.DBD0": (0x84, 1, 0, -1, "REAL"),
                   "DB1.DBD8:DINT": (0x84, 1, 8, -1, "DINT"), "MW10": (0x83, 0, 10, -1, "INT"),
                   "E0.1": (0x81, 0, 0, 1, "BOOL"), "QB2": (0x82, 0, 2, -1, "BYTE"),
                   "DB2,REAL12": (0x84, 2, 12, -1, "REAL"), "DB2,X3.1": (0x84, 2, 3, 1, "BOOL")}.items():
        i = s7.parse_address(a)
        check((i.area, i.db, i.start, i.bit, i.dtype) == exp, f"s7 {a}")
    b = modbus.build_blocks([(None, modbus.parse_address(a)) for a in ("40001", "40003:F", "40100", "40300")], 16, 125)
    check([(x.start, x.count) for x in b] == [(0, 4), (99, 1), (299, 1)], f"modbus block merge {[(x.start, x.count) for x in b]}")


async def test_modbus():
    print("modbus driver")
    fake = FakeModbus()
    port = await fake.start()
    struct_f = struct.pack(">f", 12.5)
    fake.hr[0] = 1234
    fake.hr[1], fake.hr[2] = struct.unpack(">HH", struct_f)
    fake.hr[10] = 0b1000
    fake.coils[4] = True
    fake.ir[0] = 65535
    eng = TagEngine()
    eng.loop = asyncio.get_running_loop()
    register_all(eng)
    dev = f"[127.0.0.1:{port},1]"
    t1 = eng.acquire(f"modbus:{dev}40001")
    t2 = eng.acquire(f"modbus:{dev}40002:F")
    t3 = eng.acquire(f"modbus:{dev}40011.3")
    t4 = eng.acquire(f"modbus:{dev}00005")
    t5 = eng.acquire(f"modbus:{dev}30001:U")
    check(await wait_value(t1, lambda v: v == 1234), f"HR INT16 = {t1.value}")
    check(await wait_value(t2, lambda v: abs(v - 12.5) < 1e-6), f"HR FLOAT = {t2.value}")
    check(await wait_value(t3, lambda v: v is True), f"HR bit = {t3.value}")
    check(await wait_value(t4, lambda v: v is True), f"coil = {t4.value}")
    check(await wait_value(t5, lambda v: v == 65535), f"IR UINT16 = {t5.value}")
    await eng.write(t2.ref, 99.25)
    check(await wait_value(t2, lambda v: abs(v - 99.25) < 1e-6), f"write float read-back = {t2.value}")
    await eng.write(t3.ref, False)
    check(await wait_value(t3, lambda v: v is False) and fake.hr[10] == 0, "write register bit (RMW)")
    await eng.write(t4.ref, 0)
    check(await wait_value(t4, lambda v: v is False), "write coil")
    drv = t1.driver
    check(drv.status()["blocks"] == 3, f"3 blocks for 5 tags (hr, coil, ir): {drv.status()['blocks']}")
    await eng.stop()
    await fake.stop()


async def test_s7():
    print("s7 driver (snap7 server)")
    try:
        import snap7
        from snap7.type import SrvArea
    except Exception as e:
        print("  skip (python-snap7 not installed)", e)
        return
    srv = snap7.Server(log=False)
    db1 = bytearray(64)
    mk = bytearray(64)
    struct.pack_into(">f", db1, 0, 3.75)
    struct.pack_into(">h", db1, 4, -42)
    struct.pack_into(">i", db1, 6, 100000)
    db1[10] = 0b00000101
    struct.pack_into(">BB5s", db1, 20, 10, 5, b"HELLO")
    struct.pack_into(">H", mk, 10, 777)
    srv.register_area(SrvArea.DB, 1, db1)      # snap7 3.x copies the buffer
    srv.register_area(SrvArea.MK, 0, mk)
    port = 11102
    srv.start(tcp_port=port)
    await asyncio.sleep(0.3)
    try:
        eng = TagEngine()
        eng.loop = asyncio.get_running_loop()
        register_all(eng)
        dev = f"[127.0.0.1:{port},0,1]"
        a = eng.acquire(f"s7:{dev}DB1.DBD0")
        b = eng.acquire(f"s7:{dev}DB1.DBW4")
        c = eng.acquire(f"s7:{dev}DB1.DBD6:DINT")
        d = eng.acquire(f"s7:{dev}DB1.DBX10.2")
        e = eng.acquire(f"s7:{dev}DB1.DBB20:STRING10")
        f = eng.acquire(f"s7:{dev}MW10:WORD")
        g = eng.acquire(f"s7:{dev}DB1.DBX10.0")
        check(await wait_value(a, lambda v: abs(v - 3.75) < 1e-6), f"REAL = {a.value} ({a.error})")
        check(await wait_value(b, lambda v: v == -42), f"INT = {b.value}")
        check(await wait_value(c, lambda v: v == 100000), f"DINT = {c.value}")
        check(await wait_value(d, lambda v: v is True), f"BOOL = {d.value}")
        check(await wait_value(e, lambda v: v == "HELLO"), f"STRING = {e.value!r}")
        check(await wait_value(f, lambda v: v == 777), f"MW = {f.value}")
        st = a.driver.status()
        check(st.get("requests_per_scan") == 1, f"one multi-item request per scan: {st.get('requests_per_scan')} pdu={st.get('pdu')}")
        await eng.write(a.ref, -1.5)
        check(await wait_value(a, lambda v: abs(v + 1.5) < 1e-6), f"write REAL read-back = {a.value}")
        await eng.write(d.ref, False)
        check(await wait_value(d, lambda v: v is False), "write single bit")
        # snap7's emulator ignores the bit offset of BIT writes; check our frame is the standard one
        spec = s7.S7Driver._item_spec(0x84, 1, 10, 1, 2)
        check(spec == bytes.fromhex("120a100100010001840000" + "52"), f"BIT write item spec {spec.hex()}")
        await eng.write(e.ref, "ABC")
        check(await wait_value(e, lambda v: v == "ABC"), f"write STRING = {e.value!r}")
        await eng.stop()
    finally:
        srv.stop()
        srv.destroy()


async def main():
    test_parsing()
    await test_modbus()
    await test_s7()
    print("FAILED: %d" % len(FAILS) if FAILS else "ALL PASSED")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

"""Third live test: send incline, WAIT for the pad to confirm it, then correct speed.
Speed targets are written in 0.01 mph (pad quirk); reported speed is 0.01 km/h.
Safety: abort (STOP) if speed exceeds HARD_LIMIT_MPH at any point, or won't come back down."""
import asyncio
import sys
import struct
import time
from bleak import BleakClient, BleakScanner

if len(sys.argv) < 2:
    sys.exit("usage: python live_test.py <bluetooth address from scan.py>")
ADDR = sys.argv[1]
CP = "00002ad9-0000-1000-8000-00805f9b34fb"
TDATA = "00002acd-0000-1000-8000-00805f9b34fb"
STATUS = "00002ada-0000-1000-8000-00805f9b34fb"
KMH_PER_MPH = 1.609344
TARGET_MPH = 1.0
HARD_LIMIT_MPH = 1.9
T0 = time.monotonic()
replies: asyncio.Queue = asyncio.Queue()
state = {"mph": None, "incline": None, "status_incline": None}
OPS = {0: "REQUEST_CONTROL", 2: "SET_SPEED", 3: "SET_INCLINE", 7: "START", 8: "STOP"}
RES = {1: "success", 2: "not supported", 3: "invalid param", 4: "failed", 5: "control not permitted"}


def log(m):
    print(f"[{time.monotonic() - T0:6.1f}s] {m}", flush=True)


def on_tdata(_, data):
    d = bytes(data)
    flags = struct.unpack_from("<H", d, 0)[0]
    if flags & 0x0008 == 0:
        return
    kmh = struct.unpack_from("<H", d, 2)[0] / 100
    inc = struct.unpack_from("<h", d, 7)[0] / 10
    mph = round(kmh / KMH_PER_MPH, 2)
    if (mph, inc) != (state["mph"], state["incline"]):
        log(f"belt: {mph:.2f} mph ({kmh:.2f} km/h), incline {inc:.0f}%")
    state["mph"], state["incline"] = mph, inc


def on_status(_, data):
    d = bytes(data)
    log(f"  status {d.hex()}")
    if d[:1] == b"\x06" and len(d) >= 3:
        state["status_incline"] = struct.unpack_from("<h", d, 1)[0] / 10


def on_cp(_, data):
    d = bytes(data)
    if len(d) >= 3 and d[0] == 0x80:
        log(f"  reply {OPS.get(d[1], d[1])} -> {RES.get(d[2], d[2])}")
    replies.put_nowait(d)


async def cmd(c, payload, label):
    while not replies.empty():
        replies.get_nowait()
    log(f"SEND {label}")
    await c.write_gatt_char(CP, payload, response=True)
    try:
        await asyncio.wait_for(replies.get(), 5)
    except asyncio.TimeoutError:
        log("  (no reply)")


def speed_cmd(mph):
    return b"\x02" + struct.pack("<H", round(mph * 100))


def check_hard_limit():
    if state["mph"] is not None and state["mph"] > HARD_LIMIT_MPH:
        raise RuntimeError(f"speed {state['mph']} mph over hard limit, aborting")


async def set_incline(c, pct):
    state["status_incline"] = None
    t = time.monotonic()
    await cmd(c, b"\x03" + struct.pack("<h", round(pct * 10)), f"SET_INCLINE {pct}%")
    while time.monotonic() - t < 8:
        await asyncio.sleep(0.1)
        check_hard_limit()
        if state["status_incline"] == pct or state["incline"] == pct:
            log(f"  incline confirmed after {time.monotonic() - t:.1f}s")
            break
    else:
        log("  incline NOT confirmed within 8s")
    await cmd(c, speed_cmd(TARGET_MPH), f"SET_SPEED {TARGET_MPH} mph (correction)")


async def watch(c, seconds):
    corrections = 0
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        await asyncio.sleep(0.25)
        check_hard_limit()
        if state["mph"] is not None and state["mph"] > TARGET_MPH + 0.15:
            corrections += 1
            if corrections > 2:
                raise RuntimeError(f"speed stuck at {state['mph']} mph, aborting")
            log(f"!! watchdog: {state['mph']} mph > target, resending speed")
            await cmd(c, speed_cmd(TARGET_MPH), f"SET_SPEED {TARGET_MPH} mph (watchdog)")
            await asyncio.sleep(2)


async def connect():
    for attempt in range(1, 5):
        try:
            dev = await BleakScanner.find_device_by_address(ADDR, timeout=15.0)
            if dev:
                c = BleakClient(dev, timeout=45.0)
                await c.connect()
                log(f"connected (attempt {attempt})")
                return c
        except Exception as e:
            log(f"attempt {attempt} failed: {type(e).__name__} {e}")
    raise SystemExit("could not connect")


async def main():
    c = await connect()
    try:
        for ch, fn in ((CP, on_cp), (TDATA, on_tdata), (STATUS, on_status)):
            await c.start_notify(ch, fn)
        await cmd(c, b"\x07", "START")
        await asyncio.sleep(8)
        await cmd(c, speed_cmd(TARGET_MPH), f"SET_SPEED {TARGET_MPH} mph")
        await asyncio.sleep(6)
        log("=== incline 1%, wait for confirm, then correct speed ===")
        await set_incline(c, 1)
        await watch(c, 10)
        log("=== incline 0%, wait for confirm, then correct speed ===")
        await set_incline(c, 0)
        await watch(c, 10)
    except Exception as e:
        log(f"!!! {e}")
    finally:
        try:
            await cmd(c, b"\x08\x01", "STOP")
            await asyncio.sleep(4)
        except Exception as e:
            log(f"!!! STOP failed: {e} -- use the remote")
        await c.disconnect()
        log("disconnected")


asyncio.run(main())


"""Bluetooth Fitness Machine Service (FTMS) helpers for the NovaWalk W50 / MRK-T25.

Pad quirks found by live testing (Nov 2026, firmware 3.4.2.4):
  * SET_SPEED targets are interpreted as 0.01 mph (the spec says 0.01 km/h),
    while Treadmill Data reports speed in real 0.01 km/h.
  * After every SET_INCLINE the pad re-targets speed to (current km/h number as mph),
    i.e. speed jumps x1.6. Fix: wait for the incline to be confirmed, then resend speed.
    Resending speed *before* the incline is confirmed cancels the incline change.
"""
import struct


def uuid16(x: int) -> str:
    return f"0000{x:04x}-0000-1000-8000-00805f9b34fb"


FTMS_SERVICE = uuid16(0x1826)
CONTROL_POINT = uuid16(0x2AD9)
TREADMILL_DATA = uuid16(0x2ACD)
MACHINE_STATUS = uuid16(0x2ADA)

KMH_PER_MPH = 1.609344
METERS_PER_MILE = 1609.344
PAD_MIN_MPH = 0.6
PAD_MAX_MPH = 4.0
PAD_MIN_INCLINE = 0
PAD_MAX_INCLINE = 12

OP_REQUEST_CONTROL = 0x00
OP_SET_SPEED = 0x02
OP_SET_INCLINE = 0x03
OP_START = 0x07
OP_STOP_PAUSE = 0x08
OP_RESPONSE = 0x80

OP_NAMES = {0x00: "request control", 0x02: "set speed", 0x03: "set incline", 0x07: "start", 0x08: "stop"}
RESULTS = {1: "success", 2: "not supported", 3: "invalid parameter", 4: "operation failed",
           5: "control not permitted"}

STATUS_STOPPED = 0x02
STATUS_SAFETY_KEY = 0x03
STATUS_STARTED = 0x04
STATUS_TARGET_SPEED = 0x05
STATUS_TARGET_INCLINE = 0x06
STATUS_CONTROL_LOST = 0xFF


def cmd_request_control() -> bytes:
    return bytes([OP_REQUEST_CONTROL])


def cmd_start() -> bytes:
    return bytes([OP_START])


def cmd_stop() -> bytes:
    return bytes([OP_STOP_PAUSE, 0x01])


def cmd_set_speed_mph(mph: float) -> bytes:
    """Target speed. This pad reads the value as 0.01 mph (see module docstring)."""
    return bytes([OP_SET_SPEED]) + struct.pack("<H", round(mph * 100))


def cmd_set_incline(pct: float) -> bytes:
    return bytes([OP_SET_INCLINE]) + struct.pack("<h", round(pct * 10))


def parse_cp_response(d: bytes):
    """Control point indication -> (request_opcode, result_code), or None."""
    if len(d) >= 3 and d[0] == OP_RESPONSE:
        return d[1], d[2]
    return None


def parse_machine_status(d: bytes) -> dict:
    if not d:
        return {"code": None}
    code, out = d[0], {"code": d[0]}
    if code == STATUS_TARGET_SPEED and len(d) >= 3:
        out["speed_kmh"] = struct.unpack_from("<H", d, 1)[0] / 100
    elif code == STATUS_TARGET_INCLINE and len(d) >= 3:
        out["incline_pct"] = struct.unpack_from("<h", d, 1)[0] / 10
    elif code == STATUS_STOPPED and len(d) >= 2:
        out["paused"] = d[1] == 0x02
    return out


def parse_treadmill_data(d: bytes) -> dict:
    """Parse FTMS Treadmill Data (0x2ACD). Returns only the fields present.

    The pad alternates two records: a full one (flags 0x058C: speed, distance,
    incline, energy, heart rate, elapsed time) and a short one (flags 0x2010).
    """
    out: dict = {}
    if len(d) < 2:
        return out
    flags = int.from_bytes(d[0:2], "little")
    pos = 2

    def take(n: int) -> bytes:
        nonlocal pos
        if pos + n > len(d):
            raise IndexError
        chunk = d[pos:pos + n]
        pos += n
        return chunk

    def u16() -> int:
        return int.from_bytes(take(2), "little")

    def s16() -> int:
        return int.from_bytes(take(2), "little", signed=True)

    try:
        if not flags & 0x0001:
            out["speed_kmh"] = u16() / 100
        if flags & 0x0002:
            take(2)  # average speed
        if flags & 0x0004:
            out["distance_m"] = int.from_bytes(take(3), "little")
        if flags & 0x0008:
            out["incline_pct"] = s16() / 10
            take(2)  # ramp angle
        if flags & 0x0010:
            take(4)  # elevation gain +/-
        if flags & 0x0020:
            take(1)  # instantaneous pace
        if flags & 0x0040:
            take(1)  # average pace
        if flags & 0x0080:
            kcal = u16()
            take(3)  # energy per hour / per minute
            if kcal != 0xFFFF:
                out["kcal"] = kcal
        if flags & 0x0100:
            hr = take(1)[0]
            out["heart_rate"] = hr or None
        if flags & 0x0200:
            take(1)  # metabolic equivalent
        if flags & 0x0400:
            out["elapsed_s"] = u16()
        if flags & 0x0800:
            out["remaining_s"] = u16()
    except IndexError:
        pass
    return out

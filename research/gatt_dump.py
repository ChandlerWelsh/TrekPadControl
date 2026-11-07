"""Read-only GATT dump: connects, lists services/characteristics, reads readable values.
Never writes to any characteristic, so it cannot start or change the belt."""
import asyncio
import struct
import sys
from bleak import BleakClient, BleakScanner

if len(sys.argv) < 2:
    sys.exit("usage: python gatt_dump.py <bluetooth address from scan.py>")
ADDR = sys.argv[1]
BASE = "-0000-1000-8000-00805f9b34fb"


def short(u):
    return u[4:8] if u.endswith(BASE) else u


def decode(uuid, data):
    s = short(uuid)
    try:
        if s == "2acc":  # FTMS Feature
            mf, tf = struct.unpack("<II", data[:8])
            mbits = {0: "avg speed", 1: "cadence", 2: "total distance", 3: "inclination", 4: "elevation gain",
                     5: "pace", 6: "step count", 7: "resistance", 8: "stride count", 9: "expended energy",
                     10: "heart rate", 11: "metabolic eq", 12: "elapsed time", 13: "remaining time",
                     14: "power", 15: "force on belt", 16: "user data retention"}
            tbits = {0: "SET SPEED", 1: "SET INCLINE", 2: "set resistance", 3: "set power", 4: "set heart rate",
                     5: "set energy", 6: "set steps", 7: "set strides", 8: "set distance", 9: "set train time",
                     13: "interval training", 14: "wheel circ", 15: "spin down", 16: "cadence"}
            return ("machine: " + ", ".join(v for b, v in mbits.items() if mf >> b & 1) +
                    " | targets: " + ", ".join(v for b, v in tbits.items() if tf >> b & 1))
        if s == "2ad4":  # Supported speed range, 0.01 km/h
            lo, hi, step = struct.unpack("<HHH", data[:6])
            return f"speed {lo/100}-{hi/100} km/h step {step/100}  ({lo/160.934:.2f}-{hi/160.934:.2f} mph)"
        if s == "2ad5":  # Supported inclination range, 0.1 %
            lo, hi, step = struct.unpack("<hhH", data[:6])
            return f"incline {lo/10}-{hi/10} % step {step/10}"
        if all(32 <= b < 127 for b in data) and data:
            return repr(data.decode())
    except Exception as e:
        return f"(decode error {e})"
    return None


async def main():
    dev = await BleakScanner.find_device_by_address(ADDR, timeout=15.0)
    if not dev:
        print("Device not found - is the phone app disconnected and the pad on?")
        return
    async with BleakClient(dev, timeout=45.0) as c:
        print(f"Connected to {dev.name} {ADDR}\n")
        for svc in c.services:
            print(f"[service {short(svc.uuid)}] {svc.description}")
            for ch in svc.characteristics:
                line = f"   char {short(ch.uuid)} {ch.properties}  {ch.description}"
                if "read" in ch.properties:
                    try:
                        v = bytes(await c.read_gatt_char(ch))
                        d = decode(ch.uuid, v)
                        line += f"\n        = {v.hex()}" + (f"  -> {d}" if d else "")
                    except Exception as e:
                        line += f"\n        read failed: {e}"
                print(line)


asyncio.run(main())



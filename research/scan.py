"""Passive BLE scan: lists nearby advertising devices. Sends nothing to any device."""
import asyncio
from bleak import BleakScanner

FTMS = "00001826-0000-1000-8000-00805f9b34fb"


async def main():
    found = await BleakScanner.discover(timeout=12.0, return_adv=True)
    rows = sorted(found.values(), key=lambda t: -(t[1].rssi or -999))
    for dev, adv in rows:
        name = adv.local_name or dev.name or "(no name)"
        uuids = [u[4:8] if u.endswith("-0000-1000-8000-00805f9b34fb") else u for u in adv.service_uuids]
        flag = "  <-- FTMS" if FTMS in adv.service_uuids else ""
        mfr = {k: v.hex() for k, v in adv.manufacturer_data.items()}
        print(f"{adv.rssi:>4} dBm  {dev.address}  {name!r}  uuids={uuids}  mfr={mfr}{flag}")
    print(f"\n{len(rows)} devices")


asyncio.run(main())

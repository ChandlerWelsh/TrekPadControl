# NovaWalk W50 TrekPad: Bluetooth findings

Tested November 2026 on Windows 11 with an Intel Bluetooth adapter and Python + bleak.

## Device

| | |
|---|---|
| Advertised name | `MRK-T25-xxxx` (last 4 = end of serial) |
| Device Information | Manufacturer `MRK`, model `MR-T25`, hardware 1.0, firmware 3.4.2.4, software 3.0.0 |
| Vendor service `f8c0` | `ThinkFIT`, `TRML`, `TFMODU`, … (ThinkFIT controller module) |
| Signal | Weak: about -97 dBm at 2 ft. Connections sometimes time out during service discovery, so retry. |

## GATT services

| Service | Characteristics | Notes |
|---|---|---|
| `1826` Fitness Machine (FTMS) | `2acc` feature, `2ad3` training status, `2ad4`–`2ad8` ranges, `2ad9` control point (write/indicate), `2ada` machine status (notify), `2acd` treadmill data (notify), `d18d2c10-c44c-11e8-a355-529269fb1459` (write/indicate) | **What this app uses** |
| `180a` Device Information | standard strings | |
| `fff0` | `fff1` notify/indicate, `fff2` write | vendor UART-style, unused |
| `59554c55-8000-6666-8888-4d4552414348` | one write/indicate char | ASCII tail spells `MERACH`; probably Merach app protocol, unused |
| `00010203-0405-0607-0809-0a0b0c0d1912` | read/notify/write | Telink OTA-style, **do not touch** |
| `f8c0` | `f8c1`–`f8c8` read-only strings | |

FTMS Feature (`2acc` = `1c16000003000000`):
- Machine features: total distance, inclination, elevation gain, expended energy, heart rate, elapsed time
- Target settings: **speed** and **inclination**

Ranges:
- Speed `2ad4` = `3c0090010a00`: 0.6–4.0, step 0.1. Labelled km/h by spec, but these are really **mph** values
- Incline `2ad5` = `000078000a00`: 0–12 %, step 1

## Control point commands that work

| Bytes | Meaning | Notes |
|---|---|---|
| `00` | Request control | Pad never replies; not required |
| `07` | Start | Belt counts down ~3 s, then runs at 0.6 mph. Reply is sometimes missing. |
| `02 LL HH` | Set target speed | **Units are 0.01 mph** (spec says 0.01 km/h) |
| `03 LL HH` | Set target incline | 0.1 % units (e.g. `030a00` = 1 %) |
| `08 01` | Stop | Belt ramps down over ~2.5 s |

Replies arrive as indications on `2ad9`: `80 <opcode> <result>` (01 = success).

## Quirk 1: mixed speed units

- Commands: 0.01 **mph**. `026400` (100) → belt at 1.0 mph.
- Treadmill Data and machine status `05` report real 0.01 **km/h**: 1.0 mph → `a100` (161).

## Quirk 2: incline change multiplies speed by about 1.6

After `SET_INCLINE`, the pad sets target speed to its current km/h number read as mph, rounded
to 0.1:

| Live test 1 | Speed |
|---|---|
| set 1.0 mph | 1.0 mph |
| set incline 1 % | **1.6 mph** |
| set incline 0 % | **2.5 mph** |

- **Sending speed immediately after incline** (before the pad confirms) cancels the incline change.
  Speed held, but incline stayed 0 (live test 2).
- **Waiting for the incline to be confirmed** (Treadmill Data incline field or status `06`, about
  0.9 s) and **then** sending speed works: incline applied and speed back to target about 1.5 s
  after the command (live test 3).
- Treadmill Data seems to report the *target* speed (it jumps instantly), so the real belt overshoot
  is smaller than 1.6×.
- **Jumps compound:** a second incline change sent before the first jump is corrected multiplies
  the already-jumped speed (1.5 → 2.4 → 3.8 mph, seen in live testing). The app therefore settles
  speed back to target before sending each new incline.

## Silent disconnects

Windows can keep reporting the link as connected after the pad powers off or drops out, and
writes then "succeed" without reaching the pad. The pad sends Treadmill Data about every 0.8 s
even when idle, so the app treats 5 s of silence as a lost link and reconnects.

## Treadmill Data (`2acd`)

The pad alternates two records about every 0.8 s:

- `8c05 …` (flags 0x058C): speed, total distance (m), incline + ramp angle, energy (kcal), heart rate (0), elapsed time (s)
- `1020 …` (flags 0x2010): speed, elevation gain, plus a field outside FTMS 1.0 (bit 13)

The pad's counters reset when the belt is started again.

## Machine status (`2ada`)

`04` started, `05 xxxx` target speed changed (km/h), `06 xxxx` target incline changed,
`02 01` stopped, and `03` (the spec calls it "stopped by safety key") also arrives while idle
just after connecting.

## Scripts

- `scan.py`: passive scan, flags FTMS devices
- `gatt_dump.py`: read-only service and characteristic dump
- `live_test.py`: start → 1.0 mph → incline 1 % (wait, correct) → 0 % → stop, with a speed limit
  that stops the belt. **Stay off the belt and keep the remote in hand.**

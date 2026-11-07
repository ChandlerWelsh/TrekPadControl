"""Bluetooth connection, commands and safety logic for the pad.

Runs its own asyncio loop on a background thread. Other threads (window, hotkeys,
local server) call `submit()` / `call()`; everything that touches Bluetooth runs
on the pad thread.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import time

from bleak import BleakClient, BleakScanner

from . import ftms

log = logging.getLogger(__name__)

SPEED_TOLERANCE_MPH = 0.15   # how far over target counts as "jumped"
GUARD_SECONDS = 5.0          # how long after an incline change we watch for the speed jump
START_GRACE_SECONDS = 10.0   # belt counts down ~3 s after START before it moves
MAX_CORRECTIONS = 3          # after this many failed speed corrections -> STOP
STALE_SECONDS = 5.0          # the pad sends data ~every 0.8 s; silence this long = link is dead
                             # (Windows can keep reporting "connected" after the pad drops off)


class PadError(RuntimeError):
    pass


class PadController:
    def __init__(self, address: str | None, max_mph: float, on_address_found=None):
        self.address = address
        self.max_mph = max_mph
        self.on_address_found = on_address_found
        self.sample_listeners: list = []  # called on the pad thread with each full Treadmill Data record

        self._lock = threading.Lock()
        self._state = {
            "connected": False, "status": "Starting…", "device": None,
            "speed_mph": 0.0, "incline_pct": 0.0, "distance_m": 0, "kcal": 0,
            "elapsed_s": 0, "heart_rate": None, "message": "", "message_at": 0.0,
        }
        self.loop: asyncio.AbstractEventLoop | None = None
        self._client: BleakClient | None = None
        self._closing = False

        # created on the pad loop
        self._cp_lock: asyncio.Lock | None = None
        self._action_lock: asyncio.Lock | None = None
        self._cp_replies: asyncio.Queue | None = None
        self._disconnected: asyncio.Event | None = None

        self._desired_mph: float | None = None
        self._last_speed_cmd = 0.0
        self._start_requested = 0.0
        self._status_incline: float | None = None
        self._guard_until = 0.0
        self._guard_mph: float | None = None
        self._guard_corrections = 0
        self._cap_corrections = 0
        self._last_correction = 0.0
        self._last_data = 0.0
        self._last_started_status = 0.0
        # While an incline change is settling, show the held speed instead of the pad's
        # brief x1.6 jump (the watchdog still sees and corrects the real value).
        self._hold_mph: float | None = None
        self._hold_until = 0.0

    # ------------------------------------------------------------------ thread-safe API
    def start_background(self):
        ready = threading.Event()

        def runner():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            ready.set()
            self.loop.run_until_complete(self._main())

        threading.Thread(target=runner, name="pad-bluetooth", daemon=True).start()
        ready.wait()

    def snapshot(self) -> dict:
        with self._lock:
            s = dict(self._state)
        s["running"] = self.is_running()
        raw = s["speed_mph"]
        s["reported_speed_mph"] = round(raw, 2)
        if self._is_known_jump(raw):
            raw = self._hold_mph
        s["speed_mph"] = round(raw, 2)
        s["max_speed_mph"] = self.max_mph
        return s

    def submit(self, name: str, *args) -> concurrent.futures.Future:
        """Run a command (start, stop, set_speed, change_speed, set_incline, change_incline)."""
        fut = asyncio.run_coroutine_threadsafe(getattr(self, name)(*args), self.loop)

        def report(f):
            exc = f.exception()
            if exc:
                self._msg(str(exc) if isinstance(exc, PadError) else f"Error: {exc}")
                if not isinstance(exc, PadError):
                    log.error("%s failed", name, exc_info=exc)

        fut.add_done_callback(report)
        return fut

    def call(self, name: str, *args, timeout: float = 20):
        return self.submit(name, *args).result(timeout)

    def shutdown(self, stop_belt: bool = False, timeout: float = 10):
        if self.loop is None:
            return

        async def close():
            self._closing = True
            if stop_belt and self._client is not None:
                try:
                    await self.stop()
                except Exception as e:
                    log.warning("stop on exit failed: %s", e)
            if self._client is not None:
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
            if self._disconnected is not None:
                self._disconnected.set()

        try:
            asyncio.run_coroutine_threadsafe(close(), self.loop).result(timeout)
        except Exception as e:
            log.warning("shutdown: %s", e)

    def _is_known_jump(self, raw: float) -> bool:
        """True only for the pad's single x1.6 jump right after one of our incline changes.
        Anything else (a compounded jump, the remote, going over the max) is shown as-is."""
        hold = self._hold_mph
        if hold is None or time.monotonic() >= self._hold_until:
            return False
        if raw > self._speed_limit() + SPEED_TOLERANCE_MPH:
            return False
        jumped = round(hold * ftms.KMH_PER_MPH, 1)  # what the firmware re-targets to
        return abs(raw - jumped) <= 0.15

    def is_running(self) -> bool:
        return (self._state["speed_mph"] > 0.05
                or time.monotonic() - self._start_requested < START_GRACE_SECONDS)

    # ------------------------------------------------------------------ commands (pad thread)
    async def start(self):
        await self._cp(ftms.cmd_start(), "start")
        self._start_requested = time.monotonic()
        self._desired_mph = None
        log.info("starting belt")
        asyncio.create_task(self._check_started(self._start_requested))

    async def _check_started(self, t0: float):
        await asyncio.sleep(8)
        if self._last_started_status < t0 and self._state["speed_mph"] <= 0.05 and self._start_requested == t0:
            self._start_requested = 0.0
            self._msg("The pad didn't respond to Start. Check that it's on and nearby.")

    async def stop(self):
        # No action lock: STOP must never wait behind another command.
        self._guard_until = 0.0
        self._hold_until = 0.0
        self._desired_mph = None
        self._start_requested = 0.0
        await self._cp(ftms.cmd_stop(), "stop")
        log.info("belt stopped")

    async def set_speed(self, mph: float):
        async with self._action_lock:
            await self._set_speed(float(mph))

    async def change_speed(self, delta: float):
        async with self._action_lock:
            await self._set_speed(self._base_speed() + float(delta))

    async def set_incline(self, pct: float):
        async with self._action_lock:
            await self._set_incline(float(pct))

    async def change_incline(self, delta: float):
        async with self._action_lock:
            await self._set_incline(self._state["incline_pct"] + float(delta))

    # ------------------------------------------------------------------ internals
    def _speed_limit(self) -> float:
        return max(ftms.PAD_MIN_MPH, min(float(self.max_mph), ftms.PAD_MAX_MPH))

    def _base_speed(self) -> float:
        """Speed to build on: our last command if it is recent, else what the pad reports
        (which also picks up changes made with the remote)."""
        if self._desired_mph is not None and time.monotonic() - self._last_speed_cmd < 3:
            return self._desired_mph
        return round(self._state["speed_mph"], 1)

    async def _wait_until_moving(self):
        if self._state["speed_mph"] > 0.05:
            return
        if time.monotonic() - self._start_requested > START_GRACE_SECONDS:
            raise PadError("The belt isn't running. Press Start first.")
        deadline = time.monotonic() + START_GRACE_SECONDS
        while self._state["speed_mph"] <= 0.05:
            if time.monotonic() > deadline:
                raise PadError("The belt didn't start moving.")
            await asyncio.sleep(0.2)

    async def _send_speed(self, mph: float, label: str) -> float:
        mph = round(min(max(mph, ftms.PAD_MIN_MPH), self._speed_limit()), 1)
        self._desired_mph = mph
        self._last_speed_cmd = time.monotonic()
        await self._cp(ftms.cmd_set_speed_mph(mph), f"{label} {mph:.1f} mph")
        return mph

    async def _set_speed(self, mph: float):
        await self._wait_until_moving()
        mph = await self._send_speed(mph, "set speed")
        if time.monotonic() < self._guard_until:
            self._guard_mph = mph
        if time.monotonic() < self._hold_until:
            self._hold_mph = mph
        log.info("speed %.1f mph", mph)

    async def _set_incline(self, pct: float):
        await self._wait_until_moving()
        pct = int(round(min(max(pct, ftms.PAD_MIN_INCLINE), ftms.PAD_MAX_INCLINE)))
        if abs(self._state["incline_pct"] - pct) < 0.05:
            return
        hold = max(self._base_speed(), ftms.PAD_MIN_MPH)
        # Close any earlier guard window: a watchdog correction while this incline is
        # still pending would cancel it.
        self._guard_until = 0.0
        # The pad multiplies its *current* speed by ~1.6 on every incline change. If an
        # earlier jump hasn't been corrected yet (quick successive clicks), settle it first,
        # or the jumps compound (1.5 -> 2.4 -> 3.8 mph, seen in live testing).
        if self._state["speed_mph"] > hold + SPEED_TOLERANCE_MPH:
            await self._send_speed(hold, "settle speed")
            t_settle = time.monotonic()
            while self._state["speed_mph"] > hold + SPEED_TOLERANCE_MPH:
                if time.monotonic() - t_settle > 3.0:
                    raise PadError("The speed didn't settle, so the incline wasn't changed. Try again.")
                await asyncio.sleep(0.05)
        self._status_incline = None
        t0 = time.monotonic()
        self._hold_mph = hold
        self._hold_until = t0 + 3.0 + GUARD_SECONDS
        await self._cp(ftms.cmd_set_incline(pct), f"set incline {pct}%")
        # Wait for the pad to accept the incline before correcting speed;
        # correcting earlier cancels the incline (seen in live test 2).
        while time.monotonic() - t0 < 3.0:
            if self._status_incline == pct or abs(self._state["incline_pct"] - pct) < 0.05:
                break
            await asyncio.sleep(0.05)
        else:
            log.warning("incline %s%% not confirmed within 3 s", pct)
        await self._send_speed(hold, "hold speed")
        self._guard_mph = hold
        self._guard_corrections = 0
        self._last_correction = time.monotonic()
        self._guard_until = time.monotonic() + GUARD_SECONDS
        self._hold_until = self._guard_until + 0.5
        log.info("incline %s%%, speed held at %.1f mph", pct, hold)

    async def _emergency_stop(self, reason: str):
        log.error("emergency stop: %s", reason)
        try:
            await self.stop()
        finally:
            self._msg(f"Belt stopped for safety: {reason}")

    async def _check_speed(self):
        now = time.monotonic()
        if now - self._last_correction < 1.0:
            return
        mph = self._state["speed_mph"]
        limit = self._speed_limit()
        if mph > limit + SPEED_TOLERANCE_MPH:
            self._cap_corrections += 1
            self._last_correction = now
            if self._cap_corrections > MAX_CORRECTIONS:
                await self._emergency_stop(f"speed stayed above your {limit:.1f} mph limit")
            else:
                await self._send_speed(limit, "limit speed")
                if self._cap_corrections == 1:
                    self._msg(f"Speed pulled back to your {limit:.1f} mph limit")
            return
        self._cap_corrections = 0
        if now < self._guard_until and self._guard_mph is not None \
                and mph > self._guard_mph + SPEED_TOLERANCE_MPH:
            self._guard_corrections += 1
            self._last_correction = now
            if self._guard_corrections > MAX_CORRECTIONS:
                await self._emergency_stop("speed jumped after an incline change and wouldn't settle")
            else:
                await self._send_speed(self._guard_mph, "correct speed")

    async def _watchdog(self):
        while not self._closing:
            await asyncio.sleep(0.25)
            if self._client is None or not self._state["connected"]:
                continue
            if time.monotonic() - self._last_data > STALE_SECONDS:
                await self._drop_stale_link()
                continue
            try:
                await self._check_speed()
            except Exception as e:
                log.warning("watchdog: %s", e)

    async def _drop_stale_link(self):
        log.warning("no data from the pad for %.0f s; treating the link as lost", STALE_SECONDS)
        self._set(connected=False, status="Lost the pad's signal. Reconnecting…")
        self._msg("Lost the pad's signal. Reconnecting…")
        client = self._client
        try:
            await asyncio.wait_for(client.disconnect(), 5)
        except Exception:
            pass
        if self._disconnected is not None:
            self._disconnected.set()

    async def _cp(self, payload: bytes, label: str, timeout: float = 5.0):
        client = self._client
        if (client is None or not client.is_connected or not self._state["connected"]
                or time.monotonic() - self._last_data > STALE_SECONDS):
            raise PadError("The pad isn't connected. Check that it's on and nearby.")
        async with self._cp_lock:
            while not self._cp_replies.empty():
                self._cp_replies.get_nowait()
            log.info("send %s (%s)", label, payload.hex())
            await client.write_gatt_char(ftms.CONTROL_POINT, payload, response=True)
            try:
                op, result = await asyncio.wait_for(self._cp_replies.get(), timeout)
            except asyncio.TimeoutError:
                log.info("no reply to %s", label)  # the pad sometimes doesn't answer; command still applies
                return None
        if result != 1:
            raise PadError(f"The pad refused {label}: {ftms.RESULTS.get(result, result)}")
        return result

    # ------------------------------------------------------------------ connection
    async def _main(self):
        self._cp_lock = asyncio.Lock()
        self._action_lock = asyncio.Lock()
        self._cp_replies = asyncio.Queue()
        asyncio.create_task(self._watchdog())
        while not self._closing:
            try:
                await self._connect_once()
                await self._disconnected.wait()
                if not self._closing:
                    log.warning("disconnected from pad")
                    self._set(connected=False, status="Lost the pad's signal. Reconnecting…")
            except Exception as e:
                log.warning("connect failed: %s: %s", type(e).__name__, e)
                self._set(status=f"Can't reach the pad, retrying… ({e or type(e).__name__})")
            self._client = None
            self._set(connected=False)
            if not self._closing:
                await asyncio.sleep(3)

    async def _connect_once(self):
        self._set(status="Searching for the pad…")
        if self.address:
            dev = await BleakScanner.find_device_by_address(self.address, timeout=15.0)
        else:
            def match(d, adv):
                name = adv.local_name or d.name or ""
                return name.startswith("MRK-") and ftms.FTMS_SERVICE in adv.service_uuids
            dev = await BleakScanner.find_device_by_filter(match, timeout=15.0)
        if dev is None:
            raise PadError("pad not found. Is it on, and is the phone app closed?")

        self._set(status=f"Connecting to {dev.name}…")
        loop = asyncio.get_running_loop()
        self._disconnected = asyncio.Event()
        event = self._disconnected
        client = BleakClient(dev, timeout=45.0,
                             disconnected_callback=lambda _c: loop.call_soon_threadsafe(event.set))
        await client.connect()
        try:
            await client.start_notify(ftms.CONTROL_POINT, self._on_cp)
            await client.start_notify(ftms.TREADMILL_DATA, self._on_tdata)
            await client.start_notify(ftms.MACHINE_STATUS, self._on_status)
        except Exception:
            await client.disconnect()
            raise
        self._client = client
        self._last_data = time.monotonic()  # grace period until the first data arrives
        if self.address != dev.address:
            self.address = dev.address
            if self.on_address_found:
                self.on_address_found(dev.address)
        self._set(connected=True, device=dev.name, status=f"Connected to {dev.name}")
        log.info("connected to %s %s", dev.name, dev.address)
        try:
            await self._cp(ftms.cmd_request_control(), "request control", timeout=2.0)
        except PadError as e:
            log.warning("request control: %s", e)

    # ------------------------------------------------------------------ notifications
    def _on_cp(self, _char, data: bytearray):
        reply = ftms.parse_cp_response(bytes(data))
        if reply:
            self._cp_replies.put_nowait(reply)

    def _on_status(self, _char, data: bytearray):
        st = ftms.parse_machine_status(bytes(data))
        log.info("machine status %s", bytes(data).hex())
        code = st.get("code")
        if code == ftms.STATUS_STARTED:
            self._last_started_status = time.monotonic()
        elif code == ftms.STATUS_TARGET_INCLINE:
            self._status_incline = st.get("incline_pct")
        elif code in (ftms.STATUS_STOPPED, ftms.STATUS_SAFETY_KEY):
            # The pad also sends "safety key" (03) while idle right after connecting,
            # so only mention it if the belt was actually moving.
            if code == ftms.STATUS_SAFETY_KEY and self._state["speed_mph"] > 0.05:
                self._msg("The pad reported a safety stop")
            self._start_requested = 0.0
        elif code == ftms.STATUS_CONTROL_LOST:
            self._msg("The pad says another device took control")

    def _on_tdata(self, _char, data: bytearray):
        rec = ftms.parse_treadmill_data(bytes(data))
        if not rec:
            return
        self._last_data = time.monotonic()
        if not self._state["connected"] and self._client is not None:
            return  # link was declared lost; ignore stragglers until reconnected
        upd = {}
        if "speed_kmh" in rec:
            upd["speed_mph"] = rec["speed_kmh"] / ftms.KMH_PER_MPH
        for key in ("incline_pct", "distance_m", "kcal", "elapsed_s", "heart_rate"):
            if key in rec:
                upd[key] = rec[key]
        self._set(**upd)
        if "elapsed_s" in rec:
            for fn in self.sample_listeners:
                try:
                    fn(rec)
                except Exception:
                    log.exception("sample listener failed")

    def _set(self, **kw):
        with self._lock:
            self._state.update(kw)

    def _msg(self, text: str):
        log.info(text)
        self._set(message=text, message_at=time.time())

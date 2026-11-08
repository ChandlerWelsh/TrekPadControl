"""Game mode: speed and incline follow what you're doing in a game.

Signals (no game data needed):
  * which app is in front (a game from the `games` list, or something else)
  * how long since the last mouse or keyboard input (Windows GetLastInputInfo: a single
    timestamp; no keys are read or recorded)
Game events posted to /api/game/event ("death", "respawn", "match_start", "match_end"), for
example from Game State Integration or an Overwolf bridge, override the idle guess.

Profiles:
  aim    speed: easy pace while you're playing, hard pace while you're idle or dead
         (aiming suffers at speed, so the work goes where you aren't aiming)
  climb  incline: +1% every `climb_every_min` minutes in game, up to `max_incline_pct`
  both   both of the above

Safety:
  * never starts the belt, and does nothing while it is stopped
  * speed moves 0.1 mph at a time (slower going up than down), never above your max speed
  * beeps, then waits a few seconds, before speeding up or changing the incline
  * incline changes wait for a moment you aren't aiming (idle, dead, or out of the game),
    because every incline change on this pad briefly jumps the speed (see ftms.py)
  * changing the speed yourself (app, shortcut or remote) pauses game mode for a minute;
    changing the incline yourself moves the climb by the same amount
  * leaving the game eases back to the easy pace
"""
from __future__ import annotations

import logging
import re
import threading
import time

from .ftms import PAD_MAX_INCLINE, PAD_MAX_MPH, PAD_MIN_INCLINE, PAD_MIN_MPH

log = logging.getLogger(__name__)

PROFILES = ("aim", "climb", "both")
# Which executables belong to each game source (a Dota 2 source would add "dota2": {"dota2.exe"}).
GAME_EXES = {"deadlock": {"deadlock.exe", "project8.exe"}, "dota2": {"dota2.exe"}}
GAME_NAMES = {"deadlock": "Deadlock", "dota2": "Dota 2"}
EVENTS = ("death", "respawn", "match_start", "match_end")

STEP_MPH = 0.1
UP_STEP_S = 2.0              # 0.1 mph every 2 s going up: easy -> hard (0.8 mph) takes ~16 s
DOWN_STEP_S = 1.0            # quicker coming back down, so you can aim again sooner
WARN_S = 3.0                 # beep this long before speeding up or changing incline
FOCUS_GRACE_S = 5.0          # a quick alt-tab doesn't count as leaving the game
LEAVE_RESET_S = 120.0        # out of the game this long: the climb starts over
INCLINE_STEP_PCT = 2         # largest single incline change
INCLINE_MIN_GAP_S = 45.0     # time between incline changes
INCLINE_FORCE_AFTER_S = 60.0  # a waiting incline change goes ahead after this, idle or not
MANUAL_TOLERANCE_MPH = 0.25
MANUAL_GRACE_S = 4.0         # after our own command (and 10 s after an incline change)
MANUAL_PAUSE_S = 60.0
DEAD_TIMEOUT_S = 90.0        # a "death" event with no "respawn" expires
TICK_S = 0.5

LABELS = {
    "off": "Off",
    "belt_stopped": "Waiting for the belt",
    "not_in_game": "Waiting for a game",
    "waiting_match": "Waiting for the match to start",
    "playing": "Playing · easy pace",
    "idle": "Idle · hard pace",
    "dead": "Dead · hard pace",
    "paused": "Paused · you changed the speed",
}
SHORT = {"off": "", "belt_stopped": "waiting", "not_in_game": "waiting", "waiting_match": "waiting", "playing": "easy",
         "idle": "hard", "dead": "hard", "paused": "paused"}
EXE_RE = re.compile(r"^[\w .()+-]{1,80}\.exe$", re.IGNORECASE)


def as_bool(v) -> bool:
    """JSON true/false, or "1"/"true"/"on" from a query string."""
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


class GameMode:
    def __init__(self, pad, cfg: dict, signals, beep=None, save=None, clock=time.monotonic):
        """`cfg` is the live "game_mode" section of the config (changed in place).
        `signals()` -> {"exe": foreground exe name, "self": True if it's this app, "idle_s": float}."""
        self.pad = pad
        self.cfg = cfg
        self.signals = signals
        self._beep = beep or (lambda kind: None)
        self._save = save or (lambda: None)
        self._clock = clock
        self._lock = threading.RLock()
        self._stop = threading.Event()

        self.state = "off"
        self.last_app: str | None = None
        self._focused_exe: str | None = None
        self._last_in_game = float("-inf")
        self._last_tick: float | None = None
        self._game_s = 0.0
        self._dead_until = 0.0
        self._paused_until = 0.0
        self._busy = None
        self._target_mph: float | None = None
        self._target_incline: int | None = None
        self._inc_offset = 0  # your own incline changes, added to the climb
        # One live game source per game: name -> callable returning None (not live) or
        # {"game": name, "in_match": bool, "deaths": int, ...}. Deadlock's log, Dota 2's GSI.
        self.sources: dict = {}
        self._reset_run()

    def _reset_run(self):
        self._sent_mph: float | None = None
        self._last_step = float("-inf")
        self._up_warn_until: float | None = None
        self._inc_pending_since: float | None = None
        self._inc_warn_until: float | None = None
        self._last_incline_change = float("-inf")
        self._sent_incline: int | None = None

    # ------------------------------------------------------------------ control
    def start(self):
        threading.Thread(target=self._run, name="game-mode", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(TICK_S):
            try:
                self.tick()
            except Exception:
                log.exception("game mode tick failed")

    def set_enabled(self, on: bool):
        with self._lock:
            if bool(on) == bool(self.cfg.get("enabled")):
                return
            self.cfg["enabled"] = bool(on)
            self._reset_run()
            self._paused_until = 0.0
            self._inc_offset = 0
            self._save()
        self._sound("on" if on else "off")
        self.pad._msg("Game mode on" if on else "Game mode off. The speed stays where it is.")
        log.info("game mode %s", "on" if on else "off")

    def toggle(self):
        self.set_enabled(not self.cfg.get("enabled"))

    def event(self, name: str):
        if name not in EVENTS:
            raise ValueError("event")
        with self._lock:
            now = self._clock()
            if name == "death":
                self._dead_until = now + DEAD_TIMEOUT_S
            elif name == "respawn":
                self._dead_until = 0.0
            else:  # match_start / match_end: the climb starts over
                self._game_s = 0.0
                self._inc_offset = 0
                self._dead_until = 0.0
        log.info("game event: %s", name)

    def apply_settings(self, p: dict):
        """Validate and apply settings from the API. Raises ValueError on bad values."""
        with self._lock:
            c = dict(self.cfg)
            if "profile" in p:
                if p["profile"] not in PROFILES:
                    raise ValueError("profile")
                c["profile"] = p["profile"]
            for key in ("easy_mph", "hard_mph"):
                if key in p:
                    v = round(float(p[key]), 1)
                    if not PAD_MIN_MPH <= v <= PAD_MAX_MPH:
                        raise ValueError(key)
                    c[key] = v
            for key in ("start_incline_pct", "max_incline_pct"):
                if key in p:
                    v = int(round(float(p[key])))
                    if not PAD_MIN_INCLINE <= v <= PAD_MAX_INCLINE:
                        raise ValueError(key)
                    c[key] = v
            if "climb_every_min" in p:
                v = int(round(float(p["climb_every_min"])))
                if not 1 <= v <= 60:
                    raise ValueError("climb_every_min")
                c["climb_every_min"] = v
            if "idle_seconds" in p:
                v = int(round(float(p["idle_seconds"])))
                if not 3 <= v <= 60:
                    raise ValueError("idle_seconds")
                c["idle_seconds"] = v
            if "sounds" in p:
                c["sounds"] = as_bool(p["sounds"])
            games = [g.lower() for g in c.get("games", [])]
            if "add_game" in p:
                g = str(p["add_game"]).strip().lower()
                if not EXE_RE.match(g):
                    raise ValueError("add_game")
                if g not in games:
                    games.append(g)
            if "remove_game" in p:
                games = [g for g in games if g != str(p["remove_game"]).strip().lower()]
            c["games"] = games
            # keep easy <= hard and start <= max, moving whichever one wasn't just changed
            if c["easy_mph"] > c["hard_mph"]:
                if "hard_mph" in p:
                    c["easy_mph"] = c["hard_mph"]
                else:
                    c["hard_mph"] = c["easy_mph"]
            if c["start_incline_pct"] > c["max_incline_pct"]:
                if "max_incline_pct" in p:
                    c["start_incline_pct"] = c["max_incline_pct"]
                else:
                    c["max_incline_pct"] = c["start_incline_pct"]
            self.cfg.update(c)
            self._save()
        if "enabled" in p:
            self.set_enabled(as_bool(p["enabled"]))

    def _infos(self) -> dict:
        out = {}
        for name, fn in list(self.sources.items()):
            try:
                info = fn()
            except Exception:
                log.debug("game source %s failed", name, exc_info=True)
                info = None
            if info:
                out[name] = info
        return out

    def _events_live(self) -> bool:
        return bool(self._infos())

    def _match_status(self) -> dict:
        m = self._match_info()
        return {"match": m, "game_events": m is not None,
                "source_name": GAME_NAMES.get(m.get("game"), m.get("game")) if m else None}

    def _match_info(self) -> dict | None:
        """The live source for the game in front; with no game in front (e.g. looking at
        TrekPad), whichever game has a match running, else the most recent finished match."""
        infos = self._infos()
        if self._focused_exe:
            for name, exes in GAME_EXES.items():
                if self._focused_exe in exes:
                    return infos.get(name)
            return None
        live = [i for i in infos.values() if i.get("in_match")]
        if live:
            return live[0]
        done = [i for i in infos.values() if i.get("last_match")]
        return max(done, key=lambda i: i["last_match"].get("ended_at") or 0) if done else None

    def status(self) -> dict:
        with self._lock:
            c = self.cfg
            paused = max(0, round(self._paused_until - self._clock())) if self.state == "paused" else 0
            return {
                "enabled": bool(c.get("enabled")), "state": self.state,
                "label": LABELS.get(self.state, self.state), "short": SHORT.get(self.state, ""),
                "paused_s": paused, "target_mph": self._target_mph, "target_incline_pct": self._target_incline,
                "in_game": self._focused_exe, "last_app": self.last_app,
                **self._match_status(),
                "game_minutes": round(self._game_s / 60, 1),
                "settings": {k: c.get(k) for k in ("profile", "games", "easy_mph", "hard_mph", "start_incline_pct",
                                                   "max_incline_pct", "climb_every_min", "idle_seconds", "sounds")},
            }

    # ------------------------------------------------------------------ the loop
    def tick(self):
        with self._lock:
            self._tick()

    def _sound(self, kind: str):
        if self.cfg.get("sounds", True):
            try:
                self._beep(kind)
            except Exception:
                log.debug("beep failed", exc_info=True)

    def _tick(self):
        now = self._clock()
        dt = 0.0 if self._last_tick is None else min(max(now - self._last_tick, 0.0), 5.0)
        self._last_tick = now
        c = self.cfg

        sig = self.signals() or {}
        exe = (sig.get("exe") or "").lower() or None
        if exe and not sig.get("self"):
            self.last_app = exe
        games = {g.lower() for g in c.get("games", [])}
        for name in self.sources:                       # games with a source always count
            games |= GAME_EXES.get(name, set())
        if exe in games:
            self._last_in_game = now
            self._focused_exe = exe
        focused = now - self._last_in_game < FOCUS_GRACE_S
        if not focused:
            self._focused_exe = None
        # The game's live source (Deadlock's log, Dota 2's GSI) says whether a match is running.
        # Without one, being in the game's window counts as playing (e.g. for other games).
        info = self._match_info()
        in_match = True if info is None else bool(info.get("in_match"))
        active = focused and in_match

        # Time in a match drives the climb (counted even while the belt is stopped or the mode is off).
        if active:
            self._game_s += dt
        elif now - self._last_in_game > LEAVE_RESET_S:
            self._game_s = 0.0
            self._inc_offset = 0

        if not c.get("enabled"):
            self.state = "off"
            self._target_mph = self._target_incline = None
            return

        snap = self.pad.snapshot()
        cur = float(snap.get("speed_mph") or 0.0)
        if not (snap.get("connected") and snap.get("running") and cur > 0.05):
            self.state = "belt_stopped"
            self._target_mph = self._target_incline = None
            self._reset_run()
            return

        dead = in_match and now < self._dead_until
        idle = in_match and float(sig.get("idle_s") or 0.0) >= c["idle_seconds"]
        hard = active and (dead or idle)
        not_aiming = (not active) or dead or idle
        profile = c.get("profile", "both")
        limit = min(float(getattr(self.pad, "max_mph", PAD_MAX_MPH)), PAD_MAX_MPH)

        if not in_match:
            # Waiting for the match to start: leave the belt alone, except to bring the speed back
            # to the easy pace if game mode had raised it (for example, dead when the match ended).
            self.state = "waiting_match"
            easy = round(max(PAD_MIN_MPH, min(c["easy_mph"], limit)), 1)
            if not (profile in ("aim", "both") and self._sent_mph is not None and self._sent_mph > easy + 0.05):
                self._target_mph = self._target_incline = None
                self._reset_run()
                return
        elif not focused:
            self.state = "not_in_game"
        else:
            self.state = "dead" if dead else "idle" if idle else "playing"

        # targets
        target_mph = None
        if profile in ("aim", "both"):
            target_mph = round(max(PAD_MIN_MPH, min(c["hard_mph"] if hard else c["easy_mph"], limit)), 1)
        have_inc = int(round(float(snap.get("incline_pct") or 0)))
        busy = self._busy is not None and not self._busy.done()
        target_inc = None
        if in_match and profile in ("climb", "both"):
            # you changed the incline yourself -> keep climbing from there
            if self._sent_incline is not None and have_inc != self._sent_incline and not busy \
                    and now - self._last_incline_change > 10.0:
                self._inc_offset += have_inc - self._sent_incline
                self._sent_incline = have_inc
                self.pad._msg("Game mode will keep climbing from your incline")
            steps = int(self._game_s // (c["climb_every_min"] * 60))
            target_inc = min(c["start_incline_pct"] + steps, c["max_incline_pct"]) + self._inc_offset
            target_inc = max(PAD_MIN_INCLINE, min(PAD_MAX_INCLINE, target_inc))
        self._target_mph, self._target_incline = target_mph, target_inc

        # you changed the speed yourself -> step back for a while
        if self._sent_mph is not None and now - self._last_step > MANUAL_GRACE_S \
                and now - self._last_incline_change > 10.0 and abs(cur - self._sent_mph) > MANUAL_TOLERANCE_MPH:
            self._paused_until = now + MANUAL_PAUSE_S
            self._reset_run()
            self.pad._msg("You changed the speed, so game mode is paused for a minute")
            log.info("game mode paused: speed %.1f, last sent %s", cur, self._sent_mph)
        if now < self._paused_until:
            self.state = "paused"
            return

        if busy:
            return

        # incline (rare, waits for a moment you aren't aiming)
        if target_inc is not None:
            have = have_inc
            if target_inc != have:
                if self._inc_pending_since is None:
                    self._inc_pending_since = now
                ready = (not_aiming or now - self._inc_pending_since >= INCLINE_FORCE_AFTER_S) \
                    and now - self._last_incline_change >= INCLINE_MIN_GAP_S
                if ready:
                    if self._inc_warn_until is None:
                        self._sound("incline")
                        self._inc_warn_until = now + WARN_S
                        return
                    if now >= self._inc_warn_until:
                        step = max(-INCLINE_STEP_PCT, min(INCLINE_STEP_PCT, target_inc - have))
                        self._inc_warn_until = None
                        self._inc_pending_since = now if have + step != target_inc else None
                        self._last_incline_change = now
                        self._sent_incline = have + step
                        log.info("game mode: incline %s%% -> %s%%", have, have + step)
                        self._busy = self.pad.submit("set_incline", have + step)
                    return
                self._inc_warn_until = None
            else:
                self._inc_pending_since = None
                self._inc_warn_until = None
                self._sent_incline = have

        # speed (ramps)
        if target_mph is None:
            return
        base = self._sent_mph if self._sent_mph is not None else round(cur, 1)
        diff = round(target_mph - base, 2)
        if abs(diff) < 0.05:
            self._up_warn_until = None
            return
        if diff > 0:
            if self._up_warn_until is None:
                self._sound("up")
                self._up_warn_until = now + WARN_S
                return
            if now < self._up_warn_until or now - self._last_step < UP_STEP_S:
                return
        else:
            self._up_warn_until = None
            if now - self._last_step < DOWN_STEP_S:
                return
        nxt = round(base + max(-STEP_MPH, min(STEP_MPH, diff)), 1)
        self._sent_mph = nxt
        self._last_step = now
        self._busy = self.pad.submit("set_speed", nxt)


# ---------------------------------------------------------------------- Windows helpers
TONES = {"up": [(660, 110), (880, 150)], "incline": [(523, 100), (659, 100), (784, 140)],
         "on": [(660, 80), (990, 110)], "off": [(990, 80), (660, 110)]}


def windows_beep(kind: str):
    """Short tones on a background thread (winsound.Beep blocks)."""
    import winsound

    def play():
        for freq, ms in TONES.get(kind, []):
            winsound.Beep(freq, ms)

    threading.Thread(target=play, name="beep", daemon=True).start()


class WindowsSignals:
    """Foreground app and time since the last mouse/keyboard input, via plain Win32 calls."""

    def __init__(self):
        import ctypes
        import ctypes.wintypes as wt
        import os

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]

        self._ct, self._wt, self._os = ctypes, wt, os
        self._lii = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
        self._user32, self._kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self._user32.GetForegroundWindow.restype = wt.HWND
        self._user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        self._user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
        self._kernel32.GetTickCount.restype = wt.DWORD
        self._kernel32.OpenProcess.restype = wt.HANDLE
        self._kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        self._kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                                              ctypes.POINTER(wt.DWORD)]
        self._kernel32.CloseHandle.argtypes = [wt.HANDLE]
        self._pid = os.getpid()
        self._names: dict[int, str | None] = {}

    def _exe(self, pid: int) -> str | None:
        if pid in self._names:
            return self._names[pid]
        if len(self._names) > 256:
            self._names.clear()
        name = None
        h = self._kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            try:
                buf = self._ct.create_unicode_buffer(1024)
                size = self._wt.DWORD(len(buf))
                if self._kernel32.QueryFullProcessImageNameW(h, 0, buf, self._ct.byref(size)):
                    name = self._os.path.basename(buf.value).lower()
            finally:
                self._kernel32.CloseHandle(h)
        self._names[pid] = name
        return name

    def __call__(self) -> dict:
        ct = self._ct
        hwnd = self._user32.GetForegroundWindow()
        pid = self._wt.DWORD()
        if hwnd:
            self._user32.GetWindowThreadProcessId(hwnd, ct.byref(pid))
        idle_ms = 0
        if self._user32.GetLastInputInfo(ct.byref(self._lii)):
            idle_ms = (self._kernel32.GetTickCount() - self._lii.dwTime) & 0xFFFFFFFF
        return {"exe": self._exe(pid.value) if pid.value else None, "self": pid.value == self._pid,
                "idle_s": idle_ms / 1000}

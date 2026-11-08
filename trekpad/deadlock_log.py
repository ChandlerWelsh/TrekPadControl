"""Deadlock death / respawn / match events from the game's own console log.

Deadlock has no Game State Integration (tested twice) and Overwolf needs a published app, but
with the official Source 2 launch option `-condebug` the game writes its console to
game\\citadel\\console.log, live (within a couple of seconds). Lines seen in a real online match
(Nov 2026):

    OnGameStateChanged: GameInProgress (7)                         match is on
    [Prediction] Added prediction for player slot 5 (Joyboy)        you (re)spawned; slot 5 is you
    [Prediction] Shutdown prediction for player slot 5.  3 ...      you died (death cam starts)
    [Client] Disconnecting from server: ...                         you left the match

Only reads a text file the game writes on purpose: nothing touches the game process.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

RE_ADDED = re.compile(r"\[Prediction\] Added prediction for player slot (\d+) \((.*)\)")
RE_SHUTDOWN = re.compile(r"\[Prediction\] Shutdown prediction for player slot (\d+)\b")
RE_STATE = re.compile(r"OnGameStateChanged: (\w+)")
RE_DISCONNECT = re.compile(r"\[Client\] Disconnecting from server")
IN_PROGRESS = "GameInProgress"
OVER_STATES = {"PostGame", "GameOver", "Disconnect"}
# A real match goes WaitingForPlayersToJoin -> MatchIntro -> WaitForMapToLoad -> GameInProgress.
# The hideout and the post-match screen jump straight to GameInProgress, so they aren't matches.
INTRO_STATES = {"WaitingForPlayersToJoin", "MatchIntro", "WaitForMapToLoad"}


RE_TS = re.compile(r"^(\d\d)/(\d\d) (\d\d):(\d\d):(\d\d)")


def line_time(line: str, now: datetime | None = None) -> float | None:
    """Epoch seconds from the "MM/DD HH:MM:SS" prefix (the log has no year)."""
    m = RE_TS.match(line)
    if not m:
        return None
    now = now or datetime.now()
    mo, d, h, mi, s = (int(x) for x in m.groups())
    try:
        t = datetime(now.year, mo, d, h, mi, s)
    except ValueError:
        return None
    if t - now > timedelta(days=1):   # a December log read in January
        t = t.replace(year=now.year - 1)
    return t.timestamp()


class DeadlockLogParser:
    """Feed console lines; returns events: "match_start", "death", "respawn", "match_end".
    Also keeps match stats (deaths, time dead) for the app's match card."""

    GAME = "deadlock"

    def __init__(self):
        self.slot: str | None = None      # your player slot (from "Added prediction ... (name)")
        self.player: str | None = None
        self.in_match = False
        self.dead = False
        self.intro_seen = False   # saw the match intro since the last match/disconnect
        self.started_at: float | None = None
        self.deaths = 0
        self.dead_since: float | None = None
        self.time_dead_s = 0.0
        self.last_match: dict | None = None

    def info(self) -> dict:
        """Snapshot for the UI. Times are epoch seconds; the UI adds the live parts."""
        return {"game": self.GAME, "in_match": self.in_match, "started_at": self.started_at,
                "deaths": self.deaths, "dead": self.dead, "dead_since": self.dead_since,
                "time_dead_s": round(self.time_dead_s), "last_match": self.last_match}

    def feed(self, line: str) -> list[str]:
        events: list[str] = []
        m = RE_STATE.search(line)
        if m:
            state = m.group(1)
            if state in INTRO_STATES:
                self.intro_seen = True
            elif state == IN_PROGRESS and not self.in_match and self.intro_seen:
                self.in_match, self.dead, self.intro_seen = True, False, False
                self.started_at = line_time(line) or time.time()
                self.deaths, self.dead_since, self.time_dead_s = 0, None, 0.0
                events.append("match_start")
            elif self.in_match and (state in OVER_STATES or state.startswith("Post")):
                events += self._end(line)
            return events
        if RE_DISCONNECT.search(line):
            if self.in_match:
                return self._end(line)
            self.intro_seen = False
            return []
        m = RE_ADDED.search(line)
        if m:
            self.slot, self.player = m.group(1), m.group(2)
            if self.dead:
                self.dead = False
                self._close_death(line)
                events.append("respawn")
            return events
        m = RE_SHUTDOWN.search(line)
        if m and self.in_match and m.group(1) == self.slot and not self.dead:
            self.dead = True
            self.deaths += 1
            self.dead_since = line_time(line) or time.time()
            events.append("death")
        return events

    def _close_death(self, line: str):
        if self.dead_since is not None:
            self.time_dead_s += max(0.0, (line_time(line) or time.time()) - self.dead_since)
            self.dead_since = None

    def _end(self, line: str) -> list[str]:
        self._close_death(line)
        end = line_time(line) or time.time()
        if self.started_at is not None:
            self.last_match = {"ended_at": end, "duration_s": round(end - self.started_at),
                               "deaths": self.deaths, "time_dead_s": round(self.time_dead_s)}
        self.in_match, self.dead, self.intro_seen = False, False, False
        return ["match_end"]


def find_console_log() -> Path | None:
    """game\\citadel\\console.log in the Deadlock install (Steam libraries from the registry)."""
    roots: list[Path] = []
    try:
        import winreg
        for hive, key, value in [(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                 (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")]:
            try:
                with winreg.OpenKey(hive, key) as k:
                    roots.append(Path(winreg.QueryValueEx(k, value)[0]))
            except OSError:
                pass
    except ImportError:
        pass
    roots.append(Path(r"C:\Program Files (x86)\Steam"))
    libs: list[Path] = []
    for r in roots:
        libs.append(r)
        vdf = r / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            libs += [Path(p.replace("\\\\", "\\")) for p in
                     re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="replace"))]
    for lib in libs:
        d = lib / "steamapps" / "common" / "Deadlock" / "game" / "citadel"
        if d.is_dir():
            return d / "console.log"
    return None


class DeadlockLogWatcher:
    """Tails console.log on a thread and calls on_event(name) for each event.
    Starts at the end of the file (old lines are history); follows the file when Deadlock
    recreates it at launch. `available` is True while the log is being written."""

    def __init__(self, on_event, path: Path | None = None, poll_s: float = 0.25):
        self.on_event = on_event
        self.path = path or find_console_log()
        self.poll_s = poll_s
        self.parser = DeadlockLogParser()
        self.available = False
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._run, name="deadlock-log", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self):
        pos, inode, buf = None, None, ""
        while not self._stop.wait(self.poll_s):
            try:
                if self.path is None or not self.path.exists():
                    self.available = False
                    continue
                st = os.stat(self.path)
                ident = (st.st_ctime_ns, getattr(st, "st_ino", 0))
                if pos is None:
                    # Catch up silently on what's already there (match state, your slot), so
                    # starting mid-match works; only new lines raise events.
                    with open(self.path, "rb") as f:
                        old = f.read(st.st_size)
                    for line in old.decode("utf-8", errors="replace").splitlines():
                        self.parser.feed(line)
                    pos, inode = st.st_size, ident
                elif ident != inode or st.st_size < pos:    # Deadlock relaunched: new file
                    pos, inode, buf = 0, ident, ""
                    self.parser = DeadlockLogParser()
                self.available = time.time() - st.st_mtime < 120
                if st.st_size == pos:
                    continue
                with open(self.path, "rb") as f:
                    f.seek(pos)
                    chunk = f.read()
                pos += len(chunk)
                buf += chunk.decode("utf-8", errors="replace")
                *lines, buf = buf.split("\n")
                for line in lines:
                    for ev in self.parser.feed(line):
                        log.info("deadlock log: %s", ev)
                        try:
                            self.on_event(ev)
                        except Exception:
                            log.exception("deadlock event handler failed")
            except OSError as e:
                log.debug("deadlock log: %s", e)

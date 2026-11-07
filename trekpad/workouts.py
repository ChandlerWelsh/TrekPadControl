"""Workout recording (from the pad's live counters) and storage for Apple Health sync."""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta

from .ftms import METERS_PER_MILE

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS workouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start TEXT NOT NULL,          -- ISO 8601 with UTC offset
    end TEXT NOT NULL,
    duration_s INTEGER NOT NULL,  -- belt-moving time from the pad
    distance_m INTEGER NOT NULL,
    kcal INTEGER NOT NULL,
    max_incline_pct REAL NOT NULL,
    avg_incline_pct REAL NOT NULL,
    synced_at TEXT                -- set when the iPhone Shortcut has logged it to Health
);
"""


class WorkoutStore:
    def __init__(self, path):
        self.path = str(path)
        with closing(self._conn()) as c, c:
            c.executescript(SCHEMA)

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def add(self, w: dict) -> int:
        with closing(self._conn()) as c, c:
            cur = c.execute(
                "INSERT INTO workouts (start, end, duration_s, distance_m, kcal, max_incline_pct, avg_incline_pct)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (w["start"], w["end"], w["duration_s"], w["distance_m"], w["kcal"],
                 w["max_incline_pct"], w["avg_incline_pct"]))
            return cur.lastrowid

    def list(self, unsynced_only: bool = False, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM workouts"
        if unsynced_only:
            sql += " WHERE synced_at IS NULL"
        sql += " ORDER BY start LIMIT ?"
        with closing(self._conn()) as c:
            return [to_api(dict(r)) for r in c.execute(sql, (limit,))]

    def count_unsynced(self) -> int:
        with closing(self._conn()) as c:
            return c.execute("SELECT COUNT(*) FROM workouts WHERE synced_at IS NULL").fetchone()[0]

    def mark_synced(self, wid: int) -> bool:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with closing(self._conn()) as c, c:
            return c.execute("UPDATE workouts SET synced_at = ? WHERE id = ?", (now, wid)).rowcount > 0


def to_api(r: dict) -> dict:
    """Shape a workout for the iPhone Shortcut: ready-to-use units, no maths needed there."""
    start = datetime.fromisoformat(r["start"])
    dur = r["duration_s"]
    return {
        "id": r["id"],
        "start": r["start"],
        "start_local": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end": r["end"],
        "duration_s": dur,
        "duration_min": round(dur / 60, 1),
        "distance_m": r["distance_m"],
        "distance_mi": round(r["distance_m"] / METERS_PER_MILE, 2),
        "kcal": r["kcal"],
        "avg_mph": round((r["distance_m"] / METERS_PER_MILE) / (dur / 3600), 2) if dur else 0,
        "max_incline_pct": r["max_incline_pct"],
        "avg_incline_pct": r["avg_incline_pct"],
        "synced_at": r.get("synced_at"),
    }


class WorkoutRecorder:
    """Turns the pad's running counters into workouts.

    A workout starts when the belt moves and ends when it has been stopped for
    `idle_end_s` (so a short stop and restart stays one workout). The pad resets its
    counters on each start, so resets are detected and added up.
    """

    def __init__(self, store: WorkoutStore, min_seconds: int = 60, idle_end_s: int = 90,
                 on_saved=None, clock=time.monotonic, wall=lambda: datetime.now().astimezone()):
        self.store = store
        self.min_seconds = min_seconds
        self.idle_end_s = idle_end_s
        self.on_saved = on_saved
        self._clock = clock
        self._wall = wall
        self._lock = threading.Lock()
        self._s: dict | None = None

    @property
    def active(self) -> bool:
        return self._s is not None

    def feed(self, rec: dict):
        with self._lock:
            now, wall = self._clock(), self._wall()
            moving = rec.get("speed_kmh", 0) > 0
            el, dist, kcal = rec.get("elapsed_s", 0), rec.get("distance_m", 0), rec.get("kcal", 0)
            s = self._s
            if s is None:
                if not moving:
                    return
                s = self._s = {
                    "start": wall - timedelta(seconds=el), "last_moving": now, "last_moving_wall": wall,
                    "off": [0, 0, 0], "last": [el, dist, kcal], "last_t": now,
                    "max_incline": 0.0, "incline_sum": 0.0, "incline_time": 0.0,
                }
            else:
                last = s["last"]
                if el + 5 < last[0]:  # pad counters reset (stopped, then started again)
                    s["off"] = [s["off"][i] + last[i] for i in range(3)]
                s["last"] = [el, dist, kcal]
            if moving:
                dt = now - s["last_t"]
                inc = rec.get("incline_pct", 0.0)
                if 0 < dt < 10:
                    s["incline_sum"] += inc * dt
                    s["incline_time"] += dt
                s["max_incline"] = max(s["max_incline"], inc)
                s["last_moving"], s["last_moving_wall"] = now, wall
            s["last_t"] = now
        self.tick()

    def tick(self):
        with self._lock:
            if self._s and self._clock() - self._s["last_moving"] > self.idle_end_s:
                self._finish_locked()

    def finish(self):
        with self._lock:
            return self._finish_locked()

    def _finish_locked(self):
        s, self._s = self._s, None
        if s is None:
            return None
        totals = [s["off"][i] + s["last"][i] for i in range(3)]
        duration, distance, kcal = totals
        if duration < self.min_seconds or distance <= 0:
            log.info("discarding short session (%ss, %sm)", duration, distance)
            return None
        w = {
            "start": s["start"].isoformat(timespec="seconds"),
            "end": s["last_moving_wall"].isoformat(timespec="seconds"),
            "duration_s": int(duration), "distance_m": int(distance), "kcal": int(kcal),
            "max_incline_pct": s["max_incline"],
            "avg_incline_pct": round(s["incline_sum"] / s["incline_time"], 1) if s["incline_time"] else 0.0,
        }
        wid = self.store.add(w)
        log.info("saved workout %s: %s", wid, w)
        if self.on_saved:
            self.on_saved(wid)
        return wid

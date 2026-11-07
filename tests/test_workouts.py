import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trekpad.workouts import WorkoutRecorder, WorkoutStore


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.base = datetime(2026, 11, 7, 18, 0, 0, tzinfo=timezone(timedelta(hours=-6)))

    def mono(self):
        return self.t

    def wall(self):
        return self.base + timedelta(seconds=self.t - 1000.0)


def rec(speed_kmh, el, dist, kcal, incline=0.0):
    return {"speed_kmh": speed_kmh, "elapsed_s": el, "distance_m": dist, "kcal": kcal, "incline_pct": incline}


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = WorkoutStore(Path(self.tmp.name) / "w.db")
        self.clock = FakeClock()
        self.r = WorkoutRecorder(self.store, min_seconds=60, idle_end_s=90,
                                 clock=self.clock.mono, wall=self.clock.wall)

    def tearDown(self):
        self.tmp.cleanup()

    def walk(self, seconds, speed=3.2, start_el=0, start_dist=0, incline=0.0, step=1):
        for i in range(0, seconds + 1, step):
            self.r.feed(rec(speed, start_el + i, start_dist + int(i * speed / 3.6), i // 20, incline))
            self.clock.t += step

    def test_walk_then_idle_saves_one_workout(self):
        self.walk(600, incline=2.0)
        self.assertTrue(self.r.active)
        for _ in range(100):  # belt stopped, pad keeps sending zeros
            self.r.feed(rec(0, 600, 533, 30))
            self.clock.t += 1
        self.assertFalse(self.r.active)
        [w] = self.store.list()
        self.assertEqual(w["duration_s"], 600)
        self.assertEqual(w["distance_m"], 533)
        self.assertEqual(w["start_local"], "2026-11-07 18:00:00")
        self.assertEqual(w["max_incline_pct"], 2.0)
        self.assertAlmostEqual(w["avg_incline_pct"], 2.0)
        self.assertAlmostEqual(w["distance_mi"], 0.33)

    def test_stop_and_restart_counts_as_one_workout(self):
        self.walk(300)
        for _ in range(30):  # 30 s break, shorter than the 90 s idle limit
            self.r.feed(rec(0, 300, 266, 15))
            self.clock.t += 1
        self.walk(300)  # pad counters restart from zero
        self.r.finish()
        [w] = self.store.list()
        self.assertEqual(w["duration_s"], 600)
        self.assertEqual(w["distance_m"], 532)

    def test_short_test_runs_are_not_saved(self):
        self.walk(40)
        self.r.finish()
        self.assertEqual(self.store.list(), [])

    def test_sync_flag(self):
        self.walk(120)
        self.r.finish()
        [w] = self.store.list(unsynced_only=True)
        self.assertTrue(self.store.mark_synced(w["id"]))
        self.assertEqual(self.store.list(unsynced_only=True), [])
        self.assertEqual(self.store.count_unsynced(), 0)


if __name__ == "__main__":
    unittest.main()

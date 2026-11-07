import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlite3

from trekpad.workouts import (KG_PER_LB, WorkoutRecorder, WorkoutStore, o2_to_kcal, walking_o2_ml_per_kg,
                              weight_kg_from_config)


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


class WeightCalorieTests(unittest.TestCase):
    """Calories from body weight (ACSM walking equation, active energy only)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = WorkoutStore(Path(self.tmp.name) / "w.db")
        self.clock = FakeClock()
        self.r = WorkoutRecorder(self.store, min_seconds=60, idle_end_s=90, clock=self.clock.mono,
                                 wall=self.clock.wall, weight_kg=187 * KG_PER_LB)

    def tearDown(self):
        self.tmp.cleanup()

    def walk(self, seconds, speed=3.2, incline=0.0):
        for i in range(seconds + 1):
            self.r.feed(rec(speed, i, int(i * speed / 3.6), i // 20, incline))
            self.clock.t += 1

    def test_formula(self):
        # 1 mile flat at 187 lb: 0.1 ml/kg/m * 1609 m * 84.8 kg / 1000 * 5 kcal/L ~= 68 kcal
        self.assertAlmostEqual(o2_to_kcal(walking_o2_ml_per_kg(1609.344, 0), 187 * KG_PER_LB), 68.3, places=1)
        # incline adds 1.8 * grade per metre: 5% grade is 0.19 vs 0.1
        self.assertAlmostEqual(walking_o2_ml_per_kg(1000, 5), 190)

    def test_flat_walk_uses_weight_not_pad(self):
        self.walk(600)
        self.r.finish()
        [w] = self.store.list()
        self.assertEqual(w["distance_m"], 533)
        self.assertEqual(w["kcal"], round(o2_to_kcal(533 * 0.1, 187 * KG_PER_LB)))  # 23
        self.assertEqual(w["pad_kcal"], 30)
        self.assertEqual(self.r.live_kcal(), w["kcal"])  # tile keeps showing the last walk

    def test_incline_raises_calories(self):
        self.walk(600, incline=5.0)
        self.r.finish()
        [w] = self.store.list()
        self.assertEqual(w["kcal"], round(o2_to_kcal(533 * 0.19, 187 * KG_PER_LB)))  # 43

    def test_restart_adds_distance_from_both_parts(self):
        self.walk(300)
        for _ in range(30):
            self.r.feed(rec(0, 300, 266, 15))
            self.clock.t += 1
        self.walk(300)
        live = self.r.live_kcal()
        self.r.finish()
        [w] = self.store.list()
        self.assertEqual(w["distance_m"], 532)
        self.assertEqual(w["kcal"], round(o2_to_kcal(532 * 0.1, 187 * KG_PER_LB)))
        self.assertEqual(live, w["kcal"])

    def test_pad_mode(self):
        self.r.weight_kg = None
        self.walk(600)
        self.assertIsNone(self.r.live_kcal())
        self.r.finish()
        [w] = self.store.list()
        self.assertEqual(w["kcal"], 30)
        self.assertEqual(w["pad_kcal"], 30)

    def test_config(self):
        self.assertAlmostEqual(weight_kg_from_config({"calorie_source": "weight", "weight_lb": 187}), 84.82, places=2)
        self.assertIsNone(weight_kg_from_config({"calorie_source": "pad", "weight_lb": 187}))
        self.assertIsNone(weight_kg_from_config({"calorie_source": "weight", "weight_lb": 0}))
        self.assertIsNone(weight_kg_from_config({"calorie_source": "weight", "weight_lb": "abc"}))

    def test_old_database_is_upgraded(self):
        path = Path(self.tmp.name) / "old.db"
        with sqlite3.connect(path) as c:
            c.execute("CREATE TABLE workouts (id INTEGER PRIMARY KEY AUTOINCREMENT, start TEXT NOT NULL,"
                      " end TEXT NOT NULL, duration_s INTEGER NOT NULL, distance_m INTEGER NOT NULL,"
                      " kcal INTEGER NOT NULL, max_incline_pct REAL NOT NULL, avg_incline_pct REAL NOT NULL,"
                      " synced_at TEXT)")
            c.execute("INSERT INTO workouts (start, end, duration_s, distance_m, kcal, max_incline_pct,"
                      " avg_incline_pct) VALUES ('2026-11-01T18:00:00-06:00', '2026-11-01T18:30:00-06:00',"
                      " 1800, 2000, 90, 0, 0)")
        c.close()
        store = WorkoutStore(path)
        [w] = store.list()
        self.assertEqual(w["kcal"], 90)
        self.assertIsNone(w["pad_kcal"])


if __name__ == "__main__":
    unittest.main()

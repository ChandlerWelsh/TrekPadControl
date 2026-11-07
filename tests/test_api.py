"""Calorie settings and the live calorie number over the local API."""
import json
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from trekpad.api import ApiServer
from trekpad.workouts import KG_PER_LB, WorkoutRecorder, WorkoutStore


class FakePad:
    max_mph = 4.0

    def snapshot(self):
        return {"connected": True, "running": True, "kcal": 12}


class ApiCalorieTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = {"api_port": 0, "api_token": "t", "theme": "system", "max_speed_mph": 4.0,
                    "speed_step_mph": 0.1, "incline_step_pct": 1, "calorie_source": "weight", "weight_lb": 187}
        self.recorder = WorkoutRecorder(WorkoutStore(Path(self.tmp.name) / "w.db"), weight_kg=187 * KG_PER_LB)
        patcher = mock.patch("trekpad.api.config.save")
        self.saved = patcher.start()
        self.addCleanup(patcher.stop)
        self.api = ApiServer(FakePad(), self.recorder.store, self.recorder, self.cfg, host="127.0.0.1")
        self.api.start()
        self.base = f"http://127.0.0.1:{self.api.httpd.server_address[1]}"

    def tearDown(self):
        self.api.stop()
        self.tmp.cleanup()

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method, headers={"X-Token": "t"},
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_settings_show_calorie_options(self):
        _, s = self.call("GET", "/api/settings")
        self.assertEqual((s["calorie_source"], s["weight_lb"]), ("weight", 187))

    def test_change_weight_and_source(self):
        code, s = self.call("POST", "/api/settings", {"weight_lb": 180})
        self.assertEqual((code, s["weight_lb"]), (200, 180))
        self.assertAlmostEqual(self.recorder.weight_kg, 180 * KG_PER_LB)
        self.call("POST", "/api/settings", {"calorie_source": "pad"})
        self.assertIsNone(self.recorder.weight_kg)
        self.assertEqual(self.cfg["calorie_source"], "pad")
        self.assertTrue(self.saved.called)

    def test_bad_values_are_rejected(self):
        self.assertEqual(self.call("POST", "/api/settings", {"weight_lb": 5})[0], 400)
        self.assertEqual(self.call("POST", "/api/settings", {"calorie_source": "guess"})[0], 400)
        self.assertEqual(self.cfg["weight_lb"], 187)

    def test_status_kcal_follows_source(self):
        self.recorder.feed({"speed_kmh": 4.0, "elapsed_s": 600, "distance_m": 1609, "kcal": 12, "incline_pct": 0})
        _, s = self.call("GET", "/api/status")
        self.assertEqual((s["kcal"], s["pad_kcal"]), (68, 12))
        self.call("POST", "/api/settings", {"calorie_source": "pad"})
        _, s = self.call("GET", "/api/status")
        self.assertEqual(s["kcal"], 12)


if __name__ == "__main__":
    unittest.main()

"""Dota 2 Game State Integration: parsing, the receiver endpoint, and the config file."""
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from trekpad import dota_gsi
from trekpad.api import ApiServer
from trekpad.dota_gsi import DotaGSI


def update(state, alive=True, respawn=0, k=0, d=0, a=0, clock=0, paused=False, win="none", spectating=False):
    """A trimmed Dota 2 GSI payload, as the game sends it for the local player."""
    body = {"provider": {"name": "Dota 2", "appid": 570},
            "map": {"name": "start", "game_state": f"DOTA_GAMERULES_STATE_{state}", "clock_time": clock,
                    "paused": paused, "win_team": win},
            "auth": {"token": "dota-token"}}
    if not spectating:
        body["player"] = {"name": "Player", "kills": k, "deaths": d, "assists": a, "last_hits": 10, "team_name": "radiant"}
        body["hero"] = {"name": "npc_dota_hero_crystal_maiden", "level": 6, "alive": alive, "respawn_seconds": respawn}
    return body


class Clock:
    def __init__(self):
        self.t = 1_794_000_000.0

    def __call__(self):
        return self.t


class Parsing(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.events = []
        self.g = DotaGSI(self.events.append, clock=self.clock)

    def feed(self, body, advance=1.0):
        self.g.feed(body)
        self.clock.t += advance

    def test_a_whole_match(self):
        self.feed(update("HERO_SELECTION"))
        self.feed(update("STRATEGY_TIME"))
        self.assertEqual(self.events, [])                      # picks don't count
        self.assertFalse(self.g.info()["in_match"])
        self.feed(update("PRE_GAME", clock=-60))
        self.assertEqual(self.events, ["match_start"])         # heroes on the map
        self.feed(update("GAME_IN_PROGRESS", clock=300))
        self.feed(update("GAME_IN_PROGRESS", alive=False, respawn=20, d=1, clock=301), advance=20)
        info = self.g.info()
        self.assertTrue(info["dead"])
        self.assertEqual(info["respawn_at"], self.clock.t - 20 + 20)
        self.feed(update("GAME_IN_PROGRESS", alive=True, d=1, k=2, a=3, clock=321))
        self.assertEqual(self.events, ["match_start", "death", "respawn"])
        info = self.g.info()
        self.assertEqual((info["kills"], info["deaths"], info["assists"]), (2, 1, 3))
        self.assertEqual(info["time_dead_s"], 20)
        self.assertEqual(info["hero"], "Crystal Maiden")
        self.feed(update("POST_GAME", k=2, d=1, a=3, clock=2400, win="radiant"))
        self.assertEqual(self.events[-1], "match_end")
        lm = self.g.info()["last_match"]
        self.assertEqual((lm["kills"], lm["deaths"], lm["assists"], lm["won"]), (2, 1, 3, True))
        self.assertEqual(lm["duration_s"], 321)               # last clock seen in the match

    def test_dead_at_the_end_still_counts_time_dead(self):
        self.feed(update("GAME_IN_PROGRESS"))
        self.feed(update("GAME_IN_PROGRESS", alive=False, respawn=40), advance=15)
        self.feed(update("POST_GAME", win="dire"))
        self.assertEqual(self.g.info()["last_match"]["time_dead_s"], 15)
        self.assertFalse(self.g.info()["last_match"]["won"])

    def test_spectating_is_not_playing(self):
        self.feed(update("GAME_IN_PROGRESS", spectating=True))
        self.assertEqual(self.events, [])
        self.assertFalse(self.g.info()["in_match"])

    def test_goes_quiet_when_dota_stops_sending(self):
        self.assertIsNone(self.g.info())                      # nothing received yet
        self.feed(update("GAME_IN_PROGRESS"))
        self.assertIsNotNone(self.g.info())
        self.clock.t += dota_gsi.STALE_S + 1
        self.assertIsNone(self.g.info())

    def test_ignores_garbage(self):
        self.assertEqual(self.g.feed(["not", "a", "dict"]), [])
        self.assertEqual(self.g.feed({}), [])


class Endpoint(unittest.TestCase):
    def setUp(self):
        self.received = []

        class FakeDota:
            def feed(_self, body):
                self.received.append(body)

        self.cfg = {"api_port": 0, "api_token": "belt-token", "dota_gsi_token": "dota-token"}
        self.api = ApiServer(None, None, None, self.cfg, host="127.0.0.1", dota=FakeDota())
        self.api.start()
        self.url = f"http://127.0.0.1:{self.api.httpd.server_address[1]}/api/gsi/dota2"

    def tearDown(self):
        self.api.stop()

    def post(self, body):
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status
        except urllib.error.HTTPError as e:
            e.read()
            return e.code

    def test_accepts_dotas_token_from_the_body(self):
        self.assertEqual(self.post(update("GAME_IN_PROGRESS")), 200)
        self.assertEqual(len(self.received), 1)

    def test_rejects_a_wrong_or_missing_token(self):
        bad = update("GAME_IN_PROGRESS")
        bad["auth"]["token"] = "nope"
        self.assertEqual(self.post(bad), 401)
        del bad["auth"]
        self.assertEqual(self.post(bad), 401)
        self.assertEqual(self.received, [])

    def test_belt_token_does_not_work_here(self):
        b = update("GAME_IN_PROGRESS")
        b["auth"]["token"] = "belt-token"
        self.assertEqual(self.post(b), 401)


class ConfigFile(unittest.TestCase):
    def test_writes_and_updates_the_cfg(self):
        with tempfile.TemporaryDirectory() as d:
            dota = Path(d)
            (dota / "game" / "dota").mkdir(parents=True)
            path = dota_gsi.install_config(8765, "abc", dota_dir=dota)
            self.assertEqual(path, dota / "game/dota/cfg/gamestate_integration/gamestate_integration_trekpad.cfg")
            text = path.read_text(encoding="utf-8")
            self.assertIn('"uri"           "http://127.0.0.1:8765/api/gsi/dota2"', text)
            self.assertIn('"token"     "abc"', text)
            dota_gsi.install_config(9000, "xyz", dota_dir=dota)
            self.assertIn("9000", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

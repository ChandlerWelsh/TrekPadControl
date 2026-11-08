"""Game mode logic with a simulated pad, clock and Windows signals."""
import concurrent.futures
import unittest

from trekpad import gamemode
from trekpad.gamemode import GameMode


class FakePad:
    def __init__(self):
        self.max_mph = 4.0
        self.state = {"connected": True, "running": True, "speed_mph": 1.5, "incline_pct": 0.0}
        self.commands = []
        self.messages = []

    def snapshot(self):
        return dict(self.state)

    def submit(self, name, *args):
        self.commands.append((name, *args))
        if name == "set_speed":
            self.state["speed_mph"] = args[0]
        elif name == "set_incline":
            self.state["incline_pct"] = args[0]
        f = concurrent.futures.Future()
        f.set_result(None)
        return f

    def _msg(self, text):
        self.messages.append(text)


def defaults(**kw):
    return {"enabled": True, "profile": "aim", "games": ["deadlock.exe"], "easy_mph": 2.0, "hard_mph": 2.8,
            "start_incline_pct": 0, "max_incline_pct": 4, "climb_every_min": 5, "idle_seconds": 8,
            "sounds": True, **kw}


class GameModeTests(unittest.TestCase):
    def setUp(self):
        self.t = 1000.0
        self.pad = FakePad()
        self.sig = {"exe": "deadlock.exe", "self": False, "idle_s": 0.0}
        self.beeps = []
        self.saves = 0
        self.cfg = defaults()
        self.gm = self.make()

    def make(self):
        def save():
            self.saves += 1
        return GameMode(self.pad, self.cfg, lambda: dict(self.sig), beep=self.beeps.append, save=save,
                        clock=lambda: self.t)

    def run_for(self, seconds, step=0.5):
        for _ in range(int(seconds / step)):
            self.gm.tick()
            self.t += step

    def speeds(self):
        return [c[1] for c in self.pad.commands if c[0] == "set_speed"]

    def inclines(self):
        return [c[1] for c in self.pad.commands if c[0] == "set_incline"]

    # ---------------------------------------------------------------- basics
    def test_off_does_nothing(self):
        self.cfg["enabled"] = False
        self.run_for(30)
        self.assertEqual(self.pad.commands, [])
        self.assertEqual(self.gm.status()["state"], "off")

    def test_belt_stopped_does_nothing(self):
        self.pad.state.update(running=False, speed_mph=0.0)
        self.run_for(30)
        self.assertEqual(self.pad.commands, [])
        self.assertEqual(self.gm.status()["state"], "belt_stopped")

    def test_ramps_up_to_easy_pace_after_a_warning(self):
        self.gm.tick()
        self.assertEqual(self.beeps, ["up"])
        self.assertEqual(self.pad.commands, [])
        self.t += 2.9
        self.gm.tick()
        self.assertEqual(self.pad.commands, [])  # still inside the 3 s warning
        self.run_for(20)
        self.assertEqual(self.speeds(), [1.6, 1.7, 1.8, 1.9, 2.0])
        self.assertEqual(self.beeps, ["up"])  # one warning per ramp
        self.assertEqual(self.gm.status()["state"], "playing")

    def test_steps_are_at_least_two_seconds_apart_going_up(self):
        self.run_for(3.5)
        n = len(self.speeds())
        self.run_for(1.5)
        self.assertEqual(len(self.speeds()), n)

    def test_idle_goes_hard_then_input_eases_back_quickly(self):
        self.pad.state["speed_mph"] = 2.0
        self.run_for(5)
        self.assertEqual(self.pad.commands, [])
        self.sig["idle_s"] = 10
        self.run_for(25)
        self.assertEqual(self.gm.status()["state"], "idle")
        self.assertEqual(self.pad.state["speed_mph"], 2.8)
        self.assertEqual(self.beeps, ["up"])
        self.pad.commands.clear()
        self.sig["idle_s"] = 0
        self.run_for(9)  # 0.1 mph per second down, no warning needed
        self.assertEqual(self.speeds(), [2.7, 2.6, 2.5, 2.4, 2.3, 2.2, 2.1, 2.0])
        self.assertEqual(self.beeps, ["up"])

    def test_hard_pace_never_exceeds_max_speed(self):
        self.pad.max_mph = 2.5
        self.pad.state["speed_mph"] = 2.0
        self.sig["idle_s"] = 30
        self.run_for(30)
        self.assertEqual(max(self.speeds()), 2.5)
        self.run_for(30)
        self.assertEqual(self.gm.status()["state"], "idle")  # not paused by its own cap

    def test_quick_alt_tab_is_ignored_but_leaving_eases_to_easy(self):
        self.pad.state["speed_mph"] = 2.0
        self.sig["idle_s"] = 30
        self.run_for(30)
        self.assertEqual(self.pad.state["speed_mph"], 2.8)
        self.sig.update(exe="chrome.exe", idle_s=0)
        self.run_for(3)
        self.assertEqual(self.gm.status()["state"], "playing")  # within the 5 s grace
        self.run_for(15)
        self.assertEqual(self.gm.status()["state"], "not_in_game")
        self.assertEqual(self.pad.state["speed_mph"], 2.0)
        self.assertEqual(self.gm.status()["last_app"], "chrome.exe")

    def test_own_window_is_not_remembered_as_last_app(self):
        self.sig.update(exe="trekpad.exe", self=True)
        self.gm.tick()
        self.assertIsNone(self.gm.status()["last_app"])

    def test_manual_speed_change_pauses_for_a_minute(self):
        self.run_for(20)
        self.assertEqual(self.pad.state["speed_mph"], 2.0)
        self.pad.state["speed_mph"] = 3.0  # you used the remote
        self.run_for(10)
        st = self.gm.status()
        self.assertEqual(st["state"], "paused")
        self.assertTrue(any("paused" in m for m in self.pad.messages))
        self.pad.commands.clear()
        self.run_for(45)
        self.assertEqual(self.pad.commands, [])
        self.run_for(30)
        self.assertEqual(self.gm.status()["state"], "playing")
        self.assertEqual(self.pad.state["speed_mph"], 2.0)

    # ---------------------------------------------------------------- game events
    def test_death_event_means_hard_even_with_input(self):
        self.pad.state["speed_mph"] = 2.0
        self.gm.event("death")
        self.run_for(25)
        self.assertEqual(self.gm.status()["state"], "dead")
        self.assertEqual(self.pad.state["speed_mph"], 2.8)
        self.gm.event("respawn")
        self.run_for(10)
        self.assertEqual(self.gm.status()["state"], "playing")
        self.assertEqual(self.pad.state["speed_mph"], 2.0)

    def test_death_without_respawn_expires(self):
        self.gm.event("death")
        self.run_for(gamemode.DEAD_TIMEOUT_S + 2)
        self.assertEqual(self.gm.status()["state"], "playing")

    def test_unknown_event_is_rejected(self):
        with self.assertRaises(ValueError):
            self.gm.event("explode")

    # ---------------------------------------------------------------- climb
    def climb_setup(self, profile="climb"):
        self.cfg["profile"] = profile
        self.pad.state["speed_mph"] = 2.0

    def test_climb_waits_for_an_idle_moment(self):
        self.climb_setup()
        self.run_for(5 * 60 + 1)
        self.assertEqual(self.gm.status()["target_incline_pct"], 1)
        self.assertEqual(self.inclines(), [])  # you're active, so it waits...
        self.sig["idle_s"] = 10
        self.run_for(2)
        self.assertEqual(self.beeps, ["incline"])
        self.assertEqual(self.inclines(), [])  # ...beeps, then waits 3 s
        self.run_for(2)
        self.assertEqual(self.inclines(), [1])
        self.assertEqual(self.speeds(), [])  # climb alone never touches speed

    def test_climb_goes_ahead_after_a_minute_even_if_active(self):
        self.climb_setup()
        self.run_for(5 * 60 + 1)
        self.run_for(gamemode.INCLINE_FORCE_AFTER_S + gamemode.WARN_S + 1)
        self.assertEqual(self.inclines(), [1])

    def test_climb_stops_at_max_and_resets_after_leaving(self):
        self.climb_setup()
        self.cfg["max_incline_pct"] = 2
        self.sig["idle_s"] = 30  # always a safe moment
        self.run_for(16 * 60)
        self.assertEqual(self.inclines(), [1, 2])
        self.sig["exe"] = "explorer.exe"
        self.run_for(gamemode.LEAVE_RESET_S + 60)
        self.assertEqual(self.inclines(), [1, 2, 0])  # not in game = safe to step down (2% max per change)

    def test_your_incline_change_moves_the_climb(self):
        self.climb_setup()
        self.sig["idle_s"] = 30
        self.run_for(5 * 60 + 10)
        self.assertEqual(self.inclines(), [1])
        self.run_for(15)
        self.pad.state["incline_pct"] = 3  # you raised it by 2
        self.run_for(30)
        self.assertEqual(self.gm.status()["target_incline_pct"], 3)
        self.assertEqual(self.inclines(), [1])  # no fight
        self.run_for(5 * 60)
        self.assertEqual(self.inclines(), [1, 4])

    def test_match_start_restarts_the_climb(self):
        self.climb_setup()
        self.run_for(6 * 60)
        self.gm.event("match_start")
        self.gm.tick()
        self.assertEqual(self.gm.status()["target_incline_pct"], 0)

    def test_both_profile_does_speed_and_incline(self):
        self.climb_setup("both")
        self.sig["idle_s"] = 30
        self.run_for(5 * 60 + 30)
        self.assertEqual(self.pad.state["speed_mph"], 2.8)
        self.assertEqual(self.inclines(), [1])

    # ---------------------------------------------------------------- settings
    def test_settings_validation_and_ordering(self):
        self.gm.apply_settings({"hard_mph": 1.5})
        self.assertEqual((self.cfg["easy_mph"], self.cfg["hard_mph"]), (1.5, 1.5))
        self.gm.apply_settings({"easy_mph": 2.2})
        self.assertEqual((self.cfg["easy_mph"], self.cfg["hard_mph"]), (2.2, 2.2))
        self.gm.apply_settings({"start_incline_pct": 6})
        self.assertEqual(self.cfg["max_incline_pct"], 6)
        for bad in ({"hard_mph": 9}, {"profile": "turbo"}, {"max_incline_pct": 15}, {"add_game": "notes.txt"},
                    {"idle_seconds": 1}):
            with self.assertRaises(ValueError):
                self.gm.apply_settings(bad)
        self.gm.apply_settings({"add_game": "Game.EXE"})
        self.assertIn("game.exe", self.cfg["games"])
        self.gm.apply_settings({"remove_game": "deadlock.exe"})
        self.assertNotIn("deadlock.exe", self.cfg["games"])
        self.assertGreater(self.saves, 0)

    def test_toggle_messages_and_beeps(self):
        self.gm.apply_settings({"enabled": "false"})
        self.assertFalse(self.cfg["enabled"])
        self.assertEqual(self.beeps, ["off"])
        self.gm.toggle()
        self.assertTrue(self.cfg["enabled"])
        self.assertEqual(self.beeps, ["off", "on"])

    def test_sounds_off(self):
        self.cfg["sounds"] = False
        self.run_for(20)
        self.assertEqual(self.beeps, [])
        self.assertEqual(self.pad.state["speed_mph"], 2.0)

    # ---------------------------------------------------------------- Deadlock match state
    def match(self, in_match):
        self.info = {"game": "deadlock", "in_match": in_match, "deaths": 0}
        self.gm.sources = {"deadlock": lambda: self.info}

    def test_waits_for_the_match_to_start(self):
        self.match(False)
        self.sig["idle_s"] = 30          # idle in the hideout or menus: still nothing
        self.run_for(30)
        self.assertEqual(self.pad.commands, [])
        self.assertEqual(self.gm.status()["state"], "waiting_match")
        self.info["in_match"] = True
        self.sig["idle_s"] = 0
        self.run_for(25)
        self.assertEqual(self.pad.state["speed_mph"], 2.0)
        self.assertEqual(self.gm.status()["state"], "playing")

    def test_no_climb_outside_a_match(self):
        self.cfg["profile"] = "climb"
        self.cfg["climb_every_min"] = 1
        self.match(False)
        self.run_for(180)
        self.assertEqual(self.inclines(), [])

    def test_match_end_brings_the_speed_back_down_then_leaves_the_belt_alone(self):
        self.match(True)
        self.sig["idle_s"] = 30          # dead/idle: hard pace
        self.run_for(40)
        self.assertEqual(self.pad.state["speed_mph"], 2.8)
        self.info["in_match"] = False     # match over
        self.run_for(20)
        self.assertEqual(self.pad.state["speed_mph"], 2.0)
        self.assertEqual(self.gm.status()["state"], "waiting_match")
        n = len(self.pad.commands)
        self.pad.state["speed_mph"] = 1.4  # you change it between matches: game mode doesn't react
        self.run_for(30)
        self.assertEqual(len(self.pad.commands), n)

    def test_another_game_in_front_ignores_deadlocks_match_state(self):
        self.cfg["games"] = ["deadlock.exe", "dota2.exe"]
        self.match(False)
        self.sig["exe"] = "dota2.exe"
        self.run_for(25)
        self.assertEqual(self.pad.state["speed_mph"], 2.0)   # plays as before
        self.assertEqual(self.gm.status()["state"], "playing")

    def test_status_includes_match_stats(self):
        self.match(True)
        self.gm.tick()
        st = self.gm.status()
        self.assertEqual(st["match"]["game"], "deadlock")
        self.assertEqual(st["source_name"], "Deadlock")

    def test_uses_the_source_for_the_game_in_front(self):
        dota = {"game": "dota2", "in_match": True, "deaths": 1}
        self.match(False)                                     # Deadlock: no match
        self.gm.sources["dota2"] = lambda: dota
        self.sig["exe"] = "dota2.exe"                         # counts as a game: it has a source
        self.run_for(25)
        self.assertEqual(self.gm.status()["source_name"], "Dota 2")
        self.assertEqual(self.pad.state["speed_mph"], 2.0)
        dota["in_match"] = False                              # Dota match over -> waits
        self.run_for(10)
        self.assertEqual(self.gm.status()["state"], "waiting_match")

    def test_looking_at_trekpad_shows_the_game_with_a_match(self):
        self.match(False)
        self.gm.sources["dota2"] = lambda: {"game": "dota2", "in_match": True}
        self.sig.update(exe="trekpad.exe", self=True)
        self.t += 10
        self.gm.tick()
        self.assertEqual(self.gm.status()["match"]["game"], "dota2")


if __name__ == "__main__":
    unittest.main()

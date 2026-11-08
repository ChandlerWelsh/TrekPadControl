"""Deadlock console.log parsing, using lines from a real online match (Nov 2026)."""
import tempfile
import time
import unittest
from pathlib import Path

from trekpad.deadlock_log import DeadlockLogParser, DeadlockLogWatcher

HIDEOUT_AND_MATCH = """\
11/07 21:23:58 OnGameStateChanged: GameInProgress (7)
11/07 21:23:58 [Prediction] Added prediction for player slot 0 (Player)
11/07 21:24:58 [Client] Disconnecting from server: NETWORK_DISCONNECT_LOOPDEACTIVATE
11/07 21:25:03 OnGameStateChanged: WaitingForPlayersToJoin (2)
11/07 21:25:03 [Prediction] Added prediction for player slot 5 (Player)
11/07 21:25:07 OnGameStateChanged: MatchIntro (4)
11/07 21:25:12 OnGameStateChanged: WaitForMapToLoad (5)
11/07 21:25:13 OnGameStateChanged: GameInProgress (7)
11/07 21:26:42 Cannot apply Ragdoll impulse, body part root_motion is not physicalized
11/07 21:26:53 [Prediction] Shutdown prediction for player slot 5.  3 predictable entities and 0 client-created entities
11/07 21:27:14 [Prediction] Added prediction for player slot 5 (Player)
11/07 21:29:19 [Prediction] Shutdown prediction for player slot 5.  3 predictable entities and 0 client-created entities
11/07 21:29:40 [Prediction] Added prediction for player slot 5 (Player)
"""


def run(text):
    p, out = DeadlockLogParser(), []
    for line in text.splitlines():
        out += p.feed(line)
    return p, out


class Parser(unittest.TestCase):
    def test_real_match(self):
        p, events = run(HIDEOUT_AND_MATCH)
        # the hideout isn't a match; the real match (after its intro) has two deaths
        self.assertEqual(events, ["match_start", "death", "respawn", "death", "respawn"])
        self.assertEqual((p.slot, p.player, p.in_match, p.dead), ("5", "Player", True, False))

    def test_credits_screen_after_the_match_is_not_a_new_match(self):
        _, events = run(HIDEOUT_AND_MATCH + """\
11/07 21:42:45 OnGameStateChanged: PostGame (8)
11/07 21:42:55 OnGameStateChanged: End (11)
11/07 21:42:55 [Client] Disconnecting from server: NETWORK_DISCONNECT_DISCONNECT_BY_USER
11/07 21:42:56 [Client] Disconnecting from server: NETWORK_DISCONNECT_LOOPDEACTIVATE
11/07 21:43:02 OnGameStateChanged: HeroSelection (3)
11/07 21:43:02 [Prediction] Added prediction for player slot 0 (Player)
11/07 21:43:02 OnGameStateChanged: GameInProgress (7)
11/07 21:43:30 [Prediction] Shutdown prediction for player slot 0.  3 predictable entities
""")
        self.assertEqual(events[-1], "match_end")
        self.assertEqual(events.count("match_start"), 1)

    def test_other_players_slot_is_not_your_death(self):
        _, events = run("x OnGameStateChanged: MatchIntro (4)\nx OnGameStateChanged: GameInProgress (7)\n"
                        "x [Prediction] Added prediction for player slot 5 (Player)\n"
                        "x [Prediction] Shutdown prediction for player slot 3.  1 predictable entities\n")
        self.assertEqual(events, ["match_start"])

    def test_no_death_outside_a_match(self):
        _, events = run("x [Prediction] Added prediction for player slot 5 (Player)\n"
                        "x [Prediction] Shutdown prediction for player slot 5.  3 predictable entities\n")
        self.assertEqual(events, [])

    def test_leaving_the_match_ends_it_and_clears_death(self):
        p, events = run(HIDEOUT_AND_MATCH + "x [Prediction] Shutdown prediction for player slot 5.  3 predictable\n"
                        "x [Client] Disconnecting from server: NETWORK_DISCONNECT_EXITING\n")
        self.assertEqual(events[-2:], ["death", "match_end"])
        self.assertFalse(p.dead)

    def test_post_game_ends_the_match(self):
        _, events = run(HIDEOUT_AND_MATCH + "x OnGameStateChanged: PostGame (8)\n")
        self.assertEqual(events[-1], "match_end")

    def test_repeated_shutdown_is_one_death(self):
        _, events = run(HIDEOUT_AND_MATCH + "x [Prediction] Shutdown prediction for player slot 5.  3\n"
                        "x [Prediction] Shutdown prediction for player slot 5.  3\n")
        self.assertEqual(events.count("death"), 3)  # two in the match text + one here

    def test_match_stats(self):
        p, _ = run(HIDEOUT_AND_MATCH)
        info = p.info()
        self.assertEqual((info["game"], info["in_match"], info["deaths"], info["dead"]), ("deadlock", True, 2, False))
        self.assertEqual(info["time_dead_s"], 42)      # 21 s + 21 s
        p.feed("11/07 21:30:00 [Prediction] Shutdown prediction for player slot 5.  3 predictable")
        self.assertTrue(p.info()["dead"])
        self.assertEqual(p.info()["deaths"], 3)
        p.feed("11/07 21:31:13 OnGameStateChanged: PostGame (8)")
        lm = p.info()["last_match"]
        self.assertEqual(lm["duration_s"], 6 * 60)      # 21:25:13 -> 21:31:13
        self.assertEqual((lm["deaths"], lm["time_dead_s"]), (3, 42 + 73))
        self.assertFalse(p.info()["in_match"])

    def test_hideout_deaths_are_ignored(self):
        _, events = run("x OnGameStateChanged: GameInProgress (7)\n"
                        "x [Prediction] Added prediction for player slot 0 (Player)\n"
                        "x [Prediction] Shutdown prediction for player slot 0.  3 predictable\n")
        self.assertEqual(events, [])


class Watcher(unittest.TestCase):
    def wait_for(self, got, n, timeout=3.0):
        end = time.time() + timeout
        while len(got) < n and time.time() < end:
            time.sleep(0.02)

    def test_starts_mid_match_and_follows_new_lines_and_relaunch(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "console.log"
            path.write_text(HIDEOUT_AND_MATCH, encoding="utf-8")   # already mid-match
            got = []
            w = DeadlockLogWatcher(got.append, path=path, poll_s=0.02)
            w.start()
            try:
                time.sleep(0.15)
                self.assertEqual(got, [])                          # history raises nothing
                with path.open("a", encoding="utf-8") as f:
                    f.write("x [Prediction] Shutdown prediction for player slot 5.  3 predictable\n")
                self.wait_for(got, 1)
                self.assertEqual(got, ["death"])                   # knew the match and the slot
                self.assertTrue(w.available)
                with path.open("a", encoding="utf-8") as f:
                    f.write("x [Prediction] Added prediction for player slot 5 (Player)\n")
                self.wait_for(got, 2)
                self.assertEqual(got, ["death", "respawn"])
                # Deadlock relaunches: a new, shorter file
                path.unlink()
                time.sleep(0.05)
                path.write_text("x OnGameStateChanged: MatchIntro (4)\nx OnGameStateChanged: GameInProgress (7)\n",
                                encoding="utf-8")
                self.wait_for(got, 3)
                self.assertEqual(got[-1], "match_start")
            finally:
                w.stop()


if __name__ == "__main__":
    unittest.main()

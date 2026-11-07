"""Pad logic that doesn't need Bluetooth: display hold during incline changes, stale-link refusal."""
import asyncio
import time
import unittest

from trekpad.pad import PadController, PadError


class FakeClient:
    is_connected = True  # what Windows claims even after the pad has silently dropped


class DisplayHold(unittest.TestCase):
    def setUp(self):
        self.pad = PadController("AA:BB", max_mph=4.0)
        self.pad._set(connected=True, speed_mph=2.7)  # pad's brief x1.6 jump after an incline change

    def test_jump_hidden_while_incline_settles(self):
        self.pad._hold_mph, self.pad._hold_until = 1.7, time.monotonic() + 5
        s = self.pad.snapshot()
        self.assertEqual(s["speed_mph"], 1.7)
        self.assertEqual(s["reported_speed_mph"], 2.7)

    def test_real_speed_shown_once_window_ends(self):
        self.pad._hold_mph, self.pad._hold_until = 1.7, time.monotonic() - 0.1
        self.assertEqual(self.pad.snapshot()["speed_mph"], 2.7)

    def test_slower_than_hold_is_shown(self):
        self.pad._set(speed_mph=1.0)  # e.g. slowed with the remote
        self.pad._hold_mph, self.pad._hold_until = 1.7, time.monotonic() + 5
        self.assertEqual(self.pad.snapshot()["speed_mph"], 1.0)

    def test_compounded_jump_is_shown(self):
        # quick successive incline clicks: 1.5 -> 2.4 -> 3.8 must be visible, not hidden
        self.pad._set(speed_mph=3.8)
        self.pad._hold_mph, self.pad._hold_until = 1.5, time.monotonic() + 5
        self.assertEqual(self.pad.snapshot()["speed_mph"], 3.8)

    def test_over_max_speed_is_never_hidden(self):
        self.pad.max_mph = 2.0
        self.pad._set(speed_mph=2.7)
        self.pad._hold_mph, self.pad._hold_until = 1.7, time.monotonic() + 5
        self.assertEqual(self.pad.snapshot()["speed_mph"], 2.7)


class StaleLink(unittest.TestCase):
    def test_commands_refused_when_pad_went_silent(self):
        pad = PadController("AA:BB", max_mph=4.0)
        pad._client = FakeClient()
        pad._set(connected=True)
        pad._last_data = time.monotonic() - 30

        async def run():
            pad._cp_lock = asyncio.Lock()
            pad._cp_replies = asyncio.Queue()
            await pad._cp(b"\x07", "start")

        with self.assertRaises(PadError) as ctx:
            asyncio.run(run())
        self.assertIn("isn't connected", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

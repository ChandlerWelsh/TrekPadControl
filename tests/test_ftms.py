"""Protocol tests using real packets captured from the pad during live testing."""
import unittest

from trekpad import ftms


class TreadmillData(unittest.TestCase):
    def test_full_record_while_walking(self):
        rec = ftms.parse_treadmill_data(bytes.fromhex("8c0501010c00000a0009000100000000001d00"))
        self.assertEqual(rec["speed_kmh"], 2.57)
        self.assertEqual(rec["distance_m"], 12)
        self.assertEqual(rec["incline_pct"], 1.0)
        self.assertEqual(rec["kcal"], 1)
        self.assertIsNone(rec["heart_rate"])
        self.assertEqual(rec["elapsed_s"], 29)

    def test_full_record_idle(self):
        rec = ftms.parse_treadmill_data(bytes.fromhex("8c050000000000000000000000000000000000"))
        self.assertEqual(rec["speed_kmh"], 0)
        self.assertEqual(rec["elapsed_s"], 0)

    def test_short_record_has_speed_only(self):
        rec = ftms.parse_treadmill_data(bytes.fromhex("1020a10000000000c00800"))
        self.assertEqual(rec["speed_kmh"], 1.61)
        self.assertNotIn("elapsed_s", rec)

    def test_truncated_packet_does_not_crash(self):
        rec = ftms.parse_treadmill_data(bytes.fromhex("8c05a100"))
        self.assertEqual(rec, {"speed_kmh": 1.61})
        self.assertEqual(ftms.parse_treadmill_data(b""), {})


class Commands(unittest.TestCase):
    def test_bytes_match_what_worked_live(self):
        self.assertEqual(ftms.cmd_start().hex(), "07")
        self.assertEqual(ftms.cmd_stop().hex(), "0801")
        self.assertEqual(ftms.cmd_set_speed_mph(1.0).hex(), "026400")
        self.assertEqual(ftms.cmd_set_speed_mph(0.6).hex(), "023c00")
        self.assertEqual(ftms.cmd_set_incline(1).hex(), "030a00")
        self.assertEqual(ftms.cmd_set_incline(0).hex(), "030000")

    def test_replies_and_status(self):
        self.assertEqual(ftms.parse_cp_response(bytes.fromhex("800201")), (2, 1))
        self.assertIsNone(ftms.parse_cp_response(bytes.fromhex("04")))
        self.assertEqual(ftms.parse_machine_status(bytes.fromhex("060a00"))["incline_pct"], 1.0)
        self.assertEqual(ftms.parse_machine_status(bytes.fromhex("05a100"))["speed_kmh"], 1.61)
        self.assertFalse(ftms.parse_machine_status(bytes.fromhex("0201"))["paused"])


if __name__ == "__main__":
    unittest.main()

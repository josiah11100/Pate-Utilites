import os
import unittest

os.environ.setdefault("PATE_DB_PATH", ":memory:")

from pate_utilities import format_elapsed, level_for_boosts, parse_duration


class DurationTests(unittest.TestCase):
    def test_parses_common_and_compound_durations(self):
        self.assertEqual(parse_duration("1m"), 60)
        self.assertEqual(parse_duration("2h"), 7200)
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("1d, 2h"), 93600)
        self.assertEqual(parse_duration("1mo"), 30 * 24 * 60 * 60)

    def test_rejects_empty_zero_and_unknown_durations(self):
        for value in ("", "0m", "soon", "1fortnight"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_duration(value)


class BoostLevelTests(unittest.TestCase):
    def test_level_thresholds(self):
        self.assertEqual(level_for_boosts(1), 0)
        self.assertEqual(level_for_boosts(2), 1)
        self.assertEqual(level_for_boosts(6), 1)
        self.assertEqual(level_for_boosts(7), 2)
        self.assertEqual(level_for_boosts(14), 3)


class ElapsedFormatTests(unittest.TestCase):
    def test_formats_duration_for_afk_return_message(self):
        self.assertEqual(format_elapsed(0), "less than a second")
        self.assertEqual(format_elapsed(62), "1 minute 2 seconds")
        self.assertEqual(format_elapsed(90061), "1 day 1 hour")


if __name__ == "__main__":
    unittest.main()

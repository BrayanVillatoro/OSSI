import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ossi.when import describe, parse_when

# A Wednesday.
NOW = datetime(2026, 10, 28, 10, 0)


def parsed(text, now=NOW):
    result = parse_when(text, now)
    return None if result is None else datetime.fromtimestamp(result)


class ParseWhenTest(unittest.TestCase):
    def assert_means(self, text, *expected, now=NOW):
        self.assertEqual(parsed(text, now), datetime(*expected), text)

    def test_relative_times_add_to_now(self):
        for text, seconds in [
            ("in 20 minutes", 1200),
            ("in 20 mins", 1200),
            ("in 30 seconds", 30),
            ("in a minute", 60),
            ("in an hour", 3600),
            ("in half an hour", 1800),
            ("in 1 hour and 30 minutes", 5400),
            ("in 2 hrs", 7200),
            ("in 3 days", 259200),
            ("in 2 weeks", 1209600),
        ]:
            self.assertEqual(parse_when(text, NOW), NOW.timestamp() + seconds, text)

    def test_clock_times_mean_the_next_occurrence(self):
        self.assert_means("at 3pm", 2026, 10, 28, 15, 0)
        self.assert_means("3:30 pm", 2026, 10, 28, 15, 30)
        self.assert_means("at 15:45", 2026, 10, 28, 15, 45)
        self.assert_means("at 9am", 2026, 10, 29, 9, 0)
        self.assert_means("at 3 p.m.", 2026, 10, 28, 15, 0)
        self.assert_means("at noon", 2026, 10, 28, 12, 0)
        self.assert_means("at midnight", 2026, 10, 29, 0, 0)
        self.assert_means("this afternoon", 2026, 10, 28, 15, 0)
        self.assert_means("at 8 in the evening", 2026, 10, 28, 20, 0)

    def test_times_without_am_or_pm_pick_the_next_sensible_one(self):
        self.assert_means("at 3", 2026, 10, 28, 15, 0)
        self.assert_means("at 11", 2026, 10, 28, 11, 0)
        self.assert_means("at 9", 2026, 10, 28, 21, 0)
        self.assert_means("at 3", 2026, 10, 29, 15, 0, now=datetime(2026, 10, 28, 16, 0))

    def test_midnight_and_noon_hours_with_am_and_pm(self):
        self.assert_means("at 12am", 2026, 10, 29, 0, 0)
        self.assert_means("at 12pm", 2026, 10, 28, 12, 0)

    def test_day_words(self):
        self.assert_means("tomorrow", 2026, 10, 29, 9, 0)
        self.assert_means("tomorrow at 3pm", 2026, 10, 29, 15, 0)
        self.assert_means("at 3pm tomorrow", 2026, 10, 29, 15, 0)
        self.assert_means("tomorrow morning", 2026, 10, 29, 9, 0)
        self.assert_means("tomorrow at 3", 2026, 10, 29, 15, 0)
        self.assert_means("today at 5pm", 2026, 10, 28, 17, 0)
        self.assert_means("tonight", 2026, 10, 28, 20, 0)
        self.assert_means("tonight at 9", 2026, 10, 28, 21, 0)

    def test_today_can_be_in_the_past_so_the_caller_can_refuse_it(self):
        self.assertLess(parse_when("today at 8am", NOW), NOW.timestamp())

    def test_today_alone_needs_a_time(self):
        self.assertIsNone(parse_when("today", NOW))

    def test_weekdays(self):
        self.assert_means("friday", 2026, 10, 30, 9, 0)
        self.assert_means("on friday at 5pm", 2026, 10, 30, 17, 0)
        self.assert_means("next friday", 2026, 10, 30, 9, 0)
        self.assert_means("monday", 2026, 11, 2, 9, 0)
        self.assert_means("wednesday at 5pm", 2026, 10, 28, 17, 0)
        self.assert_means("wednesday at 8am", 2026, 11, 4, 8, 0)
        self.assert_means("next wednesday at 5pm", 2026, 11, 4, 17, 0)

    def test_dates(self):
        self.assert_means("november 3", 2026, 11, 3, 9, 0)
        self.assert_means("nov 3rd at 2pm", 2026, 11, 3, 14, 0)
        self.assert_means("on 11/3 at 2pm", 2026, 11, 3, 14, 0)
        self.assert_means("january 5", 2027, 1, 5, 9, 0)
        self.assert_means("october 1", 2027, 10, 1, 9, 0)
        self.assert_means("jan 5, 2028", 2028, 1, 5, 9, 0)
        self.assert_means("1/5/28", 2028, 1, 5, 9, 0)

    def test_things_that_are_not_times(self):
        for text in [
            "",
            "call the bank",
            "chapter 3",
            "3",
            "the morning",
            "morning",
            "at 25",
            "at 3:75",
            "13pm",
            "0am",
            "february 31",
            "13/45",
            "in 5",
            "in a while",
            "in 99999 weeks",
            "jan 5, 9999",
            "sun",
        ]:
            self.assertIsNone(parse_when(text, NOW), text)


class DescribeTest(unittest.TestCase):
    def describes(self, *moment):
        return describe(datetime(*moment).timestamp(), NOW)

    def test_nearby_days_use_words(self):
        self.assertEqual(self.describes(2026, 10, 28, 15, 0), "today at 3:00 PM")
        self.assertEqual(self.describes(2026, 10, 29, 9, 5), "tomorrow at 9:05 AM")
        self.assertEqual(self.describes(2026, 10, 27, 9, 0), "yesterday at 9:00 AM")
        self.assertEqual(self.describes(2026, 10, 30, 12, 0), "Friday at 12:00 PM")

    def test_later_days_use_dates(self):
        self.assertEqual(self.describes(2026, 11, 20, 9, 0), "Nov 20 at 9:00 AM")
        self.assertEqual(self.describes(2027, 1, 5, 9, 0), "Jan 5, 2027 at 9:00 AM")


if __name__ == "__main__":
    unittest.main()

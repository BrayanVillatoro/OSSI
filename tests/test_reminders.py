import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ossi import router, storage
from ossi.skills.reminders import fire_due, split_request, upcoming


class ReminderTest(unittest.TestCase):
    def setUp(self):
        self.db = storage.connect(":memory:")
        self.addCleanup(self.db.close)

    def say(self, text):
        return router.handle(self.db, text)

    def test_time_can_come_before_or_after_the_message(self):
        for text in [
            "remind me in 20 minutes to check the oven",
            "remind me to check the oven in 20 minutes",
            "remind me about check the oven in 20 minutes",
            "Remind me In 20 Minutes to check the oven",
        ]:
            message, due = split_request(text[len("remind me ") :])
            self.assertEqual(message, "check the oven", text)
            self.assertAlmostEqual(due, time.time() + 1200, delta=5)

    def test_only_the_first_to_after_the_time_splits_the_message(self):
        message, _ = split_request("tomorrow at 3pm to go to the store")
        self.assertEqual(message, "go to the store")
        message, _ = split_request("to go to the store tomorrow")
        self.assertEqual(message, "go to the store")

    def test_numbers_in_the_message_are_not_mistaken_for_times(self):
        message, due = split_request("to read chapter 3")
        self.assertEqual(message, "read chapter 3")
        self.assertIsNone(due)

    def test_adding_confirms_the_time_and_lists_it(self):
        reply = self.say("remind me tomorrow at 3pm to call the bank")
        self.assertEqual(reply, "Reminder 1 set for tomorrow at 3:00 PM: call the bank")
        self.assertEqual(self.say("reminders"), "1. tomorrow at 3:00 PM: call the bank")
        self.assertEqual(self.say("what are my reminders?"), self.say("reminders"))

    def test_upcoming_is_soonest_first(self):
        self.say("remind me in 2 hours to later")
        self.say("remind me in 1 hour to sooner")
        self.assertEqual([row["message"] for row in upcoming(self.db)], ["sooner", "later"])

    def test_missing_pieces_get_a_helpful_reply(self):
        self.assertIn("When should I remind you", self.say("remind me to call the bank"))
        self.assertIn("What should I remind you", self.say("remind me tomorrow"))
        self.assertIn("What should I remind you", self.say("remind me"))
        self.assertEqual(upcoming(self.db), [])

    def test_a_time_in_the_past_is_refused(self):
        self.assertIn("already passed", self.say("remind me jan 1, 2020 to time travel"))
        self.assertEqual(upcoming(self.db), [])

    def test_cancel(self):
        self.say("remind me in 1 hour to call the bank")
        self.assertEqual(self.say("cancel reminder 1"), "Canceled reminder 1: call the bank")
        self.assertEqual(self.say("reminders"), "No upcoming reminders.")
        self.assertEqual(self.say("cancel reminder 1"), "There's no upcoming reminder 1.")

    def test_delete_without_the_word_reminder_does_not_cancel_one(self):
        self.say("remind me in 1 hour to call the bank")
        self.assertEqual(self.say("delete 1"), router.FALLBACK)
        self.assertEqual(len(upcoming(self.db)), 1)

    def test_fire_due_only_returns_due_reminders_once(self):
        self.say("remind me in 1 hour to soon")
        self.say("remind me in 5 hours to later")
        self.assertEqual(fire_due(self.db), [])

        fired = fire_due(self.db, time.time() + 2 * 3600)
        self.assertEqual([row["message"] for row in fired], ["soon"])
        self.assertEqual(fire_due(self.db, time.time() + 2 * 3600), [])
        self.assertEqual([row["message"] for row in upcoming(self.db)], ["later"])

    def test_a_fired_reminder_cannot_be_canceled(self):
        self.say("remind me in 1 hour to soon")
        fire_due(self.db, time.time() + 2 * 3600)
        self.assertEqual(self.say("cancel reminder 1"), "There's no upcoming reminder 1.")


if __name__ == "__main__":
    unittest.main()

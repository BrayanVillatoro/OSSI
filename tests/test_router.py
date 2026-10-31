import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ossi import router, storage


class RouterTest(unittest.TestCase):
    def setUp(self):
        self.db = storage.connect(":memory:")
        self.addCleanup(self.db.close)

    def say(self, text):
        return router.handle(self.db, text)

    def test_task_lifecycle(self):
        self.assertEqual(self.say("add task call the bank"), "Added task 1: call the bank")
        self.assertEqual(self.say("add buy milk to my list"), "Added task 2: buy milk")
        self.assertEqual(self.say("tasks"), "1. call the bank\n2. buy milk")
        self.assertEqual(self.say("done 1"), "Completed task 1.")
        self.assertEqual(self.say("what do I need to do?"), "2. buy milk")

    def test_completing_a_missing_task_says_so(self):
        self.assertEqual(self.say("done 99"), "There's no open task 99.")

    def test_undo_reopens_tasks_most_recent_first(self):
        self.assertEqual(self.say("undo"), "There's nothing to undo.")
        self.say("add task call the bank")
        self.say("add task buy milk")
        self.say("done 1")
        self.say("done 2")
        self.assertEqual(self.say("undo"), "Reopened task 2: buy milk")
        self.assertEqual(self.say("tasks"), "2. buy milk")
        self.assertEqual(self.say("undo"), "Reopened task 1: call the bank")
        self.assertEqual(self.say("undo"), "There's nothing to undo.")

    def test_notes_save_and_search(self):
        self.assertEqual(self.say("note the wifi password is on the router"), "Saved.")
        self.say("remember that the car is due for service")
        self.assertIn("wifi", self.say("find note wifi"))
        self.assertNotIn("car", self.say("find note wifi"))
        self.assertEqual(self.say("find note 100%"), "No notes mention '100%'.")
        self.assertIn("car", self.say("notes"))

    def test_time_and_date(self):
        self.assertIn("M.", self.say("what time is it?"))
        self.assertIn("20", self.say("what's the date"))

    def test_help_lists_commands(self):
        self.assertIn("add task <title>", self.say("help"))

    def test_unknown_input_gets_the_fallback(self):
        self.assertEqual(self.say("fly me to the moon"), router.FALLBACK)


if __name__ == "__main__":
    unittest.main()

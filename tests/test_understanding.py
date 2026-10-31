import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ossi import router, storage
from ossi.skills.reminders import upcoming
from ossi.skills.tasks import open_tasks


class Conversation(unittest.TestCase):
    def setUp(self):
        self.db = storage.connect(":memory:")
        self.addCleanup(self.db.close)
        self.session = router.Session()

    def say(self, text):
        return router.handle(self.db, text, self.session)

    def open_titles(self):
        return [row["title"] for row in open_tasks(self.db)]

    def reminder_messages(self):
        return [row["message"] for row in upcoming(self.db)]


class TypoTest(Conversation):
    def test_typos_in_command_words_are_suggested(self):
        for typed, meant in [
            ("taks", "tasks"),
            ("remindrs", "reminders"),
            ("nots", "notes"),
            ("hlep", "help"),
            ("wat time is it", "what time is it"),
            ("shw my tasks", "show my tasks"),
            ("add taks call the bank", "add task call the bank"),
            ("remnd me tomorrow at 3pm to call", "remind me tomorrow at 3pm to call"),
            ("dont strat at login", "don't start at login"),
        ]:
            self.assertEqual(self.say(typed), f"Did you mean: {meant}?", typed)
            self.session.pending = None

    def test_yes_runs_the_correction_and_no_drops_it(self):
        self.say("add taks call the bank")
        self.assertEqual(self.say("yes"), "Added task 1: call the bank")

        self.say("add taks buy milk")
        self.assertEqual(self.say("no"), "OK, never mind.")
        self.assertEqual(self.open_titles(), ["call the bank"])

    def test_a_correction_never_runs_without_asking(self):
        self.say("add taks call the bank")
        self.assertEqual(self.open_titles(), [])

    def test_a_different_message_replaces_the_question(self):
        self.say("add taks call the bank")
        self.assertIn("No open tasks", self.say("tasks"))
        self.assertEqual(self.say("yes"), router.FALLBACK)
        self.assertEqual(self.open_titles(), [])

    def test_the_users_own_spelling_in_a_title_is_kept(self):
        self.assertEqual(
            self.say("add taks call teh bnak"), "Did you mean: add task call teh bnak?"
        )

    def test_misspelled_time_words_are_suggested(self):
        reply = self.say("remind me tomorow at 3pm to call the bank")
        self.assertEqual(reply, "Did you mean: remind me tomorrow at 3pm to call the bank?")
        self.assertIn("Reminder 1 set for tomorrow at 3:00 PM", self.say("yes"))

    def test_close_phrases_are_suggested(self):
        self.assertEqual(self.say("what time"), "Did you mean: what time is it?")

    def test_unrelated_text_is_not_forced_into_a_command(self):
        for text in [
            "fly me to the moon",
            "sing me a song",
            "not sure",
            "yes",
            "no",
            "the weather is nice",
            "asdfghjkl",
        ]:
            self.assertEqual(self.say(text), router.FALLBACK, text)


class FillerTest(Conversation):
    def test_polite_words_are_ignored(self):
        for text in [
            "please show my tasks",
            "can you show my tasks",
            "could you please show my tasks?",
            "hey ossi, show my tasks",
            "OSSI show my tasks please",
            "show my tasks, thanks",
            "ok ossi can you show my tasks",
        ]:
            self.assertEqual(self.say(text), "No open tasks.", text)

    def test_a_title_keeps_its_polite_words_when_the_command_already_matches(self):
        self.assertEqual(self.say("add task say please"), "Added task 1: say please")

    def test_curly_apostrophes_work(self):
        self.assertIn("It's", self.say("what’s the date"))

    def test_missing_apostrophe_works(self):
        self.assertIn("It's", self.say("whats the date"))

    def test_small_talk(self):
        self.assertIn("Hi", self.say("hello"))
        self.assertIn("welcome", self.say("thanks!"))
        self.assertIn("OSSI", self.say("who are you?"))

    def test_small_talk_stays_out_of_help(self):
        self.assertNotIn("None", self.say("help"))


class TaskReferenceTest(Conversation):
    def setUp(self):
        super().setUp()
        self.say("add task call the bank")
        self.say("add task buy milk")
        self.say("add task buy eggs")

    def test_it_means_the_last_task_mentioned(self):
        self.say("add task walk the dog")
        for text in ["finish it"]:
            self.assertEqual(self.say(text), "Completed task 4: walk the dog")
        self.assertEqual(self.say("undo that"), "Reopened task 4: walk the dog")
        self.assertEqual(self.say("that's done"), "Completed task 4: walk the dog")

    def test_other_ways_to_say_it(self):
        for text in ["done", "mark it done", "mark that as complete", "it's done"]:
            self.assertIn("buy eggs", self.say(text), text)
            self.say("undo")

    def test_name_or_part_of_a_name(self):
        self.assertEqual(self.say("finish call the bank"), "Completed task 1: call the bank")
        self.assertEqual(self.say("done with milk"), "Completed task 2: buy milk")
        self.assertEqual(self.say("i finished eggs"), "Completed task 3: buy eggs")

    def test_a_misspelled_name_still_finds_the_task(self):
        self.assertEqual(self.say("finish call the bnak"), "Completed task 1: call the bank")

    def test_a_name_with_several_matches_asks_which_one(self):
        reply = self.say("finish buy")
        self.assertEqual(reply, "Which one?\n2. buy milk\n3. buy eggs")
        self.assertEqual(self.open_titles(), ["call the bank", "buy milk", "buy eggs"])
        self.assertEqual(self.say("the second one"), "Completed task 3: buy eggs")

    def test_answering_which_one_by_number_or_name(self):
        self.say("finish buy")
        self.assertEqual(self.say("2"), "Completed task 2: buy milk")
        self.say("undo")
        self.say("finish buy")
        self.assertEqual(self.say("eggs"), "Completed task 3: buy eggs")

    def test_position_follows_the_last_list_shown(self):
        self.say("tasks")
        self.assertEqual(self.say("finish the first one"), "Completed task 1: call the bank")
        self.say("tasks")
        self.assertEqual(self.say("done the last one"), "Completed task 3: buy eggs")

    def test_nothing_in_mind_with_several_tasks_asks(self):
        fresh = router.Session()
        reply = router.handle(self.db, "finish it", fresh)
        self.assertIn("Which one?", reply)
        self.assertEqual(len(self.open_titles()), 3)

    def test_nothing_in_mind_with_one_task_finishes_it(self):
        self.say("done 1")
        self.say("done 2")
        fresh = router.Session()
        self.assertEqual(router.handle(self.db, "finish it", fresh), "Completed task 3: buy eggs")

    def test_it_ignores_a_task_that_is_already_finished(self):
        self.say("done 3")
        self.assertIn("Which one?", self.say("finish it"))

    def test_clear_failures(self):
        self.assertEqual(self.say("done 99"), "There's no open task 99.")
        self.assertEqual(
            self.say("finish xylophone lessons"),
            "I can't find an open task matching 'xylophone lessons'.",
        )

    def test_no_tasks_at_all(self):
        for number in (1, 2, 3):
            self.say(f"done {number}")
        self.assertEqual(self.say("finish it"), "No open tasks.")


class MorePhrasingTest(Conversation):
    def test_ways_to_ask_for_lists(self):
        for text in ["show me my tasks", "what's on my list", "whats on my plate"]:
            self.assertEqual(self.say(text), "No open tasks.", text)
        self.assertEqual(self.say("show me my reminders"), "No upcoming reminders.")
        self.assertEqual(self.say("show me my notes"), "No notes yet.")

    def test_saying_something_is_done(self):
        self.say("add task buy milk")
        self.say("add task call the bank")
        self.say("add task walk the dog")
        self.assertEqual(self.say("milk is done"), "Completed task 1: buy milk")
        self.assertEqual(self.say("task 2 is done"), "Completed task 2: call the bank")
        self.say("undo")
        self.assertEqual(self.say("it is done"), "Completed task 2: call the bank")

    def test_is_done_does_not_swallow_adding_or_noting(self):
        self.assertEqual(
            self.say("add task check the oven is done"), "Added task 1: check the oven is done"
        )
        self.assertEqual(self.say("note the laundry is done"), "Saved.")
        self.assertEqual(self.open_titles(), ["check the oven is done"])

    def test_set_a_reminder(self):
        for text in [
            "set a reminder for tomorrow at 3pm to call the bank",
            "set a reminder to call the bank tomorrow at 3pm",
            "create a reminder for tomorrow at 3pm to call the bank",
        ]:
            self.assertIn("set for tomorrow at 3:00 PM: call the bank", self.say(text), text)

    def test_set_a_reminder_with_nothing_else_asks(self):
        self.assertEqual(self.say("set a reminder"), "What should I remind you about?")

    def test_leading_words_are_ignored(self):
        self.say("remind me in 1 hour to call the bank")
        self.assertEqual(self.say("actually cancel it"), "Canceled reminder 1: call the bank")
        self.assertEqual(self.say("oh and just show my tasks"), "No open tasks.")


class ReminderConversationTest(Conversation):
    def test_bare_cancel_asks_even_with_a_recent_reminder(self):
        self.say("remind me in 1 hour to call the bank")
        self.assertEqual(self.say("cancel"), "Which one?\n1. call the bank")
        self.assertEqual(self.reminder_messages(), ["call the bank"])
        self.assertEqual(self.say("never mind"), "OK, never mind.")
        self.assertEqual(self.reminder_messages(), ["call the bank"])

    def test_missing_time_is_asked_for(self):
        self.assertEqual(
            self.say("remind me to call the bank"), "When should I remind you to call the bank?"
        )
        self.assertEqual(self.reminder_messages(), [])
        self.assertIn("set for tomorrow at 3:00 PM: call the bank", self.say("tomorrow at 3pm"))

    def test_missing_message_is_asked_for(self):
        self.assertEqual(self.say("remind me tomorrow"), "What should I remind you about?")
        self.assertIn("set for tomorrow at 9:00 AM: call the bank", self.say("call the bank"))

    def test_nothing_given_asks_for_both(self):
        self.say("remind me")
        self.assertEqual(self.say("call the bank"), "When should I remind you to call the bank?")
        self.assertIn("call the bank", self.say("in 2 hours"))

    def test_a_typo_in_the_answer_is_understood(self):
        self.say("remind me to call the bank")
        self.assertIn("set for tomorrow", self.say("tomorow at 3pm"))

    def test_an_unclear_time_asks_again_and_keeps_waiting(self):
        self.say("remind me to call the bank")
        self.assertIn("didn't catch", self.say("whenever"))
        self.assertIn("call the bank", self.say("in 2 hours"))

    def test_never_mind_drops_the_question(self):
        for word in ["never mind", "nevermind", "forget it", "cancel"]:
            self.say("remind me to call the bank")
            self.assertEqual(self.say(word), "OK, never mind.", word)
        self.assertEqual(self.reminder_messages(), [])

    def test_a_real_command_interrupts_the_question(self):
        self.say("remind me to call the bank")
        self.assertEqual(self.say("tasks"), "No open tasks.")
        self.assertEqual(self.say("in 2 hours"), router.FALLBACK)
        self.assertEqual(self.reminder_messages(), [])

    def test_time_already_passed_while_answering(self):
        self.say("remind me to call the bank")
        self.assertIn("already passed", self.say("jan 1, 2020"))

    def test_cancel_it_means_the_reminder_just_set(self):
        self.say("remind me in 1 hour to call the bank")
        self.say("remind me in 2 hours to check the oven")
        self.assertEqual(self.say("cancel it"), "Canceled reminder 2: check the oven")
        self.assertEqual(self.reminder_messages(), ["call the bank"])

    def test_cancel_by_name_position_or_number(self):
        self.say("remind me in 1 hour to call the bank")
        self.say("remind me in 2 hours to check the oven")
        self.say("remind me in 3 hours to walk the dog")
        self.assertEqual(self.say("cancel the oven reminder"), "Canceled reminder 2: check the oven")
        self.say("reminders")
        self.assertEqual(self.say("cancel the last one"), "Canceled reminder 3: walk the dog")
        self.assertEqual(self.say("delete reminder 1"), "Canceled reminder 1: call the bank")

    def test_cancel_with_nothing_in_mind_always_asks_because_it_cannot_be_undone(self):
        self.say("remind me in 1 hour to call the bank")
        fresh = router.Session()
        self.assertEqual(
            router.handle(self.db, "cancel it", fresh), "Which one?\n1. call the bank"
        )
        self.assertEqual(self.reminder_messages(), ["call the bank"])
        self.assertEqual(
            router.handle(self.db, "1", fresh), "Canceled reminder 1: call the bank"
        )

    def test_it_means_different_things_for_tasks_and_reminders(self):
        self.say("add task buy milk")
        self.say("remind me in 1 hour to call the bank")
        self.assertEqual(self.say("finish it"), "Completed task 1: buy milk")
        self.assertEqual(self.say("cancel it"), "Canceled reminder 1: call the bank")

    def test_delete_with_only_a_number_still_means_nothing(self):
        self.say("remind me in 1 hour to call the bank")
        self.assertEqual(self.say("delete 1"), router.FALLBACK)


class WithoutASessionTest(unittest.TestCase):
    """The one-shot terminal command has no conversation to continue."""

    def test_handle_works_without_a_session(self):
        db = storage.connect(":memory:")
        self.addCleanup(db.close)
        self.assertEqual(router.handle(db, "add task buy milk"), "Added task 1: buy milk")
        self.assertEqual(router.handle(db, "finish it"), "Completed task 1: buy milk")


if __name__ == "__main__":
    unittest.main()

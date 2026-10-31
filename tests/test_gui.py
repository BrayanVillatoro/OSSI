import sys
import time
import tkinter as tk
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ossi import gui, storage
from ossi.drawing import blend, rounded_image


class FakeProcess:
    def __init__(self, code=0):
        self.code = code

    def poll(self):
        return self.code


class GuiTest(unittest.TestCase):
    def setUp(self):
        self.db = storage.connect(":memory:")
        self.addCleanup(self.db.close)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"no display: {error}")
        self.addCleanup(self.close_window)
        self.root.withdraw()
        self.sent = []
        self.toast_result = FakeProcess()
        self.app = gui.App(self.root, self.db, notify=self.fake_notify)

    def fake_notify(self, title, body):
        self.sent.append((title, body))
        return self.toast_result

    def add_reminder(self, message, due_at):
        with self.db:
            self.db.execute(
                "INSERT INTO reminders (message, due_at) VALUES (?, ?)", (message, due_at)
            )

    def close_window(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def type_and_send(self, text):
        self.app.entry.insert(0, text)
        self.app.send()

    def transcript(self):
        return self.app.log.get("1.0", "end")

    def bubbles(self):
        return [
            bubble.itemcget("message", "text") for bubble in self.app.log.winfo_children()
        ]

    def sidebar(self):
        return self.app.agenda.get("1.0", "end")

    def test_send_shows_the_reply_and_clears_the_box(self):
        self.type_and_send("note buy stamps")
        self.assertEqual(self.bubbles(), ["note buy stamps"])
        self.assertIn("Saved.", self.transcript())
        self.assertEqual(self.app.entry.get(), "")

    def test_blank_input_does_nothing(self):
        before = self.transcript()
        self.type_and_send("   ")
        self.assertEqual(self.transcript(), before)
        self.assertEqual(self.bubbles(), [])

    def test_new_task_appears_and_completing_removes_it(self):
        self.type_and_send("add task call the bank")
        self.type_and_send("add task buy milk")
        self.assertIn("call the bank", self.sidebar())
        self.assertIn("buy milk", self.sidebar())
        self.assertEqual(self.app.task_ids, [1, 2])

        self.app.complete(2)

        self.assertIn("call the bank", self.sidebar())
        self.assertNotIn("buy milk", self.sidebar())
        self.assertIn("Completed task 2: buy milk", self.transcript())

    def test_the_window_remembers_the_conversation(self):
        self.type_and_send("add task buy milk")
        self.type_and_send("finish it")
        self.assertIn("Completed task 1: buy milk", self.transcript())

        self.type_and_send("remind me to call the bank")
        self.type_and_send("in 2 hours")
        self.assertIn("call the bank", self.sidebar())

    def test_empty_task_list_says_so(self):
        self.assertIn("All clear", self.sidebar())
        self.type_and_send("add task call the bank")
        self.assertNotIn("All clear", self.sidebar())

    def test_upcoming_reminders_appear_in_the_sidebar_and_leave_when_canceled(self):
        self.assertNotIn("REMINDERS", self.sidebar())
        self.type_and_send("remind me tomorrow at 3pm to call the bank")
        self.assertIn("REMINDERS", self.sidebar())
        self.assertIn("call the bank", self.sidebar())
        self.assertIn("Tomorrow at 3:00 PM", self.sidebar())

        self.type_and_send("cancel reminder 1")
        self.assertNotIn("REMINDERS", self.sidebar())

    def test_due_reminder_notifies_and_shows_in_chat_once(self):
        self.add_reminder("call the bank", time.time() - 1)

        self.app.check_reminders()
        self.app.check_reminders()

        self.assertEqual(self.sent, [("Reminder", "call the bank")])
        self.assertEqual(self.transcript().count("Reminder: call the bank"), 1)
        self.assertNotIn("call the bank", self.sidebar())

    def test_future_reminder_does_nothing_yet(self):
        self.add_reminder("call the bank", time.time() + 3600)
        self.app.check_reminders()
        self.assertEqual(self.sent, [])

    def test_reminder_from_while_ossi_was_off_is_labeled_missed(self):
        self.add_reminder("call the bank", time.time() - 7200)
        self.app.check_reminders()
        self.assertEqual(self.sent, [("Missed reminder", "call the bank")])
        self.assertIn("Missed reminder from", self.transcript())

    def test_several_due_reminders_share_one_notification(self):
        self.add_reminder("first", time.time() - 2)
        self.add_reminder("second", time.time() - 1)
        self.app.check_reminders()
        self.assertEqual(self.sent, [("2 reminders", "first; second")])
        self.assertIn("first", self.transcript())
        self.assertIn("second", self.transcript())

    def test_window_opens_when_a_notification_cannot_start(self):
        self.toast_result = None
        self.add_reminder("call the bank", time.time() - 1)
        self.app.check_reminders()
        self.root.update()
        self.assertEqual(self.root.state(), "normal")

    def test_window_opens_when_a_notification_fails_afterward(self):
        self.toast_result = FakeProcess(code=1)
        self.add_reminder("call the bank", time.time() - 1)
        self.app.check_reminders()
        self.assertEqual(self.root.state(), "withdrawn")
        self.app.check_toasts()
        self.root.update()
        self.assertEqual(self.root.state(), "normal")

    def test_window_stays_hidden_when_a_notification_works(self):
        self.add_reminder("call the bank", time.time() - 1)
        self.app.check_reminders()
        self.app.check_toasts()
        self.assertEqual(self.root.state(), "withdrawn")
        self.assertEqual(self.app.toasts, [])

    def test_hiding_explains_itself_only_the_first_time(self):
        self.app.hide()
        self.app.hide()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("still running", self.sent[0][0])
        self.assertEqual(self.root.state(), "withdrawn")

    def test_show_brings_the_window_back(self):
        self.app.hide()
        self.app.show()
        self.root.update()
        self.assertEqual(self.root.state(), "normal")

    def test_sidebar_refreshes_when_the_day_changes(self):
        self.app.shown_date = None
        self.app.listener = mock.Mock(poll=lambda: False)
        with mock.patch.object(self.root, "after"):
            self.app.tick()
        self.assertEqual(self.app.shown_date, datetime.now().date())

    def test_tick_keeps_running_after_an_error(self):
        self.app.listener = mock.Mock()
        self.app.listener.poll.side_effect = ValueError
        scheduled = []
        with mock.patch.object(self.root, "after", lambda ms, fn: scheduled.append(fn)):
            with self.assertRaises(ValueError):
                self.app.tick()
        self.assertEqual(scheduled, [self.app.tick])

    def test_undo_link_appears_after_completing_and_restores_the_task(self):
        self.assertFalse(self.app.undo_label.grid_info())
        self.type_and_send("add task buy milk")

        self.app.complete(1)
        self.assertTrue(self.app.undo_label.grid_info())
        self.assertIn("buy milk", self.app.undo_label.cget("text"))

        self.app.run("undo")
        self.assertIn("buy milk", self.sidebar())
        self.assertFalse(self.app.undo_label.grid_info())

    def test_undo_link_shortens_long_titles(self):
        self.type_and_send("add task " + "x" * 80)
        self.app.complete(1)
        self.assertLess(len(self.app.undo_label.cget("text")), 45)

    def test_copy_puts_text_on_the_clipboard(self):
        self.app.copy("hello")
        self.assertEqual(self.root.clipboard_get(), "hello")

    def test_bubble_text_is_the_selection_or_else_everything(self):
        self.type_and_send("note buy stamps")
        bubble = self.app.log.winfo_children()[0]
        item = bubble.find_withtag("message")[0]
        self.assertEqual(gui.bubble_text(bubble, item), "note buy stamps")
        bubble.select_from(item, 5)
        bubble.select_to(item, 7)
        self.assertEqual(gui.bubble_text(bubble, item), "buy")

    def test_clear_selections_clears_the_chat_and_every_bubble(self):
        self.type_and_send("note buy stamps")
        bubble = self.app.log.winfo_children()[0]
        bubble.select_from("message", 0)
        bubble.select_to("message", 3)
        self.app.log.tag_add("sel", "1.0", "1.4")

        self.app.clear_selections()

        self.assertFalse(bubble.select_item())
        self.assertFalse(self.app.log.tag_ranges("sel"))

    def test_reply_text_is_the_selection_when_there_is_one(self):
        self.app.say("first reply")
        self.app.log.tag_add("sel", "end-12c", "end-7c")
        self.assertTrue(self.app.reply_text(0, 0))
        self.assertIn(self.app.reply_text(0, 0), "first reply")

    def test_rounded_image_is_smooth_at_the_corners(self):
        image = rounded_image(40, 30, 10, "#FFFFFF", "#000000")
        red = lambda x, y: image.get(x, y)[0]
        self.assertEqual(red(0, 0), 0)
        self.assertEqual(red(20, 15), 255)
        partly_covered = {red(x, y) for x in range(10) for y in range(10)} - {0, 255}
        self.assertTrue(partly_covered)
        self.assertEqual(red(39, 29), red(0, 0))


class BlendTest(unittest.TestCase):
    def test_blend(self):
        self.assertEqual(blend("#000000", "#FFFFFF", 0), "#000000")
        self.assertEqual(blend("#000000", "#FFFFFF", 1), "#FFFFFF")
        self.assertEqual(blend("#000000", "#FF0000", 0.5), "#800000")


class IconTest(unittest.TestCase):
    def test_icon_file_exists(self):
        self.assertTrue(gui.ICON.is_file())


if __name__ == "__main__":
    unittest.main()

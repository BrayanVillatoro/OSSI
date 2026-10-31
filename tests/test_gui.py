import sys
import tkinter as tk
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ossi import gui, storage
from ossi.drawing import blend, rounded_image


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
        self.app = gui.App(self.root, self.db)

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
        return self.app.tasks.get("1.0", "end")

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
        self.assertIn("Completed task 2.", self.transcript())

    def test_empty_task_list_says_so(self):
        self.assertIn("All clear", self.sidebar())
        self.type_and_send("add task call the bank")
        self.assertNotIn("All clear", self.sidebar())

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

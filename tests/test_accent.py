import gc
import sys
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ossi import glow, gui, storage, theme


def brightness(color):
    """How bright a color looks to the eye, from 0 to 1, by the standard contrast formula."""
    channels = []
    for start in (1, 3, 5):
        value = int(color[start : start + 2], 16) / 255
        channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(first, second):
    high, low = sorted((brightness(first), brightness(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class ThemeTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(theme.use_accent, theme.DEFAULT_ACCENT)

    def test_every_choice_can_be_read(self):
        for name in theme.ACCENTS:
            theme.use_accent(name)
            for background in (theme.BG, theme.SIDEBAR):
                self.assertGreaterEqual(contrast(theme.ACCENT, background), 4.5, name)
            self.assertGreaterEqual(contrast(theme.ON_ACCENT, theme.ACCENT), 4.5, name)

    def test_an_unknown_color_falls_back(self):
        theme.use_accent("Plaid")
        self.assertEqual(theme.ACCENT, theme.ACCENTS[theme.DEFAULT_ACCENT])
        theme.use_accent(None)
        self.assertEqual(theme.ACCENT, theme.ACCENTS[theme.DEFAULT_ACCENT])

    def test_the_default_looks_the_way_it_always_has(self):
        theme.use_accent(theme.DEFAULT_ACCENT)
        self.assertEqual(theme.ACCENT, "#4CC9A4")

    def test_the_glow_uses_it(self):
        theme.use_accent("Pink")
        self.assertEqual(glow.colors()[glow.LISTENING], theme.ACCENTS["Pink"])


class WindowTest(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.addCleanup(gc.collect)
        self.addCleanup(theme.use_accent, theme.DEFAULT_ACCENT)
        self.db = storage.connect(":memory:")
        self.addCleanup(self.db.close)
        self.window()

    def window(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"no display: {error}")
        self.addCleanup(self.close)
        self.root.withdraw()
        self.app = gui.App(self.root, self.db, notify=lambda title, body: None)
        self.panel = self.app.settings
        self.panel.refresh_appearance()  # Normally done when Settings opens.

    def close(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def everything_in(self, widget):
        yield widget
        for child in widget.winfo_children():
            yield from self.everything_in(child)

    def colors_in_use(self):
        found = set()
        for widget in self.everything_in(self.root):
            for option in ("fg", "bg", "highlightcolor", "highlightbackground"):
                try:
                    found.add(str(widget.cget(option)))
                except tk.TclError:
                    pass
        return found

    def test_mint_until_changed(self):
        self.assertEqual(self.panel.accent_name.cget("text"), "Mint")
        self.assertEqual(self.app.undo_label.cget("fg"), theme.ACCENTS["Mint"])

    def test_choosing_a_color_repaints_the_whole_window(self):
        old = (theme.ACCENT, theme.ACCENT_HOVER, theme.ON_ACCENT)
        self.assertIn(old[0], self.colors_in_use())
        self.app.set_accent("Blue")
        self.assertTrue(self.colors_in_use().isdisjoint(old))
        self.assertEqual(self.app.undo_label.cget("fg"), theme.ACCENTS["Blue"])
        self.assertEqual(self.app.talk_label.cget("fg"), theme.ACCENTS["Blue"])
        self.assertEqual(self.app.send_button.cget("fg"), theme.ON_ACCENT)

    def test_chat_names_and_reminder_times_change_too(self):
        self.app.set_accent("Pink")
        self.assertEqual(str(self.app.log.tag_cget("name", "foreground")), theme.ACCENTS["Pink"])
        self.assertEqual(str(self.app.agenda.tag_cget("due", "foreground")), theme.ACCENTS["Pink"])

    def test_pictures_are_redrawn(self):
        send = str(self.app.send_button.cget("image"))
        save = str(self.panel.save_button.cget("image"))
        switch_images = dict(self.panel.switches[0].images)
        self.app.set_accent("Violet")
        self.assertNotEqual(str(self.app.send_button.cget("image")), send)
        self.assertNotEqual(str(self.panel.save_button.cget("image")), save)
        self.assertIsNot(self.panel.switches[0].images[True], switch_images[True])

    def test_switches_stay_on_or_off(self):
        on = self.panel.switches[0]
        on.set(True)
        self.app.set_accent("Red")
        self.assertTrue(on.on)
        self.assertFalse(self.panel.switches[1].on)

    def test_hover_uses_the_new_color(self):
        self.app.run("add task laundry")
        self.app.run("done 1")  # Undo only shows once there's something to undo.
        self.root.deiconify()
        self.root.update()
        self.app.set_accent("Orange")
        self.app.undo_label.event_generate("<Enter>")
        self.root.update()
        self.assertEqual(self.app.undo_label.cget("fg"), theme.ACCENT_HOVER)
        self.app.undo_label.event_generate("<Leave>")
        self.root.update()
        self.assertEqual(self.app.undo_label.cget("fg"), theme.ACCENTS["Orange"])

    def test_clicking_a_color_chooses_it(self):
        self.root.deiconify()
        self.app.toggle_settings()
        self.panel.canvas.yview_moveto(0.5)
        self.root.update()
        self.panel.accent_buttons["Yellow"].event_generate("<Button-1>")
        self.root.update()
        self.assertEqual(theme.ACCENT, theme.ACCENTS["Yellow"])
        self.assertEqual(self.panel.accent_name.cget("text"), "Yellow")
        self.assertEqual(storage.get_setting(self.db, "accent_color"), "Yellow")

    def test_the_chosen_color_is_marked(self):
        self.app.set_accent("Blue")
        chosen = str(self.panel.accent_buttons["Blue"].cget("image"))
        self.assertEqual(chosen, str(self.panel.accent_image("Blue", True)))
        self.assertEqual(
            str(self.panel.accent_buttons["Mint"].cget("image")),
            str(self.panel.accent_image("Mint", False)),
        )

    def test_the_color_is_remembered_next_time(self):
        self.app.set_accent("Violet")
        self.root.destroy()
        theme.use_accent(theme.DEFAULT_ACCENT)
        self.window()
        self.assertEqual(theme.ACCENT, theme.ACCENTS["Violet"])
        self.assertEqual(self.app.undo_label.cget("fg"), theme.ACCENTS["Violet"])
        self.assertEqual(self.panel.accent_name.cget("text"), "Violet")

    def test_a_saved_color_that_no_longer_exists_is_harmless(self):
        storage.set_setting(self.db, "accent_color", "Plaid")
        self.root.destroy()
        self.window()
        self.assertEqual(self.panel.accent_name.cget("text"), theme.DEFAULT_ACCENT)
        self.assertEqual(theme.ACCENT, theme.ACCENTS[theme.DEFAULT_ACCENT])

    def test_choosing_the_same_color_twice_is_fine(self):
        self.app.set_accent("Blue")
        self.app.set_accent("Blue")
        self.assertEqual(self.app.undo_label.cget("fg"), theme.ACCENTS["Blue"])

    def test_talk_button_keeps_its_state(self):
        self.app.talk.active = True
        self.app.set_talk_button(True)
        self.app.set_accent("Red")
        self.assertEqual(self.app.talk_button.cget("text"), "End")
        self.assertEqual(self.app.talk_button.cget("fg"), theme.ON_ACCENT)
        self.app.talk.active = False

    def test_a_focused_message_box_keeps_its_highlight(self):
        with mock.patch.object(self.app, "draw_box") as draw:
            self.app.box_border = theme.ACCENT
            self.app.set_accent("Blue")
        draw.assert_called_once_with(theme.ACCENTS["Blue"])


if __name__ == "__main__":
    unittest.main()

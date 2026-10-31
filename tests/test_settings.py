import gc
import json
import sys
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ossi import autostart, gui, ollama, router, storage, weather
from ossi.settings_panel import PULL_COMMAND
from ossi.skills import briefing, startup

BOSTON = {"name": "Boston, Massachusetts", "latitude": 1, "longitude": 2, "unit": "fahrenheit"}
PARIS = {"name": "Paris, France", "latitude": 3, "longitude": 4, "unit": "celsius"}


class PanelTest(unittest.TestCase):
    def setUp(self):
        # See test_briefing: old windows must be freed here, not on a worker thread.
        gc.collect()
        self.addCleanup(gc.collect)
        self.db = storage.connect(":memory:")
        self.addCleanup(self.db.close)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"no display: {error}")
        self.addCleanup(self.root.destroy)
        self.root.withdraw()

        # Replaced so that no test here can change the real login settings.
        self.login = {"on": False}
        for name, replacement in (
            ("supported", lambda: True),
            ("is_enabled", lambda: self.login["on"]),
            ("enable", lambda: self.login.update(on=True)),
            ("disable", lambda: self.login.update(on=False)),
        ):
            patcher = mock.patch.object(autostart, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.app = gui.App(self.root, self.db, notify=lambda title, body: None)
        self.panel = self.app.settings
        self.app.toggle_settings()

    def wait(self):
        deadline = time.time() + 5
        while self.app.fetched.empty() and time.time() < deadline:
            time.sleep(0.005)
        self.app.check_fetches()
        self.root.update_idletasks()

    def with_models(self, *names):
        return mock.patch.object(ollama, "list_models", return_value=list(names))

    def without_ollama(self, installed=False):
        down = mock.patch.object(ollama, "list_models", side_effect=OSError("refused"))
        found = mock.patch.object(ollama, "is_installed", return_value=installed)
        down.start()
        found.start()
        self.addCleanup(down.stop)
        self.addCleanup(found.stop)

    def status(self):
        return self.panel.ai_status.cget("text")

    def link_texts(self, frame):
        return [child.cget("text") for child in frame.winfo_children()]

    def click_link(self, frame, text):
        for child in frame.winfo_children():
            if child.cget("text") == text:
                child.event_generate("<Button-1>")
                return
        self.fail(f"no link called {text}")

    def setting(self, key):
        return storage.get_setting(self.db, key)


class OpeningTest(PanelTest):
    def test_swaps_with_the_chat(self):
        self.assertTrue(self.panel.grid_info())
        self.assertFalse(self.app.chat.grid_info())
        self.assertEqual(self.app.settings_label.cget("text"), gui.BACK_LINK)
        self.app.toggle_settings()
        self.assertFalse(self.panel.grid_info())
        self.assertTrue(self.app.chat.grid_info())
        self.assertEqual(self.app.settings_label.cget("text"), gui.SETTINGS_LINK)

    def test_opening_with_the_ai_off_does_not_contact_ollama(self):
        with mock.patch.object(ollama, "OPENER") as opener:
            self.app.toggle_settings()
            self.app.toggle_settings()
            time.sleep(0.05)
        opener.open.assert_not_called()
        self.assertIn("Off", self.status())

    def test_the_briefing_waits_for_the_chat(self):
        with mock.patch.object(self.app, "is_in_use", return_value=True):
            self.app.check_briefing()
            self.assertNotIn("Good ", self.app.log.get("1.0", "end"))
            self.app.toggle_settings()
            self.app.check_briefing()
        self.assertIn("Good ", self.app.log.get("1.0", "end"))


class AiSwitchTest(PanelTest):
    def test_turning_on_with_one_model(self):
        with self.with_models("llama3.2:latest"):
            self.panel.ai_switch.flip()
            self.wait()
        self.assertEqual(self.setting("ai_model"), "llama3.2:latest")
        self.assertTrue(self.panel.ai_switch.on)
        self.assertIn("using llama3.2:latest", self.status())
        self.assertFalse(self.panel.model_row.grid_info())

    def test_several_models_show_a_dropdown_that_works(self):
        with self.with_models("llama3.2:latest", "qwen:latest"):
            self.panel.ai_switch.flip()
            self.wait()
        self.assertTrue(self.panel.model_row.grid_info())
        self.assertEqual(self.panel.model_box.get(), "llama3.2:latest")
        self.panel.model_box.set("qwen:latest")
        self.panel.choose_model()
        self.assertEqual(self.setting("ai_model"), "qwen:latest")
        self.assertIn("using qwen:latest", self.status())

    def test_not_installed_snaps_back_and_says_what_to_do(self):
        self.without_ollama(installed=False)
        self.panel.ai_switch.flip()
        self.wait()
        self.assertFalse(self.panel.ai_switch.on)
        self.assertIsNone(self.setting("ai_model"))
        self.assertIn("isn't installed", self.status())
        self.assertIn(PULL_COMMAND, self.status())
        self.assertIn("Command Prompt", self.status())
        self.assertEqual(
            self.link_texts(self.panel.ai_links), ["Download Ollama", "Copy command", "Try again"]
        )

    def test_installed_but_not_running(self):
        self.without_ollama(installed=True)
        self.panel.ai_switch.flip()
        self.wait()
        self.assertFalse(self.panel.ai_switch.on)
        self.assertIn("isn't running", self.status())
        self.assertEqual(self.link_texts(self.panel.ai_links), ["Try again"])

    def test_no_models_yet(self):
        with self.with_models():
            self.panel.ai_switch.flip()
            self.wait()
        self.assertFalse(self.panel.ai_switch.on)
        self.assertIn(PULL_COMMAND, self.status())

    def test_copy_command_and_download_links(self):
        self.without_ollama()
        self.panel.ai_switch.flip()
        self.wait()
        self.panel.copy_pull()
        self.assertEqual(self.root.clipboard_get(), PULL_COMMAND)
        self.assertIn("Command Prompt", self.status())
        with mock.patch("webbrowser.open") as browser:
            self.root.update()
            self.click_link(self.panel.ai_links, "Download Ollama")
            self.root.update()
        browser.assert_called_once_with("https://ollama.com/")

    def test_try_again_succeeds_once_ollama_is_ready(self):
        self.without_ollama()
        self.panel.ai_switch.flip()
        self.wait()
        mock.patch.stopall()
        with self.with_models("llama3.2:latest"):
            self.panel.try_again()
            self.wait()
        self.assertTrue(self.panel.ai_switch.on)
        self.assertEqual(self.setting("ai_model"), "llama3.2:latest")

    def test_already_on_but_not_running_stays_on(self):
        storage.set_setting(self.db, "ai_model", "llama3.2:latest")
        self.without_ollama()
        self.panel.refresh()
        self.wait()
        self.assertTrue(self.panel.ai_switch.on)
        self.assertEqual(self.setting("ai_model"), "llama3.2:latest")
        self.assertIn("isn't running", self.status())
        self.assertIn("still works", self.status())

    def test_saved_model_that_is_gone_offers_a_choice(self):
        storage.set_setting(self.db, "ai_model", "gone:latest")
        with self.with_models("llama3.2:latest"):
            self.panel.refresh()
            self.wait()
        self.assertIn("no longer has gone:latest", self.status())
        self.assertTrue(self.panel.model_row.grid_info())

    def test_turning_off(self):
        storage.set_setting(self.db, "ai_model", "llama3.2:latest")
        with self.with_models("llama3.2:latest"):
            self.panel.refresh()
            self.wait()
        self.panel.ai_switch.flip()
        self.assertIsNone(self.setting("ai_model"))
        self.assertIn("Off", self.status())

    def test_turning_off_before_a_check_returns_ignores_the_check(self):
        with self.with_models("llama3.2:latest"):
            self.panel.ai_switch.flip()
            self.panel.ai_switch.flip()
            self.wait()
        self.assertIsNone(self.setting("ai_model"))
        self.assertFalse(self.panel.ai_switch.on)
        self.assertIn("Off", self.status())

    def test_the_window_is_not_blocked_by_the_check(self):
        release = threading.Event()

        def slow():
            release.wait(5)
            return ["llama3.2:latest"]

        with mock.patch.object(ollama, "list_models", slow):
            self.panel.ai_switch.flip()
            self.assertIn("Looking for Ollama", self.status())
            release.set()
            self.wait()
        self.assertIn("using", self.status())


class WeatherTest(PanelTest):
    def places(self, *found):
        return mock.patch.object(weather, "find_places", return_value=list(found))

    def save(self, text):
        self.panel.place_entry.delete(0, "end")
        self.panel.place_entry.insert(0, text)
        self.panel.save_place()

    def test_one_match_is_saved(self):
        with self.places(BOSTON):
            self.save("02108")
            self.wait()
        self.assertEqual(json.loads(self.setting("location"))["name"], BOSTON["name"])
        self.assertIn("Boston", self.panel.location_label.cget("text"))
        self.assertEqual(self.panel.place_entry.get(), "")
        self.assertEqual(self.link_texts(self.panel.place_links), ["Forget my location"])

    def test_several_matches_can_be_clicked(self):
        with self.places(BOSTON, PARIS):
            self.save("somewhere")
            self.wait()
        self.assertIn("Which one", self.panel.place_message.cget("text"))
        self.assertIsNone(self.setting("location"))
        self.root.update()
        rows = self.panel.place_choices.winfo_children()
        self.assertEqual(len(rows), 2)
        self.click_link(rows[1], PARIS["name"])
        self.root.update()
        self.assertEqual(json.loads(self.setting("location"))["name"], PARIS["name"])
        self.assertEqual(self.panel.place_choices.winfo_children(), [])

    def test_nothing_found_and_no_connection_save_nothing(self):
        with self.places():
            self.save("Nowhereville")
            self.wait()
        self.assertIn("can't find", self.panel.place_message.cget("text"))
        with mock.patch.object(weather, "find_places", side_effect=OSError):
            self.save("Boston")
            self.wait()
        self.assertIn("couldn't reach", self.panel.place_message.cget("text"))
        self.assertIsNone(self.setting("location"))

    def test_empty_sections_take_no_space(self):
        panel = self.panel
        self.assertFalse(panel.place_message.grid_info())
        self.assertFalse(panel.place_links.grid_info())
        self.assertFalse(panel.place_choices.grid_info())
        self.assertFalse(panel.ai_links.grid_info())

        with self.places(BOSTON, PARIS):
            self.save("somewhere")
            self.wait()
        self.assertTrue(panel.place_choices.grid_info())

        panel.choose_place(BOSTON)
        self.assertFalse(panel.place_choices.grid_info())
        self.assertTrue(panel.place_links.grid_info())
        panel.forget_place()
        self.assertFalse(panel.place_links.grid_info())

    def test_the_page_returns_to_its_height_when_ai_details_go_away(self):
        def height():
            self.root.update()
            return self.panel.body.winfo_reqheight()

        self.root.deiconify()  # A hidden window doesn't recalculate its layout.
        # A wide page, so that the status text stays on one line and only the rows differ.
        for label in self.panel.wrapped:
            label.configure(wraplength=2000)
        self.panel.refresh_ai()
        start = height()
        with self.with_models("llama3.2:latest", "qwen:latest"):
            self.panel.ai_switch.flip()
            self.wait()
        self.assertGreater(height(), start)
        self.panel.ai_switch.flip()
        self.assertEqual(height(), start)

        self.without_ollama()
        self.panel.ai_switch.flip()
        self.wait()
        self.assertTrue(self.panel.ai_links.grid_info())
        self.panel.refresh_ai()
        self.assertFalse(self.panel.ai_links.grid_info())
        self.assertEqual(height(), start)

    def test_a_blank_box_does_nothing(self):
        with mock.patch.object(weather, "find_places") as find:
            self.save("   ")
        find.assert_not_called()

    def test_forgetting(self):
        storage.set_setting(self.db, "location", json.dumps(BOSTON))
        self.panel.refresh()
        self.panel.forget_place()
        self.assertIsNone(self.setting("location"))
        self.assertIn("not set", self.panel.location_label.cget("text"))
        self.assertEqual(self.link_texts(self.panel.place_links), [])

    def test_units_highlight_what_applies(self):
        fahrenheit, celsius = self.panel.unit_buttons["fahrenheit"], self.panel.unit_buttons["celsius"]
        self.assertEqual(fahrenheit.cget("bg"), gui.ACCENT)
        storage.set_setting(self.db, "location", json.dumps(PARIS))
        self.panel.refresh()
        self.assertEqual(celsius.cget("bg"), gui.ACCENT)
        self.panel.choose_unit("fahrenheit")
        self.assertEqual(self.setting("temperature_unit"), "fahrenheit")
        self.assertEqual(fahrenheit.cget("bg"), gui.ACCENT)
        self.assertNotEqual(celsius.cget("bg"), gui.ACCENT)


class GeneralTest(PanelTest):
    def test_login_switch_changes_login_only_when_flipped(self):
        self.assertFalse(self.panel.login_switch.on)
        self.assertFalse(self.login["on"])
        self.panel.login_switch.flip()
        self.assertTrue(self.login["on"])
        self.panel.login_switch.flip()
        self.assertFalse(self.login["on"])

    def test_using_the_login_switch_stops_the_offer(self):
        self.panel.login_switch.flip()
        self.panel.login_switch.flip()
        self.assertFalse(startup.should_offer(self.db))

    def test_briefing_switch(self):
        self.assertTrue(self.panel.briefing_switch.on)
        self.panel.briefing_switch.flip()
        self.assertFalse(briefing.is_due(self.db))
        self.panel.briefing_switch.flip()
        self.assertEqual(self.setting("briefing_automatic"), None)

    def test_report_a_bug_opens_the_new_issue_page(self):
        self.assertEqual(self.link_texts(self.panel.report_links), ["Report a bug"])
        with mock.patch("webbrowser.open") as browser:
            self.root.update()
            self.click_link(self.panel.report_links, "Report a bug")
            self.root.update()
        browser.assert_called_once_with("https://github.com/BrayanVillatoro/OSSI/issues/new")

    def test_space_flips_a_switch(self):
        self.root.deiconify()  # Key presses only reach a visible window that has the focus.
        self.root.update()
        self.panel.briefing_switch.focus_force()
        self.root.update()
        self.panel.briefing_switch.event_generate("<space>")
        self.root.update()
        self.assertEqual(self.setting("briefing_automatic"), "off")


class AgreesWithTypedCommandsTest(PanelTest):
    def test_typed_commands_show_up_when_the_page_opens(self):
        self.app.toggle_settings()
        session = self.app.session
        router.handle(self.db, "use celsius", session)
        router.handle(self.db, "turn off the daily briefing", session)
        router.handle(self.db, "start at login", session)
        storage.set_setting(self.db, "location", json.dumps(BOSTON))
        self.app.toggle_settings()

        self.assertEqual(self.panel.unit_buttons["celsius"].cget("bg"), gui.ACCENT)
        self.assertFalse(self.panel.briefing_switch.on)
        self.assertTrue(self.panel.login_switch.on)
        self.assertIn("Boston", self.panel.location_label.cget("text"))

    def test_the_page_changes_what_typed_commands_report(self):
        with self.with_models("llama3.2:latest"):
            self.panel.ai_switch.flip()
            self.wait()
            self.app.session.defer = None
            reply = router.handle(self.db, "ai status", self.app.session)
        self.assertIn("using llama3.2:latest", reply)


class LoginUnsupportedTest(unittest.TestCase):
    def test_the_switch_is_left_out(self):
        gc.collect()
        self.addCleanup(gc.collect)
        db = storage.connect(":memory:")
        self.addCleanup(db.close)
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"no display: {error}")
        self.addCleanup(root.destroy)
        root.withdraw()
        with mock.patch.object(autostart, "supported", lambda: False):
            app = gui.App(root, db, notify=lambda title, body: None)
            app.toggle_settings()
        self.assertIsNone(app.settings.login_switch)


class InstalledCheckTest(unittest.TestCase):
    def test_finds_it_on_the_path_or_in_the_usual_folder(self):
        with mock.patch("shutil.which", return_value="C:/x/ollama.exe"):
            self.assertTrue(ollama.is_installed())
        with mock.patch("shutil.which", return_value=None), mock.patch.dict(
            "os.environ", {"LOCALAPPDATA": "Z:/does-not-exist"}
        ):
            self.assertFalse(ollama.is_installed())


if __name__ == "__main__":
    unittest.main()

import gc
import sys
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ossi import autostart, desktop, gui, storage


class FakeDesktopTest(unittest.TestCase):
    """Uses a temporary folder as the Desktop, so the real one is never touched."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.link = Path(folder.name) / desktop.LINK_NAME
        patcher = mock.patch.object(desktop, "link_path", lambda: self.link)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(desktop, "supported", lambda: True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db = storage.connect(":memory:")
        self.addCleanup(self.db.close)


class OfferTest(FakeDesktopTest):
    def test_offers_when_there_is_no_shortcut(self):
        self.assertTrue(desktop.should_offer(self.db))

    def test_only_once(self):
        desktop.mark_offered(self.db)
        self.assertFalse(desktop.should_offer(self.db))

    def test_never_if_the_shortcut_is_already_there(self):
        self.link.touch()
        self.assertFalse(desktop.should_offer(self.db))
        self.link.unlink()
        self.assertFalse(desktop.should_offer(self.db))  # Deleting it doesn't bring the question back.

    def test_not_where_it_is_unsupported(self):
        with mock.patch.object(desktop, "supported", lambda: False):
            self.assertFalse(desktop.should_offer(self.db))


class CreateTest(FakeDesktopTest):
    def test_the_shortcut_gets_ossi_icon_and_opens_ossi(self):
        with mock.patch("subprocess.run") as run:
            self.assertTrue(desktop.create())
        args, kwargs = run.call_args
        details = kwargs["env"]
        self.assertEqual(details["OSSI_LINK"], str(self.link))
        self.assertTrue(details["OSSI_ICON"].endswith("ossi.ico,0"))
        self.assertTrue(desktop.ICON.is_file())
        self.assertIn("pythonw", details["OSSI_TARGET"].lower())
        self.assertIn("OSSI.pyw", details["OSSI_ARGUMENTS"])
        self.assertNotIn("--background", details["OSSI_ARGUMENTS"])
        self.assertTrue(kwargs["check"])

    def test_a_folder_name_is_never_part_of_the_powershell_command(self):
        with mock.patch("subprocess.run") as run:
            desktop.create()
        self.assertNotIn(str(autostart.LAUNCHER.parent), " ".join(run.call_args[0][0]))

    def test_login_still_starts_in_the_background(self):
        self.assertTrue(autostart.command_line().endswith("--background"))


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class RealPathTest(unittest.TestCase):
    def test_the_real_path_is_on_a_desktop_folder(self):
        path = desktop.link_path()
        self.assertEqual(path.name, "OSSI.lnk")
        self.assertTrue(path.parent.is_dir())


class WindowTest(FakeDesktopTest):
    def setUp(self):
        super().setUp()
        gc.collect()
        self.addCleanup(gc.collect)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"no display: {error}")
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        self.app = gui.App(self.root, self.db, notify=lambda title, body: None)
        for name, replacement in (("is_enabled", lambda: False), ("supported", lambda: True)):
            patcher = mock.patch.object(autostart, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def log(self):
        return self.app.log.get("1.0", "end")

    def offer(self, answer, create=None):
        with mock.patch.object(self.app, "is_in_use", return_value=True), mock.patch(
            "ossi.gui.messagebox.askyesno", return_value=answer
        ) as asked, mock.patch.object(desktop, "create", create or mock.Mock(return_value=True)) as made:
            self.app.check_desktop_offer()
            self.wait_for_fetch()
        return asked, made

    def wait_for_fetch(self):
        deadline = time.time() + 2
        while self.app.fetched.empty() and time.time() < deadline:
            time.sleep(0.01)
            if "OK." in self.log():
                return
        self.app.check_fetches()

    def test_yes_adds_the_shortcut(self):
        asked, made = self.offer(True)
        asked.assert_called_once()
        made.assert_called_once()
        self.assertIn(gui.DESKTOP_ADDED, self.log())

    def test_no_changes_nothing_and_says_where_to_find_it(self):
        _, made = self.offer(False)
        made.assert_not_called()
        self.assertIn(gui.DESKTOP_LATER, self.log())

    def test_a_failure_says_so(self):
        self.offer(True, mock.Mock(side_effect=OSError))
        self.assertIn(gui.DESKTOP_FAILED, self.log())

    def test_asks_only_once(self):
        self.offer(False)
        asked, _ = self.offer(False)
        asked.assert_not_called()

    def test_waits_until_the_window_is_in_use(self):
        with mock.patch.object(self.app, "is_in_use", return_value=False), mock.patch(
            "ossi.gui.messagebox.askyesno"
        ) as asked:
            self.app.check_desktop_offer()
        asked.assert_not_called()
        self.assertTrue(desktop.should_offer(self.db))

    def test_the_question_is_marked_asked_before_it_shows(self):
        # Timers keep running behind the question, so a second tick mustn't ask again.
        seen = []

        def ask(*args):
            seen.append(desktop.should_offer(self.db))
            return False

        with mock.patch.object(self.app, "is_in_use", return_value=True), mock.patch(
            "ossi.gui.messagebox.askyesno", ask
        ):
            self.app.check_desktop_offer()
        self.assertEqual(seen, [False])

    def link_texts(self):
        panel = self.app.settings
        return [child.cget("text") for child in panel.desktop_links.winfo_children()]

    def open_settings(self):
        with mock.patch("ossi.ollama.list_models", return_value=[]):
            self.app.toggle_settings()

    def test_settings_offers_to_add_it_when_missing(self):
        self.open_settings()
        self.assertEqual(self.link_texts(), ["Add to Desktop"])
        self.assertIn("Not on your Desktop", self.app.settings.desktop_status.cget("text"))

    def test_settings_can_add_it_again_when_it_is_there(self):
        self.link.touch()
        self.open_settings()
        self.assertEqual(self.link_texts(), ["Add it again"])
        self.assertIn("It's on your Desktop", self.app.settings.desktop_status.cget("text"))

    def test_adding_from_settings_works_without_ever_being_offered(self):
        desktop.mark_offered(self.db)
        self.open_settings()

        def create():
            self.link.touch()
            return True

        with mock.patch.object(desktop, "create", create):
            self.app.settings.add_to_desktop()
            self.finish_settings_fetches()
        self.assertTrue(self.link.exists())
        self.assertEqual(self.app.settings.desktop_status.cget("text"), "Added to your Desktop.")
        self.assertEqual(self.link_texts(), ["Add it again"])

    def test_a_failure_in_settings_says_so(self):
        self.open_settings()
        with mock.patch.object(desktop, "create", side_effect=OSError):
            self.app.settings.add_to_desktop()
            self.finish_settings_fetches()
        self.assertIn("didn't work", self.app.settings.desktop_status.cget("text"))
        self.assertEqual(self.link_texts(), ["Add to Desktop"])

    def finish_settings_fetches(self):
        deadline = time.time() + 2
        status = self.app.settings.desktop_status
        while status.cget("text") == "Adding it..." and time.time() < deadline:
            time.sleep(0.01)
            self.app.check_fetches()


if __name__ == "__main__":
    unittest.main()

import gc
import io
import json
import sys
import tempfile
import time
import tkinter as tk
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ossi import __version__, gui, storage, updater
from ossi.updater import Release, UpdateError

NEWER = "99.0.0"
RELEASE = Release(NEWER, f"v{NEWER}", "https://example.com/release")


def release_zip(version=NEWER, extra=None, top="OSSI-99.0.0", code="VALUE = 1\n", skip=()):
    files = {
        "OSSI.pyw": "# launcher\n",
        "README.md": "new readme\n",
        "src/ossi/__init__.py": f'__version__ = "{version}"\n',
        "src/ossi/gui.py": code,
        "src/ossi/router.py": "VALUE = 2\n",
        **(extra or {}),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            if name not in skip:
                archive.writestr(f"{top}/{name}" if top else name, text)
    return buffer.getvalue()


def answering(body):
    response = mock.MagicMock()
    response.__enter__.return_value = io.BytesIO(body)
    return mock.patch.object(updater.OPENER, "open", return_value=response)


class Installed(unittest.TestCase):
    """A pretend copy of OSSI in a temporary folder, so the real files are never touched."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.write("OSSI.pyw", "# old launcher\n")
        self.write("README.md", "old readme\n")
        self.write("src/ossi/__init__.py", '__version__ = "1.0.0"\n')
        self.write("src/ossi/gui.py", "OLD = True\n")
        self.write("src/ossi/skills/removed_skill.py", "OLD = True\n")
        self.write("my_own_file.txt", "mine\n")

    def write(self, name, text):
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def read(self, name):
        return (self.folder / name).read_text()

    def assert_unchanged(self):
        self.assertEqual(self.read("README.md"), "old readme\n")
        self.assertEqual(self.read("src/ossi/gui.py"), "OLD = True\n")
        self.assertTrue((self.folder / "src/ossi/skills/removed_skill.py").is_file())
        self.assertFalse((self.folder / updater.WORKING_FOLDER).exists())


class CheckTest(unittest.TestCase):
    def latest(self, tag):
        body = json.dumps({"tag_name": tag, "html_url": "https://example.com/r"}).encode()
        with answering(body):
            return updater.check()

    def test_a_newer_release_is_found(self):
        release = self.latest("v99.1.2")
        self.assertEqual((release.version, release.tag), ("99.1.2", "v99.1.2"))

    def test_the_same_version_is_not_an_update(self):
        self.assertFalse(self.latest(f"v{__version__}"))

    def test_an_older_version_is_not_an_update(self):
        self.assertFalse(self.latest("v0.0.1"))

    def test_versions_compare_as_numbers(self):
        with mock.patch.object(updater, "__version__", "1.9.0"):
            self.assertTrue(self.latest("v1.10.0"))

    def test_a_tag_that_is_not_a_version_is_ignored(self):
        self.assertFalse(self.latest("nightly"))
        self.assertFalse(self.latest("v1.0.0/../../evil"))

    def test_nothing_published_yet_is_not_an_error(self):
        error = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
        with mock.patch.object(updater.OPENER, "open", side_effect=error):
            self.assertFalse(updater.check())

    def test_other_failures_are_errors(self):
        error = urllib.error.HTTPError("url", 500, "Server Error", {}, None)
        with mock.patch.object(updater.OPENER, "open", side_effect=error):
            with self.assertRaises(urllib.error.HTTPError):
                updater.check()

    def test_this_copy_has_a_valid_version(self):
        self.assertIsNotNone(updater.version_parts(__version__))


class BlockedTest(Installed):
    def test_a_normal_copy_can_update(self):
        self.assertIsNone(updater.blocked_reason(self.folder))

    def test_a_git_copy_is_never_overwritten(self):
        (self.folder / ".git").mkdir()
        self.assertIn("git pull", updater.blocked_reason(self.folder))

    def test_a_package_install_cannot(self):
        (self.folder / "OSSI.pyw").unlink()
        self.assertIn("pip", updater.blocked_reason(self.folder))


class InstallTest(Installed):
    def install(self, body):
        with answering(body), mock.patch("subprocess.run", return_value=mock.Mock(returncode=0)):
            updater.install(RELEASE, self.folder)

    def test_replaces_files_and_keeps_the_previous_version(self):
        self.install(release_zip())
        self.assertEqual(self.read("README.md"), "new readme\n")
        self.assertEqual(self.read("src/ossi/gui.py"), "VALUE = 1\n")
        self.assertEqual(self.read(f"{updater.PREVIOUS_FOLDER}/README.md"), "old readme\n")
        self.assertFalse((self.folder / updater.WORKING_FOLDER).exists())

    def test_a_skill_no_longer_in_the_release_is_gone(self):
        # Skills load automatically, so a leftover one would keep running.
        self.install(release_zip())
        self.assertFalse((self.folder / "src/ossi/skills/removed_skill.py").exists())

    def test_files_that_are_not_part_of_ossi_stay(self):
        self.install(release_zip())
        self.assertEqual(self.read("my_own_file.txt"), "mine\n")

    def test_a_second_update_replaces_the_saved_version(self):
        self.install(release_zip())
        self.install(release_zip(extra={"README.md": "newer readme\n"}))
        self.assertEqual(self.read(f"{updater.PREVIOUS_FOLDER}/README.md"), "new readme\n")

    def refused(self, body, message):
        with self.assertRaises(UpdateError) as caught:
            self.install(body)
        self.assertIn(message, str(caught.exception))
        self.assert_unchanged()

    def test_a_corrupt_download_changes_nothing(self):
        self.refused(b"this is not a zip file", "didn't download correctly")

    def test_a_download_with_the_wrong_version_changes_nothing(self):
        self.refused(release_zip(version="98.0.0"), "version didn't match")

    def test_a_download_missing_files_changes_nothing(self):
        self.refused(release_zip(skip={"src/ossi/gui.py"}), "missing")

    def test_a_file_escaping_the_folder_is_refused(self):
        self.refused(release_zip(extra={"../../evil.txt": "x"}), "unsafe")
        self.assertFalse((self.folder.parent / "evil.txt").exists())

    def test_an_absolute_path_is_refused(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("C:/evil.txt", "x")
        self.refused(buffer.getvalue(), "unsafe")

    def test_files_not_in_one_top_folder_are_refused(self):
        self.refused(release_zip(top=""), "laid out")

    def test_a_download_that_is_too_large_is_stopped(self):
        with mock.patch.object(updater, "MOST_BYTES", 10):
            self.refused(release_zip(), "larger")

    def test_a_network_failure_changes_nothing(self):
        with mock.patch.object(updater.OPENER, "open", side_effect=OSError):
            with self.assertRaises(OSError):
                updater.install(RELEASE, self.folder)
        self.assert_unchanged()

    def test_code_that_cannot_start_changes_nothing(self):
        # This one runs for real, with broken code in the download.
        with answering(release_zip(code="this is not python\n")):
            with self.assertRaises(UpdateError) as caught:
                updater.install(RELEASE, self.folder)
        self.assertIn("wouldn't start", str(caught.exception))
        self.assert_unchanged()

    def test_a_failure_partway_through_puts_everything_back(self):
        real_replace = updater.os.replace
        calls = []

        def fail_later(source, target):
            calls.append(source)
            if len(calls) == 4:
                raise PermissionError
            real_replace(source, target)

        with answering(release_zip()), mock.patch(
            "subprocess.run", return_value=mock.Mock(returncode=0)
        ), mock.patch.object(updater.os, "replace", fail_later):
            with self.assertRaises(UpdateError) as caught:
                updater.install(RELEASE, self.folder)
        self.assertIn("kept this version", str(caught.exception))
        self.assert_unchanged()
        self.assertEqual(self.read("OSSI.pyw"), "# old launcher\n")


class SettingsTest(unittest.TestCase):
    def setUp(self):
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
        patcher = mock.patch.object(updater, "blocked_reason", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = gui.App(self.root, self.db, notify=lambda title, body: None)
        self.panel = self.app.settings
        with mock.patch("ossi.ollama.list_models", return_value=[]):
            self.app.toggle_settings()

    def status(self):
        return self.panel.update_status.cget("text")

    def links(self):
        return [child.cget("text") for child in self.panel.update_links.winfo_children()]

    def finish(self, waiting_status):
        deadline = time.time() + 5
        while self.status() == waiting_status and time.time() < deadline:
            time.sleep(0.01)
            self.app.check_fetches()
        self.assertNotEqual(self.status(), waiting_status)

    def check(self, **result):
        with mock.patch.object(updater, "check", **result):
            self.panel.check_updates()
            self.finish("Checking...")

    def test_shows_the_version_and_offers_a_check(self):
        self.assertEqual(self.links(), ["Check for updates"])

    def test_never_checks_until_asked(self):
        with mock.patch.object(updater, "check") as check:
            self.panel.refresh_updates()
        check.assert_not_called()

    def test_a_copy_that_cannot_update_says_why_and_offers_nothing(self):
        with mock.patch.object(updater, "blocked_reason", return_value="Use git pull."):
            self.panel.refresh_updates()
        self.assertEqual(self.status(), "Use git pull.")
        self.assertEqual(self.links(), [])

    def test_up_to_date(self):
        self.check(return_value=False)
        self.assertIn("newest version", self.status())

    def test_a_failed_check_says_so(self):
        self.check(side_effect=OSError)
        self.assertIn("couldn't check", self.status())
        self.assertEqual(self.links(), ["Try again"])

    def test_an_update_is_offered_and_not_installed_until_clicked(self):
        with mock.patch.object(updater, "install") as install:
            self.check(return_value=RELEASE)
        install.assert_not_called()
        self.assertIn(NEWER, self.status())
        self.assertEqual(self.links(), ["Update now", "What's new"])

    def update(self, **result):
        with mock.patch.object(updater, "install", **result), mock.patch.object(
            updater, "start_new_copy"
        ) as restart:
            self.panel.install_update(RELEASE)
            self.finish(f"Updating to version {NEWER}...")
        return restart

    def test_a_successful_update_restarts(self):
        restart = self.update()
        restart.assert_called_once()
        self.assertIn("Restarting", self.status())

    def test_a_refused_update_says_why_and_does_not_restart(self):
        restart = self.update(side_effect=UpdateError("Bad download."))
        restart.assert_not_called()
        self.assertEqual(self.status(), "Bad download.")
        self.assertEqual(self.links(), ["Try again"])

    def test_an_unexpected_failure_does_not_restart(self):
        restart = self.update(side_effect=OSError)
        restart.assert_not_called()
        self.assertIn("kept this version", self.status())

    def test_reopening_settings_mid_update_does_not_offer_another(self):
        with mock.patch.object(updater, "install"), mock.patch.object(updater, "start_new_copy"):
            self.panel.install_update(RELEASE)
            self.panel.refresh_updates()
            self.assertEqual(self.links(), [])
            self.finish(f"Updating to version {NEWER}...")


if __name__ == "__main__":
    unittest.main()

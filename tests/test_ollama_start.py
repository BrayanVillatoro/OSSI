import gc
import sys
import threading
import time
import tkinter as tk
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ossi import gui, ollama, router, storage
from ossi.registry import Fetch

REFUSED = urllib.error.URLError(ConnectionRefusedError())


class StartTest(unittest.TestCase):
    """Nothing here really starts Ollama, which might be running on this computer."""

    def setUp(self):
        for patcher in (
            mock.patch.object(ollama, "START_CHECK_SECONDS", 0.001),
            mock.patch.object(ollama, "find_program", return_value="C:/Ollama/ollama.exe"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch("subprocess.Popen")
        self.popen = patcher.start()
        self.addCleanup(patcher.stop)

    def running(self, *answers):
        return mock.patch.object(ollama, "is_running", side_effect=answers)

    def test_does_nothing_if_it_is_already_running(self):
        with self.running(True):
            self.assertTrue(ollama.start())
        self.popen.assert_not_called()

    def test_starts_it_and_waits_until_it_answers(self):
        with self.running(False, False, False, True):
            self.assertTrue(ollama.start())
        self.popen.assert_called_once()
        self.assertEqual(self.popen.call_args[0][0], ["C:/Ollama/ollama.exe", "serve"])

    def test_it_is_only_ever_reachable_from_this_computer(self):
        with self.running(False, True), mock.patch.dict("os.environ", {"OLLAMA_HOST": "0.0.0.0"}):
            ollama.start()
        self.assertEqual(self.popen.call_args[1]["env"]["OLLAMA_HOST"], "127.0.0.1:11434")

    def test_not_installed(self):
        with self.running(False), mock.patch.object(ollama, "find_program", return_value=None):
            self.assertFalse(ollama.start())
        self.popen.assert_not_called()

    def test_a_program_that_will_not_run(self):
        self.popen.side_effect = OSError
        with self.running(False):
            self.assertFalse(ollama.start())

    def test_gives_up_if_it_never_answers(self):
        with mock.patch.object(ollama, "is_running", return_value=False), mock.patch.object(
            ollama, "START_TIMEOUT_SECONDS", 0.01
        ):
            self.assertFalse(ollama.start())

    def test_two_at_once_only_start_it_once(self):
        state = {"up": False}
        self.popen.side_effect = lambda *args, **kwargs: state.update(up=True)
        with mock.patch.object(ollama, "is_running", lambda: state["up"]):
            threads = [threading.Thread(target=ollama.start) for _ in range(5)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.popen.assert_called_once()


class StartingIfNeededTest(unittest.TestCase):
    def test_nothing_is_started_when_the_work_succeeds(self):
        with mock.patch.object(ollama, "start") as start:
            self.assertEqual(ollama.starting_if_needed(lambda: "answer"), "answer")
        start.assert_not_called()

    def test_starts_it_and_tries_again(self):
        work = mock.Mock(side_effect=[REFUSED, "answer"])
        with mock.patch.object(ollama, "start", return_value=True) as start:
            self.assertEqual(ollama.starting_if_needed(work), "answer")
        start.assert_called_once()

    def test_reports_failure_if_it_cannot_start(self):
        with mock.patch.object(ollama, "start", return_value=False):
            with self.assertRaises(urllib.error.URLError):
                ollama.starting_if_needed(mock.Mock(side_effect=REFUSED))

    def test_a_slow_answer_does_not_start_another(self):
        # Once connected, a model can take a long time. That doesn't mean Ollama isn't running.
        with mock.patch.object(ollama, "start") as start:
            with self.assertRaises(TimeoutError):
                ollama.starting_if_needed(mock.Mock(side_effect=TimeoutError))
        start.assert_not_called()


class ConversationTest(unittest.TestCase):
    def setUp(self):
        self.db = storage.connect(":memory:")
        self.addCleanup(self.db.close)
        storage.set_setting(self.db, "ai_model", "llama3.2:latest")

    def refused_once(self, name, answer):
        return mock.patch.object(ollama, name, side_effect=[REFUSED, answer])

    def test_a_typed_message_starts_it(self):
        with self.refused_once("chat", {"command": "", "reply": "Paris."}), mock.patch.object(
            ollama, "start", return_value=True
        ) as start:
            self.assertEqual(router.handle(self.db, "what is the capital of france"), "Paris.")
        start.assert_called_once()

    def test_a_spoken_message_starts_it(self):
        with self.refused_once("talk", "Paris."), mock.patch.object(
            ollama, "start", return_value=True
        ) as start:
            reply = router.handle_spoken(self.db, "capital of france", router.Session())
        self.assertEqual(reply, "Paris.")
        start.assert_called_once()

    def test_turning_it_on_starts_it(self):
        storage.delete_setting(self.db, "ai_model")
        with self.refused_once("list_models", ["llama3.2:latest"]), mock.patch.object(
            ollama, "start", return_value=True
        ):
            self.assertIn("The AI is on", router.handle(self.db, "turn on ai"))

    def test_with_the_ai_off_it_is_never_started(self):
        storage.delete_setting(self.db, "ai_model")
        with mock.patch.object(ollama, "start") as start:
            self.assertEqual(router.handle(self.db, "what is the capital of france"), router.FALLBACK)
        start.assert_not_called()

    def test_if_it_cannot_start_the_message_says_so(self):
        with mock.patch.object(ollama, "chat", side_effect=REFUSED), mock.patch.object(
            ollama, "start", return_value=False
        ):
            self.assertIn("didn't answer", router.handle(self.db, "what is the capital of france"))


class WindowTest(unittest.TestCase):
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
        self.app = gui.App(self.root, self.db, notify=lambda title, body: None)

    def opening(self):
        done = threading.Event()
        with mock.patch.object(ollama, "start", side_effect=done.set) as start:
            self.app.start_ollama_if_on()
            done.wait(0.2)
        return start

    def test_opening_starts_it_when_the_ai_is_on(self):
        storage.set_setting(self.db, "ai_model", "llama3.2:latest")
        self.opening().assert_called_once()

    def test_opening_leaves_it_alone_when_the_ai_is_off(self):
        self.opening().assert_not_called()


if __name__ == "__main__":
    unittest.main()

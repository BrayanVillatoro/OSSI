import gc
import os
import queue
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ossi import gui, ollama, router, storage, talk, voice, voice_worker
from ossi.voice_worker import END_FRAMES, INTERRUPT_FRAMES, START_FRAMES, Utterances, is_echo


class UtterancesTest(unittest.TestCase):
    def feed(self, utterances, level, count, speaking=False):
        return [utterances.add("frame", level, speaking) for _ in range(count)]

    def test_a_sentence_starts_and_ends(self):
        utterances = Utterances()
        self.assertEqual(self.feed(utterances, 0.0, 5).count(None), 5)
        results = self.feed(utterances, 0.9, START_FRAMES)
        self.assertEqual(results[-1], "started")
        results = self.feed(utterances, 0.0, END_FRAMES)
        self.assertIsInstance(results[-1], list)
        self.assertTrue(all(result is None for result in results[:-1]))

    def test_a_short_noise_is_not_speech(self):
        utterances = Utterances()
        results = self.feed(utterances, 0.9, START_FRAMES - 1) + self.feed(utterances, 0.0, 1)
        results += self.feed(utterances, 0.9, START_FRAMES - 1)
        self.assertNotIn("started", results)

    def test_a_pause_in_the_middle_does_not_end_it(self):
        utterances = Utterances()
        self.feed(utterances, 0.9, START_FRAMES)
        self.feed(utterances, 0.0, END_FRAMES - 1)
        self.feed(utterances, 0.9, 1)
        self.assertEqual(self.feed(utterances, 0.0, END_FRAMES - 1).count(None), END_FRAMES - 1)

    def test_interrupting_takes_more_than_starting(self):
        utterances = Utterances()
        results = self.feed(utterances, 0.6, INTERRUPT_FRAMES * 2, speaking=True)
        self.assertNotIn("started", results)
        results = self.feed(utterances, 0.9, INTERRUPT_FRAMES, speaking=True)
        self.assertEqual(results[-1], "started")

    def test_speech_is_cut_off_if_it_goes_on_forever(self):
        utterances = Utterances()
        results = self.feed(utterances, 0.9, voice_worker.MOST_FRAMES + START_FRAMES)
        self.assertTrue(any(isinstance(result, list) for result in results))

    def test_keeps_the_audio_from_just_before_it_was_sure(self):
        utterances = Utterances()
        for number in range(3):
            utterances.add(number, 0.0)
        for number in range(3, 3 + START_FRAMES):
            utterances.add(number, 0.9)
        self.assertEqual(utterances.frames[:2], [0, 1])


class EchoTest(unittest.TestCase):
    def test_its_own_words_are_an_echo(self):
        self.assertTrue(is_echo("sorry to hear that", ["I'm sorry to hear that."]))

    def test_something_new_is_not(self):
        self.assertFalse(is_echo("what about tomorrow then", ["I'm sorry to hear that."]))

    def test_nothing_heard_is_not(self):
        self.assertFalse(is_echo("", ["anything"]))


class SentencesTest(unittest.TestCase):
    def test_splits_sentences_and_removes_symbols(self):
        self.assertEqual(
            talk.sentences("Hi **there**! How are you?\n# Fine."),
            ["Hi there!", "How are you?", "Fine."],
        )

    def test_only_counts_sentences_that_have_ended(self):
        self.assertEqual(talk.finished_length("Still going"), 0)
        self.assertEqual(talk.finished_length("Done. Still go"), 6)
        self.assertEqual(talk.finished_length("Almost done."), 0)  # More may follow the period.


class ModelTextTest(unittest.TestCase):
    def test_talk_sends_plain_chat_and_keeps_the_model_loaded(self):
        captured = {}

        def stream(model, body):
            captured.update(body)
            yield "Hi "
            yield "there."

        seen = []
        with mock.patch.object(ollama, "stream", stream):
            answer = ollama.talk("m", "rules", "now", [], on_text=seen.append)
        self.assertEqual(answer, "Hi there.")
        self.assertEqual(seen, ["Hi ", "Hi there."])
        self.assertNotIn("format", captured)
        self.assertEqual(captured["keep_alive"], ollama.KEEP_LOADED)

    def test_refuses_a_model_hosted_online(self):
        with self.assertRaises(ValueError):
            ollama.talk("big:120b-cloud", "rules", "now")


class SpokenTest(unittest.TestCase):
    def setUp(self):
        self.db = storage.connect(":memory:")
        self.addCleanup(self.db.close)
        storage.set_setting(self.db, "ai_model", "llama3.2:latest")
        self.session = router.Session()

    def say(self, text):
        return router.handle_spoken(self.db, text, self.session)

    def model_says(self, answer):
        return mock.patch.object(ollama, "talk", return_value=answer)

    def test_looking_something_up_needs_no_confirmation(self):
        self.assertIn("No open tasks", self.say("tasks"))
        self.assertIsNone(self.session.pending)

    def test_changing_something_asks_first(self):
        self.assertEqual(self.say("Add task call the bank."), "Should I add task call the bank?")
        self.assertIn("No open tasks", router.handle(self.db, "tasks", router.Session()))
        self.assertEqual(self.say("Yes, please."), "Added task 1: call the bank")

    def test_saying_no_changes_nothing(self):
        self.say("add task call the bank")
        self.assertEqual(self.say("No, don't."), router.NEVER_MIND)
        self.assertIn("No open tasks", router.handle(self.db, "tasks", router.Session()))

    def test_settings_also_ask_first(self):
        self.assertEqual(self.say("start at login"), "Should I start at login?")

    def test_everyday_words_are_not_corrected_as_typos(self):
        with self.model_says("Nice."):
            self.assertEqual(self.say("taks are fun"), "Nice.")

    def test_conversation_goes_to_the_model(self):
        with self.model_says("Sounds like a long day.") as model:
            self.assertEqual(self.say("I had a long day"), "Sounds like a long day.")
        self.assertEqual(model.call_args[0][2], "I had a long day")

    def test_a_task_being_done_in_conversation_changes_nothing(self):
        router.handle(self.db, "add task laundry", router.Session())
        with self.model_says("Nice work."):
            self.assertEqual(self.say("the laundry is done"), "Nice work.")
        self.assertIn("laundry", router.handle(self.db, "tasks", router.Session()))

    def test_a_model_suggestion_asks_first_and_keeps_its_words(self):
        with self.model_says("Sure thing. [DO: add task buy eggs]"):
            self.assertEqual(self.say("I need eggs"), "Sure thing. Should I add task buy eggs?")
        self.assertEqual(self.say("yeah"), "Added task 1: buy eggs")

    def test_a_suggestion_ossi_cannot_do_is_never_offered(self):
        with self.model_says("Done! [DO: delete everything]"):
            self.assertEqual(self.say("wipe it all"), "Done!")
        self.assertIsNone(self.session.pending)

    def test_a_model_cannot_suggest_changing_settings(self):
        with self.model_says("Okay. [DO: start at login]"):
            self.assertEqual(self.say("start up with my computer please now"), "Okay.")

    def test_a_failed_model_says_so(self):
        with mock.patch.object(ollama, "talk", side_effect=OSError):
            self.assertIn("can't reach", self.say("hello there friend"))
        self.assertEqual(self.session.thinking, 0)

    def test_an_answer_to_a_question_still_works(self):
        self.assertEqual(self.say("Remind me to pay rent."), "Should I remind you to pay rent?")
        self.assertIn("When", self.say("Yes."))
        self.assertIn("Reminder 1 set", self.say("In 2 hours."))

    def test_the_conversation_is_remembered(self):
        with self.model_says("Hello.") as model:
            self.say("first thing")
            self.say("second thing")
        self.assertEqual(
            [entry["content"] for entry in model.call_args[0][3][-2:]], ["first thing", "Hello."]
        )


class FakeProcess:
    created = []

    def __init__(self, voice_name):
        self.voice_name = voice_name
        self.events = queue.Queue()
        self.sent = []
        self.closed = False
        FakeProcess.created.append(self)

    def send(self, **message):
        self.sent.append(message)

    def close(self):
        self.closed = True

    def said(self):
        return [message["text"] for message in self.sent if message["type"] == "say"]


class WindowTest(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.addCleanup(gc.collect)
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        patcher = mock.patch.dict(os.environ, {"OSSI_HOME": folder.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db = storage.connect(":memory:")
        self.addCleanup(self.db.close)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"no display: {error}")
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        for patcher in (
            mock.patch.object(voice, "VoiceProcess", FakeProcess),
            mock.patch.object(ollama, "warm"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        FakeProcess.created = []
        self.app = gui.App(self.root, self.db, notify=lambda title, body: None)
        self.talk = self.app.talk
        storage.set_setting(self.db, "ai_model", "llama3.2:latest")

    def installed(self):
        voice.folder().mkdir(parents=True, exist_ok=True)
        voice.marker_path().write_text(voice.VERSION)

    def begin(self):
        self.installed()
        self.talk.toggle()
        self.process = FakeProcess.created[-1]
        self.event(type="ready")

    def event(self, **message):
        self.process.events.put(message)
        self.talk.poll()

    def log(self):
        return self.app.log.get("1.0", "end")

    def status(self):
        return self.app.talk_label.cget("text")

    def test_needs_the_ai_on_first(self):
        storage.delete_setting(self.db, "ai_model")
        self.talk.toggle()
        self.assertIn("Use a local AI model", self.log())
        self.assertEqual(FakeProcess.created, [])

    def test_declining_the_install_does_nothing(self):
        with mock.patch("ossi.talk.messagebox.askyesno", return_value=False), mock.patch.object(
            voice, "install"
        ) as install:
            self.talk.toggle()
        install.assert_not_called()
        self.assertFalse(self.talk.active)

    def test_accepting_the_install_then_starts_talking(self):
        def fake_install(progress):
            progress("Setting up voice: step")
            voice.folder().mkdir(parents=True, exist_ok=True)
            voice.marker_path().write_text(voice.VERSION)

        with mock.patch("ossi.talk.messagebox.askyesno", return_value=True), mock.patch.object(
            voice, "install", fake_install
        ):
            self.talk.toggle()
            deadline = time.time() + 5
            while not self.talk.active and time.time() < deadline:
                self.root.update()
        self.assertTrue(self.talk.active)

    def test_a_failed_install_says_why(self):
        def broken(progress):
            raise OSError("no internet")

        with mock.patch("ossi.talk.messagebox.askyesno", return_value=True), mock.patch.object(
            voice, "install", broken
        ):
            self.talk.toggle()
            deadline = time.time() + 5
            while "no internet" not in self.log() and time.time() < deadline:
                self.root.update()
        self.assertIn("no internet", self.log())
        self.assertFalse(self.talk.installing)

    def test_greets_and_shows_it_is_listening(self):
        self.begin()
        self.assertEqual(self.process.said(), [talk.GREETING])
        self.assertIn(talk.GREETING, self.log())
        self.assertEqual(self.status(), "Listening...")
        self.assertEqual(self.app.talk_button.cget("text"), "End")

    def test_the_chosen_voice_is_used(self):
        storage.set_setting(self.db, "talk_voice", "George")
        self.begin()
        self.assertEqual(self.process.voice_name, "George")

    def test_an_unknown_voice_falls_back(self):
        storage.set_setting(self.db, "talk_voice", "Nobody")
        self.begin()
        self.assertEqual(self.process.voice_name, voice.DEFAULT_VOICE)

    def test_a_reply_is_read_one_sentence_at_a_time_as_it_is_written(self):
        self.begin()
        more = threading.Event()

        def chat(model, system, message, history, on_text):
            on_text("First one. Second")
            more.wait(5)
            on_text("First one. Second one.")
            return "First one. Second one."

        with mock.patch.object(ollama, "talk", chat):
            self.event(type="heard", text="tell me something")
            self.assertIn("tell me something", self.bubbles())
            self.wait_for(lambda: "First one." in self.process.said())
            self.assertNotIn("Second one.", self.process.said())
            more.set()
            self.wait_for(lambda: not self.app.fetched.empty())
            self.app.check_fetches()
        self.assertEqual(self.process.said(), [talk.GREETING, "First one.", "Second one."])
        self.assertEqual(self.log().count("First one."), 1)

    def bubbles(self):
        return [
            bubble.itemcget(bubble.find_withtag("message")[0], "text")
            for bubble in self.app.log.winfo_children()
        ]

    def wait_for(self, condition):
        deadline = time.time() + 5
        while not condition() and time.time() < deadline:
            self.root.update()
            self.talk.poll()
            time.sleep(0.01)
        self.assertTrue(condition())

    def test_interrupting_stops_the_model_and_keeps_what_was_said(self):
        self.begin()
        stopped = threading.Event()

        def chat(model, system, message, history, on_text):
            deadline = time.time() + 5
            while time.time() < deadline:
                try:
                    on_text("Said this. And then")
                except Exception:
                    stopped.set()
                    raise
                time.sleep(0.01)
            return "too late"

        with mock.patch.object(ollama, "talk", chat):
            self.event(type="heard", text="tell me something")
            self.wait_for(lambda: "Said this." in self.process.said())
            self.event(type="interrupted")
            self.assertTrue(stopped.wait(5))
            self.wait_for(lambda: not self.app.fetched.empty())
            self.app.check_fetches()
        self.assertIn("Said this....", self.log())
        self.assertNotIn("too late", self.log())
        self.assertEqual(self.app.session.thinking, 0)
        self.assertEqual(self.app.writing, {})
        self.assertEqual(self.app.session.history[-1]["content"], "Said this.")

    def test_a_new_message_replaces_an_answer_on_its_way(self):
        self.begin()
        release = threading.Event()

        def chat(model, system, message, history, on_text):
            release.wait(5)
            return f"Answer to {message}."

        with mock.patch.object(ollama, "talk", chat):
            self.event(type="heard", text="first question")
            self.event(type="heard", text="second question")
            release.set()
            self.wait_for(lambda: self.app.fetched.qsize() == 2)
            self.app.check_fetches()
        self.assertNotIn("Answer to first question.", self.log())
        self.assertIn("Answer to second question.", self.log())

    def test_a_command_reply_is_spoken(self):
        self.begin()
        self.event(type="heard", text="tasks")
        self.assertIn("No open tasks", " ".join(self.process.said()))

    def test_goodbye_ends_the_conversation_after_speaking(self):
        self.begin()
        self.event(type="heard", text="Goodbye.")
        self.assertIn(talk.FAREWELL, self.process.said())
        self.assertTrue(self.talk.active)
        self.event(type="quiet")
        self.assertFalse(self.talk.active)
        self.assertTrue(self.process.closed)
        self.assertEqual(self.app.talk_button.cget("text"), "Talk")

    def test_end_button(self):
        self.begin()
        self.talk.toggle()
        self.assertFalse(self.talk.active)
        self.assertTrue(self.process.closed)
        self.assertFalse(self.app.talk_strip.winfo_manager())

    def test_mute(self):
        self.begin()
        self.talk.toggle_mute()
        self.assertEqual(self.process.sent[-1], {"type": "mute", "on": True})
        self.assertEqual(self.status(), "Muted")
        self.assertEqual(self.app.mute_label.cget("text"), "Unmute")
        self.talk.toggle_mute()
        self.assertEqual(self.status(), "Listening...")

    def test_the_voice_stopping_is_reported(self):
        self.begin()
        self.event(type="gone")
        self.assertFalse(self.talk.active)
        self.assertIn("had to end", self.log())

    def test_status_follows_the_conversation(self):
        self.begin()
        self.event(type="hearing")
        self.assertEqual(self.status(), "Listening...")
        self.event(type="speaking")
        self.assertEqual(self.status(), "Speaking...")
        self.event(type="quiet")
        self.assertEqual(self.status(), "Listening...")

    def test_a_reminder_coming_due_is_spoken(self):
        self.begin()
        with self.db:
            self.db.execute(
                "INSERT INTO reminders (message, due_at) VALUES (?, ?)", ("call mom", time.time())
            )
        self.app.check_reminders()
        self.assertIn("Reminder: call mom", self.process.said())

    def test_typing_still_works_while_talking(self):
        self.begin()
        self.app.run("add task typed")
        self.assertIn("typed", self.app.agenda.get("1.0", "end"))

    def test_settings_shows_and_removes_the_add_on(self):
        self.installed()
        self.app.toggle_settings()
        self.assertIn("installed", self.app.settings.voice_status.cget("text"))
        self.app.settings.remove_voice()
        self.assertFalse(voice.is_installed())
        self.assertIn("600 MB", self.app.settings.voice_status.cget("text"))

    def test_choosing_a_voice_in_settings(self):
        self.app.toggle_settings()
        self.app.settings.voice_box.set("George")
        self.app.settings.choose_voice()
        self.assertEqual(storage.get_setting(self.db, "talk_voice"), "George")

    def choose(self, name):
        self.app.settings.voice_box.set(name)
        self.app.settings.choose_voice()

    def sample_of(self, code=None):
        process = mock.Mock()
        process.poll.return_value = code
        return mock.patch.object(voice, "play_sample", return_value=process), process

    def voice_text(self):
        return self.app.settings.voice_status.cget("text")

    def test_choosing_a_voice_plays_a_sample(self):
        self.installed()
        self.app.toggle_settings()
        patcher, process = self.sample_of()
        with patcher as play:
            self.choose("George")
        play.assert_called_once_with("George")
        self.assertEqual(self.voice_text(), "Playing George...")
        process.poll.return_value = 0
        self.app.settings.check_sample(process, "George")
        self.assertIn("installed", self.voice_text())

    def test_no_sample_before_the_add_on_is_installed(self):
        self.app.toggle_settings()
        with mock.patch.object(voice, "play_sample") as play:
            self.choose("George")
        play.assert_not_called()
        self.assertEqual(storage.get_setting(self.db, "talk_voice"), "George")

    def test_a_new_sample_replaces_the_one_playing(self):
        self.installed()
        self.app.toggle_settings()
        patcher, first = self.sample_of()
        with patcher:
            self.choose("George")
        patcher, second = self.sample_of()
        with patcher:
            self.choose("Bella")
        first.kill.assert_called_once()
        second.kill.assert_not_called()
        self.app.settings.check_sample(first, "George")  # Its old check does nothing.
        self.assertEqual(self.voice_text(), "Playing Bella...")

    def test_a_failed_sample_says_so(self):
        self.installed()
        self.app.toggle_settings()
        patcher, process = self.sample_of(code=1)
        with patcher:
            self.choose("George")
        self.app.settings.check_sample(process, "George")
        self.assertIn("couldn't play George", self.voice_text())

    def test_a_sample_that_cannot_start_says_so(self):
        self.installed()
        self.app.toggle_settings()
        with mock.patch.object(voice, "play_sample", side_effect=OSError):
            self.choose("George")
        self.assertIn("couldn't play George", self.voice_text())

    def test_no_sample_during_a_conversation(self):
        self.begin()
        self.app.toggle_settings()
        with mock.patch.object(voice, "play_sample") as play:
            self.choose("George")
        play.assert_not_called()
        self.assertIn("End your conversation", self.voice_text())

    def test_starting_a_conversation_stops_a_sample(self):
        self.installed()
        self.app.toggle_settings()
        patcher, process = self.sample_of()
        with patcher:
            self.choose("George")
        self.talk.toggle()
        process.kill.assert_called_once()

    def test_removing_the_add_on_stops_a_sample(self):
        self.installed()
        self.app.toggle_settings()
        patcher, process = self.sample_of()
        with patcher:
            self.choose("George")
        self.app.settings.remove_voice()
        process.kill.assert_called_once()

    def test_the_hear_link_is_only_there_once_installed(self):
        self.app.toggle_settings()
        self.assertFalse(self.app.settings.voice_links.winfo_children())
        self.installed()
        self.app.settings.refresh_talk()
        texts = [link.cget("text") for link in self.app.settings.voice_links.winfo_children()]
        self.assertIn("Hear this voice", texts)


class InstallTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        patcher = mock.patch.dict(os.environ, {"OSSI_HOME": folder.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_not_installed_until_finished(self):
        self.assertFalse(voice.is_installed())

    def test_an_older_add_on_counts_as_not_installed(self):
        voice.folder().mkdir(parents=True)
        voice.marker_path().write_text("0")
        self.assertFalse(voice.is_installed())

    def test_a_bad_download_is_refused(self):
        voice.folder().mkdir(parents=True)
        response = mock.MagicMock()
        response.__enter__.return_value.read.side_effect = [b"not the real file", b""]
        target = voice.folder() / "model.bin"
        with mock.patch("urllib.request.urlopen", return_value=response):
            with self.assertRaises(ValueError):
                voice.download("model.bin", "0" * 64, target)
        self.assertFalse(target.exists())
        self.assertEqual(list(voice.folder().iterdir()), [])

    def test_a_failed_step_leaves_it_not_installed(self):
        with mock.patch.object(voice, "run_logged", side_effect=OSError):
            with self.assertRaises(OSError):
                voice.install(lambda message: None)
        self.assertFalse(voice.is_installed())

    def test_packages_are_pinned(self):
        self.assertTrue(all("==" in package for package in voice.PACKAGES))


if __name__ == "__main__":
    unittest.main()

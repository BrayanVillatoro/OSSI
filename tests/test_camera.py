import base64
import gc
import json
import os
import queue
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ossi import camera, camera_view, camera_worker, gui, ollama, router, storage, voice
from ossi.settings_panel import CAMERA_OFF
from ossi.skills import ai
from ossi.skills import camera as camera_skill

# One white pixel, as a PNG.
PIXEL = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4"
    "AAAAAElFTkSuQmCC"
)
PHOTO = camera.Photo("the picture", PIXEL)
# One white pixel in the format the worker sends for the live view.
VIEW = base64.b64encode(b"P6\n1 1\n255\n\xff\xff\xff").decode()


class Home(unittest.TestCase):
    """Keeps the add-on's folder away from any real one."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        patcher = mock.patch.dict(os.environ, {"OSSI_HOME": folder.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db = storage.connect(":memory:")
        self.addCleanup(self.db.close)

    def installed(self):
        camera.folder().mkdir(parents=True, exist_ok=True)
        camera.marker_path().write_text(camera.VERSION)

    def turned_on(self):
        self.installed()
        camera_skill.turn_on(self.db)


class WordingTest(unittest.TestCase):
    def question_in(self, text):
        found = router.find_loosely(router.clean(text))
        self.assertIsNotNone(found, text)
        self.assertEqual(found[0].usage, "look at this", text)
        return found[1].get("question")

    def test_understands_ways_of_asking(self):
        for text in (
            "what do you see",
            "What can you see right now?",
            "can you see me",
            "do you see this",
            "what am I holding",
            "whats this thing I'm holding",
            "what's in my hand",
            "look at this",
            "please look at me",
            "take a look",
            "read this",
            "can you read this label for me",
            "what does this box say",
            "how do I look",
            "what color is this shirt",
            "what colour is my hair",
            "hey ossi, what am I wearing",
        ):
            self.assertTrue(self.question_in(text), text)

    def test_understands_ways_of_opening_and_closing_it(self):
        for text in ("open the camera", "turn on the webcam", "show me the live view", "start my camera"):
            self.assertEqual(router.find_loosely(router.clean(text))[0].usage, "open the camera", text)
        for text in ("close the camera", "hide the live view", "stop the webcam"):
            self.assertEqual(router.find_loosely(router.clean(text))[0].usage, "close the camera", text)

    def test_close_phrases_are_not_mistaken_for_opening_it(self):
        for text in ("open the door", "close the door", "open the window"):
            db = storage.connect(":memory:")
            self.addCleanup(db.close)
            self.assertNotIn("camera", router.handle(db, text))

    def test_its_question_is_their_own_words(self):
        self.assertEqual(self.question_in("what am I holding?"), "what am I holding")
        self.assertEqual(
            self.question_in("use the camera and tell me what breed this is"),
            "tell me what breed this is",
        )
        self.assertEqual(self.question_in("look at this and read the date"), "read the date")

    def test_opening_the_camera_has_no_question(self):
        self.assertIsNone(self.question_in("use the webcam"))

    def test_leaves_other_messages_alone(self):
        for text in (
            "what do you think",
            "what do you mean",
            "read my notes",
            "what is the capital of france",
            "how do I look up a word",
            "my camera and lens are in the bag",
            "what am I doing today",
        ):
            found = router.find_loosely(router.clean(text))
            self.assertFalse(found and found[0].usage == "look at this", text)

    def test_close_questions_are_not_mistaken_for_it(self):
        for text in ("what do you mean", "what do you say", "what do you think"):
            db = storage.connect(":memory:")
            self.addCleanup(db.close)
            with mock.patch.object(camera, "take_photo") as take:
                reply = router.handle(db, text)
            self.assertNotIn("look at this", reply)
            take.assert_not_called()


class LookTest(Home):
    def look(self, text="what am I holding", session=None):
        session = session or router.Session()
        return router.handle(self.db, text, session), session

    def answers(self, *pieces):
        def fake(model, system, question, picture, on_text=None):
            self.asked = (model, question, picture)
            text = ""
            for piece in pieces:
                text += piece
                if on_text:
                    on_text(text)
            return text

        return mock.patch.object(ollama, "look", fake)

    def test_says_how_to_turn_it_on(self):
        with mock.patch.object(camera, "take_photo") as take:
            reply, _ = self.look()
        self.assertEqual(reply, camera_skill.OFF)
        take.assert_not_called()

    def test_on_without_the_add_on_counts_as_off(self):
        camera_skill.turn_on(self.db)
        self.assertEqual(self.look()[0], camera_skill.OFF)

    def test_turned_off_by_typing(self):
        self.turned_on()
        self.assertEqual(self.look("turn off the camera")[0], camera_skill.TURNED_OFF)
        self.assertFalse(camera_skill.is_on(self.db))

    def test_asks_the_vision_model_about_the_picture(self):
        self.turned_on()
        with mock.patch.object(camera, "take_photo", return_value=PHOTO), self.answers(
            "A red ", "mug."
        ):
            reply, session = self.look()
        self.assertEqual(reply, "A red mug.")
        self.assertEqual(self.asked, (ollama.VISION_MODEL, "what am I holding", "the picture"))
        self.assertEqual(session.thinking, 0)

    def test_the_picture_never_enters_the_conversation(self):
        self.turned_on()
        with mock.patch.object(camera, "take_photo", return_value=PHOTO), self.answers("A mug."):
            _, session = self.look()
        self.assertNotIn("the picture", json.dumps(session.history))
        self.assertEqual(session.history[-1]["content"], "A mug.")

    def test_shows_the_picture_when_it_can(self):
        self.turned_on()
        shown = []
        session = router.Session(show_photo=shown.append)
        with mock.patch.object(camera, "take_photo", return_value=PHOTO), self.answers("A mug."):
            self.look(session=session)
        self.assertEqual(shown, [PIXEL])

    def test_a_camera_problem_is_explained_without_asking_the_model(self):
        self.turned_on()
        with mock.patch.object(
            camera, "take_photo", side_effect=camera.CameraError(camera.NO_CAMERA)
        ), mock.patch.object(ollama, "look") as asked:
            reply, session = self.look()
        self.assertEqual(reply, camera.NO_CAMERA)
        asked.assert_not_called()
        self.assertEqual(session.thinking, 0)

    def test_an_unexpected_camera_failure_gets_an_answer(self):
        self.turned_on()
        with mock.patch.object(camera, "take_photo", side_effect=RuntimeError):
            self.assertEqual(self.look()[0], camera_skill.COULDNT_TAKE)

    def test_says_so_when_the_model_does_not_answer(self):
        self.turned_on()
        with mock.patch.object(camera, "take_photo", return_value=PHOTO), mock.patch.object(
            ollama, "look", side_effect=OSError
        ), mock.patch.object(ollama, "start", return_value=False):
            reply, session = self.look()
        self.assertEqual(reply, camera_skill.NO_ANSWER)
        self.assertEqual(session.thinking, 0)

    def test_an_empty_answer_says_so(self):
        self.turned_on()
        with mock.patch.object(camera, "take_photo", return_value=PHOTO), self.answers("  "):
            self.assertEqual(self.look()[0], camera_skill.COULDNT_SAY)


class SafetyTest(Home):
    def test_the_chat_model_can_not_offer_it(self):
        self.turned_on()
        found = router.find_loosely("what am I holding")
        self.assertFalse(ai.worth_offering(found, ""))
        self.assertNotIn("look at this", ai.build_instructions(self.db))
        self.assertNotIn("look at this", ai.build_talk_instructions(self.db))

    def test_a_chat_model_suggestion_does_not_use_the_camera(self):
        self.turned_on()
        storage.set_setting(self.db, "ai_model", "llama3.2:latest")
        answer = {"command": "what am I holding", "reply": ""}
        with mock.patch.object(ollama, "chat", return_value=answer), mock.patch.object(
            camera, "take_photo"
        ) as take:
            reply = router.handle(self.db, "tell me about the thing")
        take.assert_not_called()
        self.assertNotIn("Did you mean", reply)

    def test_asks_first_when_spoken(self):
        self.turned_on()
        session = router.Session()
        with mock.patch.object(camera, "take_photo", return_value=PHOTO) as take, mock.patch.object(
            ollama, "look", return_value="A mug."
        ):
            reply = router.handle_spoken(self.db, "What am I holding?", session)
            self.assertEqual(reply, "Should I use the camera to take a look?")
            take.assert_not_called()
            self.assertEqual(router.handle_spoken(self.db, "Yes, please", session), "A mug.")
        take.assert_called_once()

    def test_saying_no_keeps_the_camera_off(self):
        self.turned_on()
        session = router.Session()
        with mock.patch.object(camera, "take_photo") as take:
            router.handle_spoken(self.db, "look at this", session)
            router.handle_spoken(self.db, "no", session)
        take.assert_not_called()

    def test_the_vision_model_is_not_offered_for_chat(self):
        with mock.patch.object(
            ollama, "all_models", return_value=[ollama.VISION_MODEL, "llama3.2:latest"]
        ):
            self.assertEqual(ollama.list_models(), ["llama3.2:latest"])


class FakeFrame:
    def __init__(self, brightness=100, shape=(720, 1280, 3)):
        self.brightness = brightness
        self.shape = shape

    def mean(self):
        return self.brightness


class FakeCapture:
    def __init__(self, frame=None, opens=True, reads=True):
        self.frame = frame
        self.opens = opens
        self.reads = reads
        self.released = False

    def isOpened(self):
        return self.opens

    def set(self, setting, value):
        pass

    def read(self):
        return (True, self.frame) if self.reads else (False, None)

    def release(self):
        self.released = True


class FakeCv2:
    CAP_DSHOW = 700
    CAP_MSMF = 1400
    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    IMWRITE_JPEG_QUALITY = 1
    INTER_AREA = 3

    def __init__(self, captures):
        self.captures = captures
        self.opened = []

    def VideoCapture(self, index, backend):
        self.opened.append((index, backend))
        return self.captures.get((index, backend), FakeCapture(opens=False))


class FakeLive:
    """The camera add-on's worker, already sending a view."""

    created = []

    def __init__(self):
        self.latest = VIEW
        self.problem = None
        self.closed = False
        self.taken = 0
        FakeLive.created.append(self)

    def take_photo(self):
        self.taken += 1
        return PHOTO

    def close(self):
        self.closed = True


class StreamTest(unittest.TestCase):
    class Cv2(FakeCv2):
        def __init__(self):
            super().__init__({})

        def imencode(self, extension, frame, *options):
            class Data:
                def tobytes(self):
                    return extension.encode()

            return True, Data()

        def resize(self, frame, size, interpolation):
            return FakeFrame(frame.brightness, (size[1], size[0], 3))

        def flip(self, frame, direction):
            return frame

    def setUp(self):
        for name, value in (("START_SECONDS", 0), ("FAILED_READ_PAUSE", 0)):
            patcher = mock.patch.object(camera_worker, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def stream(self, capture, stop_after, take=False):
        commands = camera_worker.Commands([])
        if take:
            commands.take.set()
        sent = []

        def send(message):
            sent.append(message)
            if len(sent) >= stop_after:
                commands.stop.set()

        camera_worker.stream(self.Cv2(), capture, commands, send)
        return sent

    def test_sends_the_view(self):
        sent = self.stream(FakeCapture(FakeFrame()), 1)
        self.assertEqual(sent[0]["type"], "view")

    def test_a_requested_picture_comes_with_both_sizes(self):
        sent = self.stream(FakeCapture(FakeFrame()), 2, take=True)
        self.assertEqual(sent[0]["type"], "picture")
        self.assertIn("picture", sent[0])
        self.assertIn("thumbnail", sent[0])

    def test_a_dark_picture_says_so_without_ending_the_view(self):
        sent = self.stream(FakeCapture(FakeFrame(0)), 2, take=True)
        self.assertEqual(sent[0], {"type": "picture", "error": "too_dark"})
        self.assertEqual(sent[1]["type"], "view")

    def test_reports_a_camera_that_stops(self):
        sent = self.stream(FakeCapture(reads=False), 99)
        self.assertEqual(sent, [{"type": "error", "error": "gone"}])

    def test_opens_the_first_camera_sending_frames_and_keeps_it_open(self):
        silent = FakeCapture(reads=False)
        working = FakeCapture(FakeFrame())
        cv2 = FakeCv2({(0, 700): silent, (1, 700): working})
        with mock.patch.object(camera_worker, "START_SECONDS", 0.01):
            self.assertIs(camera_worker.open_first_camera(cv2), working)
        self.assertTrue(silent.released)
        self.assertFalse(working.released)

    def test_finds_nothing_with_no_camera(self):
        self.assertIsNone(camera_worker.open_first_camera(FakeCv2({})))

    def test_closing_stdin_stops_it(self):
        commands = camera_worker.Commands(['{"type": "take"}\n'])
        commands.read()
        self.assertTrue(commands.take.is_set())
        self.assertTrue(commands.stop.is_set())


class LiveCameraTest(Home):
    def live(self, *messages):
        self.installed()
        lines = [json.dumps(message) + "\n" for message in messages]
        process = mock.MagicMock(stdout=lines)
        with mock.patch.object(subprocess, "Popen", return_value=process):
            live = camera.LiveCamera()
        self.addCleanup(live.log.close)
        deadline = time.time() + 5
        while live.problem is None and time.time() < deadline:
            time.sleep(0.005)
        return live, process

    def test_keeps_the_newest_view(self):
        live, _ = self.live({"type": "view", "view": "one"}, {"type": "view", "view": "two"})
        self.assertEqual(live.latest, "two")

    def test_says_when_the_worker_stops(self):
        self.assertEqual(self.live()[0].problem, "gone")
        self.assertEqual(self.live({"type": "error", "error": "no_camera"})[0].problem, "no_camera")

    def test_takes_a_picture(self):
        live, process = self.live({"type": "picture", "picture": "p", "thumbnail": "t"})
        photo = live.take_photo()
        self.assertEqual((photo.picture, photo.thumbnail), ("p", "t"))
        self.assertIn("take", process.stdin.write.call_args.args[0])

    def test_a_dark_picture_is_explained(self):
        live, _ = self.live({"type": "picture", "error": "too_dark"})
        with self.assertRaisesRegex(camera.CameraError, "dark"):
            live.take_photo()

    def test_a_stopped_worker_is_explained(self):
        with self.assertRaisesRegex(camera.CameraError, "stopped working"):
            self.live()[0].take_photo()

    def test_closing_closes_stdin_which_stops_the_worker(self):
        live, process = self.live()
        process.wait.side_effect = lambda timeout: None
        live.close()
        process.stdin.close.assert_called_once()


class SpokenTest(Home):
    def setUp(self):
        super().setUp()
        self.turned_on()
        self.session = router.Session(open_camera=self.open_view)
        self.live = FakeLive()

    def open_view(self):
        self.session.live_camera = self.live
        self.live.close = lambda: setattr(self.session, "live_camera", None)

    def say(self, text):
        with mock.patch.object(ollama, "look", return_value="A mug."):
            return router.handle_spoken(self.db, text, self.session)

    def test_opening_it_asks_first(self):
        self.assertEqual(self.say("open the camera"), "Should I open the camera?")
        self.assertIsNone(self.session.live_camera)
        self.assertEqual(self.say("yes"), camera_skill.OPENED)
        self.assertIs(self.session.live_camera, self.live)

    def test_saying_no_keeps_it_closed(self):
        self.say("open the camera")
        self.say("no")
        self.assertIsNone(self.session.live_camera)

    def test_with_the_view_open_it_does_not_ask(self):
        self.open_view()
        self.assertEqual(self.say("what am I holding"), "A mug.")
        self.assertEqual(self.live.taken, 1)

    def test_with_the_view_open_any_question_about_it_uses_the_camera(self):
        self.open_view()
        self.assertEqual(self.say("what brand is it"), "A mug.")
        self.assertEqual(self.live.taken, 1)

    def test_with_the_view_open_small_talk_stays_with_the_chat_ai(self):
        self.open_view()
        storage.set_setting(self.db, "ai_model", "llama3.2:latest")
        with mock.patch.object(ollama, "talk", return_value="Pretty good!") as talk:
            self.assertEqual(self.say("how was your day"), "Pretty good!")
        talk.assert_called_once()
        self.assertEqual(self.live.taken, 0)

    def test_closing_it_does_not_ask(self):
        self.open_view()
        self.assertEqual(self.say("close the camera"), camera_skill.CLOSED)
        self.assertIsNone(self.session.live_camera)

    def test_turning_it_off_closes_the_view_but_keeps_the_feature(self):
        self.open_view()
        self.assertEqual(self.say("turn off the camera"), camera_skill.CLOSED)
        self.assertIsNone(self.session.live_camera)
        self.assertTrue(camera_skill.is_on(self.db))

    def test_turning_it_off_with_no_view_turns_off_the_feature(self):
        self.assertEqual(self.say("turn off the camera"), camera_skill.TURNED_OFF)
        self.assertFalse(camera_skill.is_on(self.db))

    def test_while_off_says_so_without_asking(self):
        camera_skill.turn_off(self.db)
        self.assertEqual(self.say("what am I holding"), camera_skill.OFF)
        self.assertEqual(self.say("open the camera"), camera_skill.OFF)

    def test_the_chat_model_can_not_open_it(self):
        self.assertFalse(ai.worth_offering(router.find_loosely("open the camera"), ""))
        for usage in ("open the camera", "close the camera", "turn off the camera"):
            self.assertIn(usage, ai.OFF_LIMITS)

    def test_a_terminal_with_no_window_takes_one_picture(self):
        session = router.Session()
        with mock.patch.object(camera, "take_photo", return_value=PHOTO), mock.patch.object(
            ollama, "look", return_value="A mug."
        ):
            self.assertEqual(router.handle(self.db, "open the camera", session), "A mug.")


class WorkerTest(unittest.TestCase):
    def setUp(self):
        for name, value in (("SETTLE_SECONDS", 0), ("START_SECONDS", 0), ("FAILED_READ_PAUSE", 0)):
            patcher = mock.patch.object(camera_worker, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_no_camera_finds_nothing(self):
        cv2 = FakeCv2({})
        self.assertIsNone(camera_worker.take_picture(cv2))
        self.assertEqual(len(cv2.opened), 2 * camera_worker.CAMERAS_TO_TRY)

    def test_uses_the_first_working_camera(self):
        frame = FakeFrame()
        capture = FakeCapture(frame)
        cv2 = FakeCv2({(0, 700): capture})
        self.assertIs(camera_worker.take_picture(cv2), frame)
        self.assertEqual(cv2.opened, [(0, 700)])
        self.assertTrue(capture.released)

    def test_skips_a_camera_that_sends_nothing(self):
        frame = FakeFrame()
        silent = FakeCapture(reads=False)
        cv2 = FakeCv2({(0, 700): silent, (1, 700): FakeCapture(frame)})
        self.assertIs(camera_worker.take_picture(cv2), frame)
        self.assertTrue(silent.released)

    def test_falls_back_to_the_other_way_of_reaching_it(self):
        frame = FakeFrame()
        self.assertIs(camera_worker.take_picture(FakeCv2({(0, 1400): FakeCapture(frame)})), frame)

    def test_prefers_a_good_picture_over_a_dark_one(self):
        good = FakeFrame()
        cv2 = FakeCv2({(0, 700): FakeCapture(FakeFrame(0)), (1, 700): FakeCapture(good)})
        self.assertIs(camera_worker.take_picture(cv2), good)

    def test_reports_problems_by_name(self):
        self.assertEqual(camera_worker.answer_for(None, None), {"error": "no_camera"})
        self.assertEqual(camera_worker.answer_for(None, FakeFrame(1)), {"error": "too_dark"})
        for name in ("no_camera", "too_dark"):
            self.assertIn(name, camera.PROBLEMS)


class TakePhotoTest(Home):
    def setUp(self):
        super().setUp()
        self.installed()

    def worker_says(self, stdout):
        return mock.patch.object(
            subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout)
        )

    def test_returns_the_picture(self):
        with self.worker_says(json.dumps({"picture": "p", "thumbnail": "t"})) as run:
            photo = camera.take_photo()
        self.assertEqual((photo.picture, photo.thumbnail), ("p", "t"))
        self.assertIn("-I", run.call_args.args[0])

    def test_explains_each_problem(self):
        for name, message in camera.PROBLEMS.items():
            with self.worker_says(json.dumps({"error": name})):
                with self.assertRaises(camera.CameraError) as caught:
                    camera.take_photo()
            self.assertEqual(str(caught.exception), message)

    def test_a_crashed_worker_is_explained(self):
        with self.worker_says(""):
            with self.assertRaisesRegex(camera.CameraError, "isn't working"):
                camera.take_photo()

    def test_a_stuck_camera_is_explained(self):
        stuck = subprocess.TimeoutExpired([], 1)
        with mock.patch.object(subprocess, "run", side_effect=stuck):
            with self.assertRaisesRegex(camera.CameraError, "didn't respond"):
                camera.take_photo()

    def test_a_missing_add_on_is_explained(self):
        with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(camera.CameraError, "isn't working"):
                camera.take_photo()


class InstallTest(Home):
    def install(self, models):
        messages = []
        with mock.patch.object(camera, "run_logged") as run, mock.patch.object(
            ollama, "all_models", return_value=models
        ), mock.patch.object(ollama, "pull") as pull:
            pull.side_effect = lambda model, report: report(2 * camera.BYTES_PER_GB)
            camera.install(messages.append)
        return messages, run, pull

    def test_downloads_the_model_and_finishes(self):
        messages, run, pull = self.install(["llama3.2:latest"])
        self.assertTrue(camera.is_installed())
        self.assertEqual(pull.call_args.args[0], ollama.VISION_MODEL)
        self.assertTrue(any("2.0 of about" in message for message in messages))
        pinned = run.call_args_list[1].args[0]
        self.assertTrue(all("==" in package for package in camera.PACKAGES))
        self.assertIn(camera.PACKAGES[0], pinned)

    def test_keeps_a_model_already_there_without_downloading(self):
        _, _, pull = self.install([ollama.VISION_MODEL])
        pull.assert_not_called()
        self.assertFalse(camera.model_is_ours_path().exists())

    def test_a_failed_install_is_not_marked_as_installed(self):
        with mock.patch.object(camera, "run_logged"), mock.patch.object(
            ollama, "all_models", return_value=[]
        ), mock.patch.object(ollama, "pull", side_effect=ValueError("no space")):
            with self.assertRaisesRegex(ValueError, "no space"):
                camera.install(lambda message: None)
        self.assertFalse(camera.is_installed())

    def test_removing_deletes_only_a_model_ossi_downloaded(self):
        self.install([])
        with mock.patch.object(ollama, "delete_model") as delete:
            self.assertTrue(camera.remove())
        delete.assert_called_once_with(ollama.VISION_MODEL)
        self.assertFalse(camera.folder().exists())

        self.install([ollama.VISION_MODEL])
        with mock.patch.object(ollama, "delete_model") as delete:
            self.assertTrue(camera.remove())
        delete.assert_not_called()

    def test_removing_says_if_the_model_stayed(self):
        self.install([])
        with mock.patch.object(ollama, "delete_model", side_effect=OSError), mock.patch.object(
            ollama, "start", return_value=False
        ):
            self.assertFalse(camera.remove())
        self.assertFalse(camera.folder().exists())


class PullTest(unittest.TestCase):
    def pull_with(self, *pieces):
        lines = [json.dumps(piece).encode() + b"\n" for piece in pieces]
        response = mock.MagicMock()
        response.__enter__.return_value = lines
        reported = []
        with mock.patch.object(ollama.OPENER, "open", return_value=response):
            ollama.pull("a model", reported.append)
        return reported

    def test_adds_up_each_part(self):
        reported = self.pull_with(
            {"status": "pulling", "digest": "a", "total": 10, "completed": 4},
            {"status": "pulling", "digest": "a", "total": 10, "completed": 10},
            {"status": "pulling", "digest": "b", "total": 5, "completed": 2},
            {"status": "success"},
        )
        self.assertEqual(reported, [4, 10, 12])

    def test_an_error_stops_it(self):
        with self.assertRaisesRegex(ValueError, "disk full"):
            self.pull_with({"error": "disk full"})

    def test_ending_early_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "stopped early"):
            self.pull_with({"status": "pulling", "digest": "a", "completed": 1})


class WindowTest(Home):
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
        patcher = mock.patch.object(camera, "LiveCamera", FakeLive)
        patcher.start()
        self.addCleanup(patcher.stop)
        FakeLive.created = []
        self.app = gui.App(self.root, self.db, notify=lambda title, body: None)
        self.addCleanup(self.app.camera_view.close)
        self.panel = self.app.settings

    def poll(self):
        if self.app.camera_view.timer is not None:
            self.root.after_cancel(self.app.camera_view.timer)
        self.app.camera_view.poll()

    def test_the_button_goes_to_settings_until_it_is_set_up(self):
        self.app.camera_view.toggle()
        self.assertTrue(self.panel.visible)
        self.assertEqual(FakeLive.created, [])

    def test_the_button_opens_and_closes_the_view(self):
        self.turned_on()
        self.app.camera_view.toggle()
        live = FakeLive.created[-1]
        self.assertIs(self.app.session.live_camera, self.app.camera_view)
        self.assertEqual(self.app.camera_strip.grid_info()["row"], 1)
        self.app.camera_view.toggle()
        self.assertTrue(live.closed)
        self.assertIsNone(self.app.session.live_camera)
        self.assertEqual(self.app.camera_strip.grid_info(), {})

    def test_shows_each_new_view(self):
        self.turned_on()
        self.app.camera_view.open()
        self.poll()
        self.assertEqual(self.app.camera_picture.width(), 1)
        self.assertEqual(self.app.camera_label.cget("text"), camera_view.HINT)

    def test_a_typed_question_uses_the_live_view(self):
        self.turned_on()
        self.app.camera_view.open()
        live = FakeLive.created[-1]
        with mock.patch.object(ollama, "look", return_value="A red mug.") as look, mock.patch.object(
            camera, "take_photo"
        ) as one_shot:
            self.app.entry.insert(0, "what brand is it")
            self.app.send()
            self.wait(lambda: "A red mug." in self.log())
        self.assertEqual(live.taken, 1)
        one_shot.assert_not_called()
        self.assertEqual(look.call_args.args[2], "what brand is it")

    def test_enter_with_nothing_typed_describes_the_view(self):
        self.turned_on()
        self.app.camera_view.open()
        with mock.patch.object(ollama, "look", return_value="A desk.") as look:
            self.app.send()
            self.wait(lambda: "A desk." in self.log())
        self.assertEqual(look.call_args.args[2], "What do you see")

    def test_enter_with_nothing_typed_does_nothing_with_the_view_closed(self):
        self.app.send()
        self.assertNotIn("What do you see", self.log())

    def test_ordinary_commands_still_work_with_the_view_open(self):
        self.turned_on()
        self.app.camera_view.open()
        with mock.patch.object(ollama, "look") as look:
            self.app.run("add task buy milk")
        look.assert_not_called()
        self.assertEqual(FakeLive.created[-1].taken, 0)
        self.assertIn("buy milk", self.log())

    def test_the_view_says_why_when_the_camera_fails(self):
        self.turned_on()
        self.app.camera_view.open()
        live = FakeLive.created[-1]
        live.problem = "no_camera"
        self.poll()
        self.assertIn(camera.NO_CAMERA, self.log())
        self.assertTrue(live.closed)
        self.assertIsNone(self.app.session.live_camera)

    def test_closes_itself_after_a_while_without_a_question(self):
        self.turned_on()
        self.app.camera_view.open()
        self.app.camera_view.last_used -= camera_view.IDLE_SECONDS
        self.poll()
        self.assertTrue(FakeLive.created[-1].closed)
        self.assertIn(camera_view.CLOSED_WHEN_IDLE, self.log())

    def test_asking_a_question_keeps_it_open(self):
        self.turned_on()
        self.app.camera_view.open()
        self.app.camera_view.last_used -= camera_view.IDLE_SECONDS
        self.app.camera_view.take_photo()
        self.poll()
        self.assertFalse(FakeLive.created[-1].closed)

    def test_closes_when_the_window_hides(self):
        self.turned_on()
        self.app.camera_view.open()
        self.app.hide()
        self.assertTrue(FakeLive.created[-1].closed)

    def test_closes_when_settings_opens(self):
        self.turned_on()
        self.app.camera_view.open()
        self.app.toggle_settings()
        self.assertTrue(FakeLive.created[-1].closed)

    def test_closes_when_turned_off_by_typing(self):
        self.turned_on()
        self.app.camera_view.open()
        self.app.run("close the camera")
        self.assertTrue(FakeLive.created[-1].closed)
        self.assertIn(camera_skill.CLOSED, self.log())

    def test_opens_by_typing(self):
        self.turned_on()
        self.app.run("open the camera")
        self.assertIs(self.app.session.live_camera, self.app.camera_view)
        self.assertIn(camera_skill.OPENED, self.log())

    def test_a_broken_add_on_is_explained(self):
        self.turned_on()
        with mock.patch.object(camera, "LiveCamera", side_effect=OSError):
            self.app.camera_view.open()
        self.assertIn(camera_view.COULDNT_OPEN, self.log())
        self.assertIsNone(self.app.session.live_camera)

    def test_a_whole_spoken_conversation_with_the_camera(self):
        class FakeVoice:
            def __init__(self, voice_name):
                self.events = queue.Queue()
                self.said = []

            def send(self, **message):
                if message["type"] == "say":
                    self.said.append(message["text"])

            def close(self):
                pass

        self.turned_on()
        storage.set_setting(self.db, "ai_model", "llama3.2:latest")
        voice.folder().mkdir(parents=True, exist_ok=True)
        voice.marker_path().write_text(voice.VERSION)
        talk = self.app.talk
        self.addCleanup(self.app.glow.stop)
        with mock.patch.object(voice, "VoiceProcess", FakeVoice), mock.patch.object(ollama, "warm"):
            talk.toggle()
        self.addCleanup(lambda: talk.timer is not None and self.root.after_cancel(talk.timer))
        spoken = talk.process.said

        def hear(text):
            talk.handle({"type": "heard", "text": text})

        hear("Open the camera.")
        self.assertEqual(spoken[-1], "Should I open the camera?")
        self.assertIsNone(self.app.session.live_camera)
        hear("Yes.")
        self.assertTrue(" ".join(spoken).endswith(camera_skill.OPENED))  # Read a sentence at a time.
        live = FakeLive.created[-1]

        with mock.patch.object(ollama, "look", return_value="It's a red mug. It looks new."):
            hear("What am I holding?")
            self.assertEqual(spoken[-1], camera_skill.TAKING)
            self.wait(lambda: "It looks new." in " ".join(spoken))
        self.assertEqual(live.taken, 1)
        self.assertEqual(spoken[-2:], ["It's a red mug.", "It looks new."])
        self.assertEqual(self.app.talk_label.cget("text"), "Thinking...")

        hear("Close the camera.")
        self.assertEqual(spoken[-1], camera_skill.CLOSED)
        self.assertTrue(live.closed)
        talk.end()

    def test_the_button_follows_the_accent_color(self):
        self.turned_on()
        self.app.camera_view.open()
        self.app.set_accent("Blue")
        self.assertEqual(self.app.camera_button.cget("image"), str(self.app.talk_on_image))

    def log(self):
        return self.app.log.get("1.0", "end")

    def wait(self, until):
        deadline = time.time() + 5
        while not until() and time.time() < deadline:
            self.app.check_fetches()
            self.root.update()
            time.sleep(0.005)
        self.assertTrue(until())

    def status(self):
        return self.panel.camera_status.cget("text")

    def links(self):
        return [child.cget("text") for child in self.panel.camera_links.winfo_children()]

    def test_shows_the_picture_then_the_answer(self):
        self.turned_on()
        with mock.patch.object(camera, "take_photo", return_value=PHOTO), mock.patch.object(
            ollama, "look", return_value="A red mug."
        ):
            self.app.run("what am I holding")
            self.assertIn(camera_skill.TAKING, self.log())
            self.wait(lambda: "A red mug." in self.log())
        self.assertEqual(len(self.app.photos), 1)
        self.assertEqual(len(self.app.log.image_names()), 1)
        self.assertNotIn(camera_skill.LOOKING, self.log())
        self.assertEqual(self.app.session.thinking, 0)

    def test_says_what_it_is_doing_until_words_arrive(self):
        self.turned_on()
        release = []

        def slow(model, system, question, picture, on_text=None):
            while not release:
                time.sleep(0.005)
            return "A mug."

        with mock.patch.object(camera, "take_photo", return_value=PHOTO), mock.patch.object(
            ollama, "look", slow
        ):
            self.app.run("what am I holding")
            self.wait(lambda: camera_skill.LOOKING in self.log())
            release.append(True)
            self.wait(lambda: "A mug." in self.log())

    def test_never_mind_drops_the_answer(self):
        self.turned_on()
        release = []

        def slow(model, system, question, picture, on_text=None):
            while not release:
                time.sleep(0.005)
            return "A mug."

        with mock.patch.object(camera, "take_photo", return_value=PHOTO), mock.patch.object(
            ollama, "look", slow
        ):
            self.app.run("what am I holding")
            self.wait(lambda: camera_skill.LOOKING in self.log())
            self.app.run("never mind")
            release.append(True)
            self.wait(lambda: self.app.session.thinking == 0)
        self.assertNotIn("A mug.", self.log())
        self.assertNotIn(camera_skill.LOOKING, self.log())

    def test_never_mind_while_the_camera_is_on_skips_the_model(self):
        self.turned_on()
        release = []

        def slow():
            while not release:
                time.sleep(0.005)
            return PHOTO

        with mock.patch.object(camera, "take_photo", slow), mock.patch.object(
            ollama, "look"
        ) as asked:
            self.app.run("what am I holding")
            self.app.run("never mind")
            release.append(True)
            self.wait(lambda: self.app.session.thinking == 0)
        asked.assert_not_called()
        self.assertEqual(self.app.photos, [])

    def test_settings_starts_off_and_explains_itself(self):
        self.panel.refresh_camera()
        self.assertFalse(self.panel.camera_switch.on)
        self.assertEqual(self.status(), CAMERA_OFF)
        self.assertEqual(self.links(), [])

    def test_turning_on_without_ollama_explains_what_to_do(self):
        with mock.patch.object(ollama, "is_installed", return_value=False):
            self.panel.camera_switch.flip()
        self.assertFalse(self.panel.camera_switch.on)
        self.assertIn("Ollama", self.status())
        self.assertEqual(self.links(), ["Download Ollama"])

    def test_declining_the_download_leaves_it_off(self):
        with mock.patch.object(ollama, "is_installed", return_value=True), mock.patch(
            "ossi.settings_panel.messagebox.askyesno", return_value=False
        ), mock.patch.object(camera, "install") as install:
            self.panel.camera_switch.flip()
        install.assert_not_called()
        self.assertFalse(self.panel.camera_switch.on)

    def test_setting_up_turns_it_on_and_says_so_in_the_chat(self):
        def fake_install(progress):
            progress("Halfway there")
            self.installed()

        with mock.patch.object(ollama, "is_installed", return_value=True), mock.patch(
            "ossi.settings_panel.messagebox.askyesno", return_value=True
        ), mock.patch.object(camera, "install", fake_install):
            self.panel.camera_switch.flip()
            self.assertTrue(self.panel.camera_installing)
            self.wait(lambda: not self.panel.camera_installing)
        self.assertTrue(camera_skill.is_on(self.db))
        self.assertIn(camera_skill.READY, self.log())
        self.assertEqual(self.links(), ["Remove the camera add-on"])

    def test_a_failed_setup_says_why_and_can_be_retried(self):
        with mock.patch.object(ollama, "is_installed", return_value=True), mock.patch(
            "ossi.settings_panel.messagebox.askyesno", return_value=True
        ), mock.patch.object(camera, "install", side_effect=ValueError("no space")):
            self.panel.camera_switch.flip()
            self.wait(lambda: not self.panel.camera_installing)
        self.assertFalse(self.panel.camera_switch.on)
        self.assertIn("no space", self.status())
        self.assertEqual(self.links(), ["Try again"])
        self.assertFalse(camera_skill.is_on(self.db))

    def test_turning_it_off_keeps_the_add_on(self):
        self.turned_on()
        self.panel.refresh_camera()
        self.assertTrue(self.panel.camera_switch.on)
        self.panel.camera_switch.flip()
        self.assertFalse(camera_skill.is_on(self.db))
        self.assertTrue(camera.is_installed())
        with mock.patch.object(camera, "install") as install:
            self.panel.camera_switch.flip()
        install.assert_not_called()
        self.assertTrue(camera_skill.is_on(self.db))

    def test_removing_the_add_on_asks_first(self):
        self.turned_on()
        with mock.patch(
            "ossi.settings_panel.messagebox.askyesno", return_value=False
        ), mock.patch.object(camera, "remove") as remove:
            self.panel.remove_camera()
        remove.assert_not_called()
        self.assertTrue(camera_skill.is_on(self.db))

        with mock.patch(
            "ossi.settings_panel.messagebox.askyesno", return_value=True
        ), mock.patch.object(ollama, "delete_model"):
            self.panel.remove_camera()
            self.wait(lambda: self.status() != "Removing it...")
        self.assertFalse(camera.is_installed())
        self.assertFalse(camera_skill.is_on(self.db))
        self.assertEqual(self.links(), [])


if __name__ == "__main__":
    unittest.main()

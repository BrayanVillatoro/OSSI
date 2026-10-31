import contextlib
import gc
import http.server
import io
import json
import sys
import threading
import time
import tkinter as tk
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ossi import cli, gui, ollama, router, storage
from ossi.skills import ai


def model_says(command="", reply=""):
    return mock.patch.object(
        ollama, "chat", return_value={"command": command, "reply": reply}
    )


def ollama_is_down():
    return mock.patch.object(ollama, "chat", side_effect=OSError("connection refused"))


def models_are(*names):
    return mock.patch.object(ollama, "list_models", return_value=list(names))


class Base(unittest.TestCase):
    def setUp(self):
        self.db = storage.connect(":memory:")
        self.addCleanup(self.db.close)
        self.session = router.Session()

    def say(self, text):
        return router.handle(self.db, text, self.session)

    def turn_on(self):
        storage.set_setting(self.db, "ai_model", "llama3.2:latest")


class OffByDefaultTest(Base):
    def test_never_contacts_ollama_until_turned_on(self):
        with mock.patch.object(ollama, "OPENER") as opener:
            self.assertEqual(self.say("how tall is the moon"), router.FALLBACK)
        opener.open.assert_not_called()

    def test_turning_it_off_stops_it(self):
        self.turn_on()
        self.assertIn("off", self.say("turn off ai"))
        with mock.patch.object(ollama, "chat") as chat:
            self.assertEqual(self.say("how tall is the moon"), router.FALLBACK)
        chat.assert_not_called()


class WhatReachesTheModelTest(Base):
    def setUp(self):
        super().setUp()
        self.turn_on()

    def test_commands_and_typos_are_handled_without_it(self):
        with mock.patch.object(ollama, "chat") as chat:
            self.assertEqual(self.say("add task buy milk"), "Added task 1: buy milk")
            self.assertIn("Did you mean", self.say("add taks call the bank"))
        chat.assert_not_called()

    def test_a_loose_sentence_goes_to_the_model(self):
        with model_says(reply="hi") as chat:
            self.say("I need to call the bank sometime tomorrow")
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(chat.call_args[0][2], "I need to call the bank sometime tomorrow")


class SuggestionTest(Base):
    def setUp(self):
        super().setUp()
        self.turn_on()

    def test_a_suggestion_changes_nothing_until_confirmed(self):
        with model_says(command="add task call the bank"):
            reply = self.say("I should probably ring the bank")
        self.assertEqual(reply, "Did you mean: add task call the bank?")
        self.assertEqual(self.say("tasks"), "No open tasks.")

    def test_yes_runs_it(self):
        with model_says(command="add task call the bank"):
            self.say("I should probably ring the bank")
        self.assertEqual(self.say("yes"), "Added task 1: call the bank")

    def test_no_drops_it(self):
        with model_says(command="add task call the bank"):
            self.say("I should probably ring the bank")
        self.assertEqual(self.say("no"), router.NEVER_MIND)
        self.assertEqual(self.say("tasks"), "No open tasks.")

    def test_a_command_ossi_does_not_have_is_never_offered(self):
        with model_says(command="delete everything", reply="I can't do that."):
            self.assertEqual(self.say("wipe it all out"), "I can't do that.")

    def test_a_made_up_command_with_no_reply_gets_the_usual_message(self):
        with model_says(command="delete everything"):
            self.assertEqual(self.say("wipe it all out"), router.FALLBACK)

    def test_settings_are_off_limits(self):
        for command in ("start at login", "forget my location", "turn off ai"):
            with model_says(command=command):
                self.assertEqual(self.say("do the thing"), router.FALLBACK, command)
            self.assertIsNone(self.session.pending)

    def test_an_answer_in_words(self):
        with model_says(reply="Start with the bank."):
            self.assertEqual(self.say("which task matters most"), "Start with the bank.")

    def test_odd_answers_do_not_crash(self):
        for answer in ({}, {"command": None, "reply": None}, {"command": 5, "reply": ""}):
            with mock.patch.object(ollama, "chat", return_value=answer):
                self.assertEqual(self.say("do the thing"), router.FALLBACK, answer)

    def test_ollama_being_down_says_so(self):
        with ollama_is_down():
            reply = self.say("do the thing")
        self.assertIn("Ollama didn't answer", reply)
        self.assertIn("don't know how", reply)

    def test_a_garbled_answer_is_treated_as_no_answer(self):
        with mock.patch.object(ollama, "chat", side_effect=json.JSONDecodeError("bad", "", 0)):
            self.assertIn("didn't answer", self.say("do the thing"))


class MemoryTest(Base):
    def setUp(self):
        super().setUp()
        self.turn_on()

    def history_sent(self, chat):
        return chat.call_args[0][3]

    def test_the_model_sees_the_conversation_so_far(self):
        with model_says(reply="Good idea.") as chat:
            self.say("add task call the bank")
            self.say("I keep putting that off")
            self.say("when should I do it")
        self.assertEqual(
            self.history_sent(chat),
            [
                {"role": "user", "content": "add task call the bank"},
                {"role": "assistant", "content": "Added task 1: call the bank"},
                {"role": "user", "content": "I keep putting that off"},
                {"role": "assistant", "content": "Good idea."},
            ],
        )
        self.assertEqual(chat.call_args[0][2], "when should I do it")

    def test_a_suggestion_and_its_outcome_are_remembered_once(self):
        with model_says(command="add task call the bank") as chat:
            self.say("I should ring the bank")
            self.say("yes")
            self.say("do that again tomorrow")
        contents = [entry["content"] for entry in self.history_sent(chat)]
        self.assertIn("Did you mean: add task call the bank?", contents)
        self.assertEqual(contents.count("Added task 1: call the bank"), 1)

    def test_memory_is_limited(self):
        for number in range(30):
            self.say(f"note {number} " + "x" * 2000)
        self.assertEqual(len(self.session.history), router.MOST_REMEMBERED_MESSAGES)
        longest = max(len(entry["content"]) for entry in self.session.history)
        self.assertEqual(longest, router.MOST_REMEMBERED_CHARACTERS)

    def test_a_new_session_starts_fresh(self):
        self.say("add task call the bank")
        with model_says(reply="ok") as chat:
            router.handle(self.db, "what did I say")
        self.assertEqual(self.history_sent(chat), [])


class ClientHistoryOrderTest(unittest.TestCase):
    def test_history_goes_between_the_instructions_and_the_new_message(self):
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(
            json.dumps({"message": {"content": '{"command":"","reply":"ok"}'}}).encode()
        )
        earlier = [{"role": "user", "content": "before"}]
        with mock.patch.object(ollama.OPENER, "open", return_value=response) as opener:
            ollama.chat("m", "rules", "now", earlier)
        sent = json.loads(opener.call_args[0][0].data)["messages"]
        self.assertEqual([m["content"] for m in sent], ["rules", "before", "now"])


class SlowAnswerTest(Base):
    """A window session, where answers arrive after other messages."""

    def setUp(self):
        super().setUp()
        self.turn_on()
        self.waiting = []
        self.session.defer = self.waiting.append

    def arrive(self, command="", reply=""):
        with model_says(command, reply):
            fetch = self.waiting.pop(0)
            result = fetch.finish(self.db, fetch.get_value(), self.session)
        return router.render(self.db, result, self.session)

    def test_an_answer_on_time_has_no_label(self):
        self.say("what should I do first")
        self.assertEqual(self.arrive(reply="The bank."), "The bank.")

    def test_a_late_answer_says_which_message_it_belongs_to(self):
        self.say("what should I do first")
        self.say("add task buy milk")
        self.assertEqual(
            self.arrive(reply="The bank."), 'About "what should I do first": The bank.'
        )

    def test_a_late_suggestion_can_still_be_accepted(self):
        self.say("I should ring the bank")
        self.say("add task buy milk")
        self.assertEqual(
            self.arrive(command="add task call the bank"),
            'About "I should ring the bank": Did you mean: add task call the bank?',
        )
        self.assertEqual(self.say("yes"), "Added task 2: call the bank")

    def test_a_late_suggestion_does_not_replace_a_question_being_answered(self):
        self.say("I should ring the bank")
        self.assertIn("When should I", self.say("remind me to pay rent"))
        self.assertEqual(
            self.arrive(command="add task call the bank"),
            'About "I should ring the bank": You could type: add task call the bank',
        )
        self.assertIn("Reminder 1 set", self.say("in 2 hours"))

    def test_never_mind_stops_the_wait(self):
        self.say("what should I do first")
        self.assertEqual(self.say("never mind"), router.NEVER_MIND)
        self.assertIsNone(self.arrive(reply="The bank."))
        self.assertEqual(self.session.thinking, 0)

    def test_never_mind_only_stops_answers_already_on_the_way(self):
        self.say("first question")
        self.say("never mind")
        self.say("second question")
        self.assertIsNone(self.arrive(reply="one"))
        self.assertEqual(self.arrive(reply="two"), "two")

    def test_turning_off_the_ai_drops_answers_on_the_way(self):
        self.say("what should I do first")
        self.say("turn off ai")
        self.assertIsNone(self.arrive(reply="The bank."))

    def test_cancel_still_means_a_reminder_while_waiting(self):
        self.say("what should I do first")
        self.assertEqual(self.say("cancel"), "No upcoming reminders.")

    def test_never_mind_with_nothing_happening_goes_to_the_model_as_usual(self):
        self.say("never mind")
        self.assertEqual(len(self.waiting), 1)


class TerminalTest(Base):
    def run_cli(self, argv, answers, is_terminal=True):
        printed = io.StringIO()
        with mock.patch.object(storage, "connect", return_value=_Unclosed(self.db)), \
                mock.patch("sys.stdin", mock.Mock(isatty=lambda: is_terminal)), \
                mock.patch("builtins.input", side_effect=answers) as asked, \
                contextlib.redirect_stdout(printed):
            cli.main(argv)
        return printed.getvalue(), asked

    def test_a_typo_can_be_confirmed_on_the_spot(self):
        output, _ = self.run_cli(["add", "taks", "call", "the", "bank"], ["yes"])
        self.assertIn("Did you mean: add task call the bank?", output)
        self.assertIn("Added task 1: call the bank", output)

    def test_keeps_going_while_questions_continue(self):
        output, asked = self.run_cli(["remind", "me", "to", "pay", "rent"], ["whenever", "in 2 hours"])
        self.assertIn("didn't catch that time", output)
        self.assertIn("Reminder 1 set", output)
        self.assertEqual(asked.call_count, 2)

    def test_a_blank_answer_or_end_of_input_stops_asking(self):
        for answers in ([""], [EOFError], [KeyboardInterrupt]):
            output, _ = self.run_cli(["remind", "me", "to", "pay", "rent"], answers)
            self.assertIn("When should I", output)

    def test_does_not_wait_when_output_is_piped_or_scripted(self):
        output, asked = self.run_cli(["add", "taks", "x"], [], is_terminal=False)
        self.assertIn("Did you mean", output)
        asked.assert_not_called()

    def test_a_plain_command_does_not_prompt(self):
        _, asked = self.run_cli(["add", "task", "x"], [])
        asked.assert_not_called()


class _Unclosed:
    """A database connection that stays open after the terminal command finishes."""

    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        return getattr(self._db, name)

    def __enter__(self):
        return self._db.__enter__()

    def __exit__(self, *args):
        return self._db.__exit__(*args)

    def close(self):
        pass


class InstructionsTest(Base):
    def test_includes_the_time_tasks_reminders_and_commands(self):
        self.say("add task buy milk")
        self.say("remind me in 2 hours to check the oven")
        text = ai.build_instructions(self.db, datetime(2026, 10, 31, 11, 5))
        self.assertIn("Saturday, October 31, 2026 11:05 AM", text)
        self.assertIn("- buy milk", text)
        self.assertIn("check the oven", text)
        self.assertIn("- add task <title>", text)

    def test_leaves_out_off_limits_commands(self):
        text = ai.build_instructions(self.db)
        for usage in ai.OFF_LIMITS:
            self.assertNotIn(f"- {usage}\n", text)

    def test_leaves_out_notes_and_location(self):
        self.say("note the gate code is 4412")
        storage.set_setting(self.db, "location", json.dumps({"name": "Secretville"}))
        text = ai.build_instructions(self.db)
        self.assertNotIn("4412", text.split("Now:")[1])
        self.assertNotIn("Secretville", text)

    def test_braces_in_a_task_do_not_break_it(self):
        self.say("add task fix {commands} and {oops}")
        self.assertIn("fix {commands} and {oops}", ai.build_instructions(self.db))


class TurningOnTest(Base):
    def test_one_model_is_used_right_away(self):
        with models_are("llama3.2:latest"):
            self.assertIn("using llama3.2:latest", self.say("turn on ai"))
        self.assertEqual(storage.get_setting(self.db, "ai_model"), "llama3.2:latest")

    def test_several_models_ask_which(self):
        with models_are("llama3.2:latest", "qwen:latest"):
            self.assertIn("Which one?", self.say("turn on ai"))
            self.assertIsNone(storage.get_setting(self.db, "ai_model"))
            self.assertIn("using qwen:latest", self.say("2"))

    def test_naming_a_model(self):
        with models_are("llama3.2:latest", "qwen:latest"):
            self.assertIn("using qwen:latest", self.say("use model qwen"))

    def test_naming_a_model_ollama_does_not_have(self):
        with models_are("llama3.2:latest"):
            self.assertIn("ollama pull mistral", self.say("use model mistral"))
        self.assertIsNone(storage.get_setting(self.db, "ai_model"))

    def test_no_models_says_how_to_get_one(self):
        with models_are():
            self.assertIn("ollama pull", self.say("turn on ai"))

    def test_ollama_missing_says_how_to_install_it(self):
        with mock.patch.object(ollama, "list_models", side_effect=OSError):
            self.assertIn("ollama.com", self.say("turn on ai"))
        self.assertIsNone(storage.get_setting(self.db, "ai_model"))

    def test_status(self):
        self.assertIn("off", self.say("ai status"))
        self.turn_on()
        with models_are("llama3.2:latest"):
            self.assertIn("Ollama is running", self.say("ai status"))
        with models_are():
            self.assertIn("no longer has", self.say("is the ai working"))
        with mock.patch.object(ollama, "list_models", side_effect=OSError):
            self.assertIn("isn't running", self.say("ai status"))


class FakeOllama(http.server.BaseHTTPRequestHandler):
    """Speaks Ollama's protocol over real HTTP, so the client's requests get checked."""

    requests = []
    tags = {"models": [{"name": "llama3.2:latest"}, {"name": "big:120b-cloud"}]}
    content = json.dumps({"command": "add task call the bank", "reply": ""})

    def send_json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.requests.append((self.path, None))
        self.send_json(self.tags)

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        self.requests.append((self.path, json.loads(self.rfile.read(length))))
        self.send_json({"message": {"role": "assistant", "content": self.content}})

    def log_message(self, *args):
        pass


class ClientTest(unittest.TestCase):
    def setUp(self):
        FakeOllama.requests = []
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        patcher = mock.patch.object(ollama, "ADDRESS", f"http://127.0.0.1:{server.server_port}")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_lists_only_models_on_this_computer(self):
        self.assertEqual(ollama.list_models(), ["llama3.2:latest"])

    def test_chat_request_and_answer(self):
        answer = ollama.chat("llama3.2:latest", "be helpful", "hello")
        self.assertEqual(answer["command"], "add task call the bank")
        path, body = FakeOllama.requests[0]
        self.assertEqual(path, "/api/chat")
        self.assertEqual(body["model"], "llama3.2:latest")
        self.assertIs(body["stream"], False)
        self.assertEqual(body["format"]["required"], ["command", "reply"])
        self.assertEqual(
            body["messages"],
            [{"role": "system", "content": "be helpful"}, {"role": "user", "content": "hello"}],
        )

    def test_an_answer_that_is_not_an_object_is_refused(self):
        with mock.patch.object(FakeOllama, "content", "[1, 2]"):
            with self.assertRaises(ValueError):
                ollama.chat("llama3.2:latest", "be helpful", "hello")

    def test_a_windows_proxy_is_never_used(self):
        proxies = {"http": "http://proxy.example:8080"}
        with mock.patch("urllib.request.getproxies", return_value=proxies):
            self.assertEqual(ollama.list_models(), ["llama3.2:latest"])

    def test_refuses_a_model_hosted_online(self):
        with self.assertRaises(ValueError):
            ollama.chat("big:120b-cloud", "be helpful", "hello")
        self.assertEqual(FakeOllama.requests, [])

    def test_the_real_address_is_this_computer(self):
        mock.patch.stopall()
        self.assertTrue(ollama.ADDRESS.startswith("http://127.0.0.1:"))


class WindowTest(unittest.TestCase):
    def setUp(self):
        # See test_briefing: old windows must be freed here, not on the worker thread.
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
        storage.set_setting(self.db, "ai_model", "llama3.2:latest")

    def test_the_window_stays_free_while_the_model_thinks(self):
        release = threading.Event()
        threads = []

        def slow_chat(model, system, message, history):
            threads.append(threading.current_thread())
            release.wait(5)
            return {"command": "add task call the bank", "reply": ""}

        with mock.patch.object(ollama, "chat", slow_chat):
            self.app.run("I should probably ring the bank")
            self.assertIn("Thinking...", self.app.log.get("1.0", "end"))
            self.app.run("add task something else")
            self.assertIn("something else", self.app.agenda.get("1.0", "end"))
            release.set()
            deadline = time.time() + 5
            while self.app.fetched.empty() and time.time() < deadline:
                time.sleep(0.01)
            self.app.check_fetches()

        self.assertIsNot(threads[0], threading.main_thread())
        self.assertIn(
            'About "I should probably ring the bank": Did you mean: add task call the bank?',
            self.app.log.get("1.0", "end"),
        )
        self.app.run("yes")
        self.assertIn("call the bank", self.app.agenda.get("1.0", "end"))


if __name__ == "__main__":
    unittest.main()

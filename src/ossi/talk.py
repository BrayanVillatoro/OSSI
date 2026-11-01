"""A spoken conversation in the window: what you say goes to OSSI, and its replies are read out."""

import re
import threading
from tkinter import messagebox

from ossi import glow, ollama, router, storage, voice
from ossi.skills import ai

POLL_MS = 50
END_WORDS = {"goodbye", "bye", "bye bye", "end conversation", "end the conversation", "stop listening"}
GREETING = "Hey, I'm listening."
FAREWELL = "Okay, talk soon."
NEEDS_AI = (
    "Talking needs the local AI. Click Settings and turn on Use a local AI model, then click Talk."
)
CONFIRM_INSTALL = (
    "Talking needs the voice add-on, which is about 600 MB to download.\n\n"
    "After that it works offline, and what you say stays on this computer.\n\nInstall it now?"
)
SENTENCE_END = re.compile(r"""[.!?]+["')]*\s+|\n+""")
NOT_SPOKEN = re.compile(r"[*_#`]")


def finished_length(text):
    """How much of a reply still being written is complete sentences."""
    ends = [match.end() for match in SENTENCE_END.finditer(text)]
    return ends[-1] if ends else 0


def sentences(text):
    """Split text to read out one sentence at a time, so the first one starts sooner."""
    cleaned = NOT_SPOKEN.sub("", text)
    found = []
    position = 0
    for match in SENTENCE_END.finditer(cleaned):
        found.append(cleaned[position : match.end()].strip())
        position = match.end()
    found.append(cleaned[position:].strip())
    return [sentence for sentence in found if sentence]


class Talk:
    def __init__(self, app):
        self.app = app
        self.active = False
        self.installing = False
        self.process = None
        self.draft = None
        self.sent = ""
        self.muted = False
        self.ending = False
        self.timer = None
        self.setup_messages = []

    def toggle(self):
        if self.active:
            self.end()
        elif not self.installing:
            self.begin()

    def begin(self):
        if not storage.get_setting(self.app.db, "ai_model"):
            self.app.say(NEEDS_AI)
        elif voice.is_installed():
            self.start()
        elif messagebox.askyesno("OSSI", CONFIRM_INSTALL):
            self.install()

    def install(self):
        self.installing = True
        self.app.show_talk_status("Setting up voice...")

        def work():
            try:
                voice.install(self.setup_messages.append)
                self.setup_messages.append(True)
            except Exception as error:
                self.setup_messages.append(error)

        threading.Thread(target=work, daemon=True).start()
        self.schedule()

    def check_install(self):
        while self.setup_messages:
            message = self.setup_messages.pop(0)
            if message is True:
                self.installing = False
                self.app.hide_talk_status()
                self.start()
            elif isinstance(message, Exception):
                self.installing = False
                self.app.hide_talk_status()
                self.app.say(
                    f"I couldn't set up the voice add-on: {message}. "
                    f"There's more detail in {voice.log_path()}."
                )
            else:
                self.app.show_talk_status(message)

    def start(self):
        self.app.settings.stop_sample()  # The microphone would hear it.
        model = storage.get_setting(self.app.db, "ai_model")
        name = storage.get_setting(self.app.db, "talk_voice") or voice.DEFAULT_VOICE
        self.process = voice.VoiceProcess(name if name in voice.VOICES else voice.DEFAULT_VOICE)
        self.active = True
        self.muted = False
        self.ending = False
        self.app.show_talk_status("Getting ready...", glow.WAITING)
        self.app.set_talk_button(True)
        system = ai.build_talk_instructions(self.app.db)
        threading.Thread(target=self.warm_up, args=(model, system), daemon=True).start()
        self.schedule()

    def warm_up(self, model, system):
        try:
            ollama.starting_if_needed(lambda: ollama.warm(model, system))
        except Exception:
            pass  # The first reply will say so if the model really can't be reached.

    def schedule(self):
        if self.timer is None:
            self.timer = self.app.root.after(POLL_MS, self.poll)

    def poll(self):
        self.timer = None
        self.check_install()
        if self.active:
            while self.active and not self.process.events.empty():
                self.handle(self.process.events.get())
            self.read_draft()
        if self.active or self.installing:
            self.schedule()

    def handle(self, event):
        kind = event["type"]
        if kind == "level":
            self.app.glow.set_level(event["value"])
        elif kind == "ready":
            self.show_listening()
            self.app.say(GREETING, keep_place=True)
            self.speak(GREETING)
        elif kind == "hearing":
            self.app.show_talk_status("Listening...", glow.HEARING)
        elif kind == "heard":
            self.respond(event["text"])
        elif kind == "nothing":
            self.show_listening()
        elif kind == "interrupted":
            self.keep_interrupted_reply()
        elif kind == "speaking":
            self.app.show_talk_status("Speaking...", glow.SPEAKING)
        elif kind == "quiet":
            if self.ending:
                self.end()
            else:
                self.show_listening()
        elif kind == "error":
            self.fail(event["message"])
        elif kind == "gone":
            self.fail("the voice stopped unexpectedly")

    def fail(self, reason):
        self.end()
        self.app.say(f"Sorry, I had to end the conversation: {reason}. Details: {voice.log_path()}")

    def show_listening(self):
        if self.muted:
            self.app.show_talk_status("Muted", glow.MUTED_STATE)
        elif self.draft:  # It said a sentence, and the model is still writing the next one.
            self.app.show_talk_status("Thinking...", glow.THINKING)
        else:
            self.app.show_talk_status("Listening...", glow.LISTENING)

    def respond(self, text):
        session = self.app.session
        self.app.echo(text)
        if router.clean(text).lower() in END_WORDS:
            self.ending = True
            self.app.say(FAREWELL, keep_place=True)
            self.speak(FAREWELL)
            return
        session.canceled_turn = session.turn  # A new message replaces any answer on its way.
        self.app.show_talk_status("Thinking...", glow.THINKING)
        reply = router.handle_spoken(self.app.db, text, session)
        if reply is not None:
            self.app.say(reply)
            self.speak(reply)
        self.app.refresh_sidebar()

    def speak(self, text):
        for sentence in sentences(text):
            self.process.send(type="say", text=sentence)

    def follow(self, draft):
        """Read out this reply as it's written."""
        self.draft = draft
        self.sent = ""
        self.show_listening()  # Looking at a picture starts well after OSSI said it would.

    def read_draft(self):
        if self.draft is None:
            return
        if self.draft.stopped():
            self.draft = None
            return
        finished = self.draft.text[: finished_length(self.draft.text)]
        if len(finished) > len(self.sent):
            self.speak(finished[len(self.sent) :])
            self.sent = finished

    def reply_finished(self, reply):
        """Read out whatever part of a finished reply hasn't been yet."""
        if self.ending:
            return
        said = self.sent.strip()
        position = reply.find(said) if said else -1
        self.speak(reply[position + len(said) :] if position >= 0 else reply)
        self.draft = None
        self.sent = ""

    def keep_interrupted_reply(self):
        """Being cut off cancels the reply, so keep the part they were told in the chat."""
        session = self.app.session
        draft, said = self.draft, self.sent.strip()
        self.draft = None
        self.sent = ""
        session.canceled_turn = session.turn
        if draft and said:
            self.app.stop_writing(draft)
            self.app.say(f"{said}...", keep_place=True)
            router.remember(session, "assistant", said)

    def toggle_mute(self):
        self.muted = not self.muted
        self.process.send(type="mute", on=self.muted)
        self.app.set_mute_label(self.muted)
        self.show_listening()

    def tell(self, text):
        """Read out something that isn't a reply, like a reminder coming due."""
        if self.active:
            self.speak(text)

    def end(self):
        if self.active:
            self.process.close()
        self.active = False
        self.ending = False
        self.draft = None
        self.sent = ""
        self.app.hide_talk_status()
        self.app.set_talk_button(False)

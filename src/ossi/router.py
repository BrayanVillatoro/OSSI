"""Sends what the user typed to the command that matches it."""

import re
from dataclasses import dataclass, field
from typing import Callable

from ossi import fuzzy, skills  # noqa: F401  (importing skills registers every skill)
from ossi.registry import COMMANDS, NO, YES, Ask, Confirm, Fetch
from ossi.skills import ai
from ossi.skills import camera as camera_skill

FALLBACK = ai.NOT_UNDERSTOOD
NEVER_MIND = "OK, never mind."

DISMISS = {"never mind", "nevermind", "forget it", "cancel", "stop"}
# "cancel" alone means canceling a reminder unless OSSI just asked a question.
STOP_WAITING = DISMISS - {"cancel"}
MOST_REMEMBERED_MESSAGES = 12
MOST_REMEMBERED_CHARACTERS = 500

LEADING_FILLER = re.compile(
    r"^(?:(?:hey|hi|hello|ok|okay|yo|oh|um|so|and|also|actually)\b[, ]*)*(?:ossi\b[, ]*)?"
    r"(?:(?:please|just|can you|could you|would you|will you|i want you to|i need you to"
    r"|i'd like you to)\s+)*",
    re.IGNORECASE,
)
TRAILING_FILLER = re.compile(r"[, ]+(?:please|thanks|thank you)$", re.IGNORECASE)
APOSTROPHES = {
    "whats": "what's",
    "hows": "how's",
    "whos": "who's",
    "wheres": "where's",
    "thats": "that's",
    "dont": "don't",
    "doesnt": "doesn't",
    "isnt": "isn't",
    "arent": "aren't",
    "cant": "can't",
    "wont": "won't",
}
MISSING_APOSTROPHE = re.compile(rf"\b(?:{'|'.join(APOSTROPHES)})\b", re.IGNORECASE)


@dataclass
class Session:
    """What OSSI remembers during one conversation."""

    pending: Confirm | Ask | None = None
    focus: dict = field(default_factory=dict)  # The task or reminder "it" refers to.
    listing: dict = field(default_factory=dict)  # The order "the second one" refers to.
    defer: Callable[[Fetch], None] | None = None  # Runs slow work without blocking the caller.
    show_photo: Callable[[str], None] | None = None  # Shows a picture, if this can show them.
    open_camera: Callable[[], None] | None = None  # Opens a live view, if this can show one.
    live_camera: object | None = None  # The live view while it's open, to take pictures from.
    history: list = field(default_factory=list)  # Recent messages, for the AI's context.
    turn: int = 0  # How many messages the user has sent.
    thinking: int = 0  # How many answers the AI still owes.
    canceled_turn: int = 0  # The AI's answers to messages up to this one are unwanted.


def remember(session, role, text):
    entry = {"role": role, "content": text[:MOST_REMEMBERED_CHARACTERS]}
    # A confirmed command replies through two layers, which shouldn't count twice.
    if not session.history or session.history[-1] != entry:
        session.history.append(entry)
    del session.history[:-MOST_REMEMBERED_MESSAGES]


def clean(text):
    return text.replace("’", "'").strip().rstrip(" ?!.,")


def strip_filler(text):
    return TRAILING_FILLER.sub("", LEADING_FILLER.sub("", text))


def find_command(text):
    for cmd in COMMANDS:
        for pattern in cmd.patterns:
            match = pattern.fullmatch(text)
            if match:
                return cmd, match.groupdict()
    return None


def with_apostrophes(text):
    """Put back the apostrophes people skip when typing, like "whats" or "dont"."""
    return MISSING_APOSTROPHE.sub(lambda match: APOSTROPHES[match.group().lower()], text)


def find_loosely(text):
    """Find a command, allowing for filler words and skipped apostrophes.

    The exact wording is tried first, so that text being saved, like a note, stays as typed.
    """
    stripped = strip_filler(text)
    return find_command(text) or find_command(stripped) or find_command(with_apostrophes(stripped))


def run(db, found, session):
    cmd, groups = found
    if cmd.wants_session:
        groups["session"] = session
    return cmd.handler(db, **groups)


def render(db, result, session):
    """Turn a skill's result into text, remembering any question it asks."""
    if isinstance(result, Fetch):
        if session.defer:
            session.defer(result)
            return result.prompt
        return render(db, result.finish(db, result.get_value(), session), session)
    if isinstance(result, Confirm):
        session.pending = result
        reply = f"{result.intro}{result.question} {result.shown or result.command}?"
    elif isinstance(result, Ask):
        session.pending = result
        reply = result.prompt
    else:
        reply = result
    if reply is not None:  # None means there's nothing to say, like a canceled AI answer.
        remember(session, "assistant", reply)
    return reply


def answer_pending(db, pending, text, session):
    if isinstance(pending, Ask):
        return pending.answer(db, text, session)
    if text.lower() in YES:
        return handle(db, pending.command, session)
    if text.lower() in NO:
        return NEVER_MIND
    return None


def first_word(text):
    words = text.lower().replace(",", " ").split()
    return words[0] if words else ""


def handle_spoken(db, text, session):
    """Like `handle`, but for speech, which can be misheard and is often just conversation.

    Commands that only look something up run right away, and anything else asks first. Typos
    aren't corrected, so that everyday words don't turn into "Did you mean".
    """
    text = clean(text)
    stripped = strip_filler(text)
    found = find_loosely(text)
    pending = session.pending
    # People say more than the bare word out loud, like "Yes, please do".
    if isinstance(pending, Confirm) and first_word(text) in YES:
        return handle(db, "yes", session)
    if isinstance(pending, Confirm) and first_word(text) in NO:
        return handle(db, "no", session)
    if isinstance(pending, Ask) and not found:
        return handle(db, text, session)
    if (pending and text.lower() in DISMISS) or (session.thinking and text.lower() in STOP_WAITING):
        return handle(db, text, session)

    session.pending = None
    session.turn += 1
    remember(session, "user", text)

    if found and found[0].usage in ai.LOOKUPS:
        return render(db, run(db, found, session), session)
    # There's nothing to ask if the camera is already on, or can't turn on.
    if (
        found
        and found[0].usage in camera_skill.TURNS_IT_ON
        and (session.live_camera or not camera_skill.is_on(db))
    ):
        return render(db, run(db, found, session), session)
    if found and found[0].usage is not None:
        return render(db, ai.spoken_confirm(stripped, usage=found[0].usage), session)
    if session.live_camera and camera_skill.is_about_the_view(text):
        return render(db, camera_skill.look(db, session, text), session)

    def find_suggested(command):
        return find_loosely(clean(command))

    result = ai.converse(db, text, find_suggested, session)
    return render(db, FALLBACK if result is None else result, session)


def handle(db, text, session=None):
    session = Session() if session is None else session
    pending, session.pending = session.pending, None
    text = clean(text)
    session.turn += 1
    remember(session, "user", text)

    if session.thinking and text.lower() in STOP_WAITING:
        session.canceled_turn = session.turn
        return render(db, NEVER_MIND, session)
    if pending and text.lower() in DISMISS:
        return render(db, NEVER_MIND, session)

    stripped = strip_filler(text)
    found = find_loosely(text)
    if found:
        return render(db, run(db, found, session), session)

    if pending:
        result = answer_pending(db, pending, text, session)
        if result is not None:
            return render(db, result, session)

    # With the live view open, a message that isn't a command is a question about what's in it.
    if session.live_camera and text.lower() not in DISMISS:
        return render(db, camera_skill.look(db, session, text), session)

    suggestion = fuzzy.suggest(
        with_apostrophes(stripped or text), lambda c: find_command(c) is not None
    )
    if suggestion:
        return render(db, Confirm(suggestion), session)

    def find_suggested(command):
        return find_loosely(clean(command))

    result = ai.understand(db, text, find_suggested, session)
    return render(db, FALLBACK if result is None else result, session)

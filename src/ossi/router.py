"""Sends what the user typed to the command that matches it."""

import re
from dataclasses import dataclass, field
from typing import Callable

from ossi import fuzzy, skills  # noqa: F401  (importing skills registers every skill)
from ossi.registry import COMMANDS, Ask, Confirm, Fetch

FALLBACK = "Sorry, I don't know how to do that yet. Type 'help' to see what I can do."
NEVER_MIND = "OK, never mind."

YES = {"yes", "y", "yeah", "yep", "yup", "sure", "ok", "okay", "correct", "right"}
NO = {"no", "n", "nope", "nah", "wrong"}
DISMISS = {"never mind", "nevermind", "forget it", "cancel", "stop"}

LEADING_FILLER = re.compile(
    r"^(?:(?:hey|hi|hello|ok|okay|yo|oh|um|so|and|also|actually)\b[, ]*)*(?:ossi\b[, ]*)?"
    r"(?:(?:please|just|can you|could you|would you|will you|i want you to|i need you to"
    r"|i'd like you to)\s+)*",
    re.IGNORECASE,
)
TRAILING_FILLER = re.compile(r"[, ]+(?:please|thanks|thank you)$", re.IGNORECASE)


@dataclass
class Session:
    """What OSSI remembers during one conversation."""

    pending: Confirm | Ask | None = None
    focus: dict = field(default_factory=dict)  # The task or reminder "it" refers to.
    listing: dict = field(default_factory=dict)  # The order "the second one" refers to.
    defer: Callable[[Fetch], None] | None = None  # Runs slow work without blocking the caller.


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


def run(db, found, session):
    cmd, groups = found
    if cmd.wants_session:
        groups["session"] = session
    return cmd.handler(db, **groups)


def render(db, result, session):
    """Turn a skill's result into text, remembering any question it asks."""
    if isinstance(result, Confirm):
        session.pending = result
        return f"Did you mean: {result.command}?"
    if isinstance(result, Ask):
        session.pending = result
        return result.prompt
    if isinstance(result, Fetch):
        if session.defer:
            session.defer(result)
            return result.prompt
        return render(db, result.finish(db, result.get_value(), session), session)
    return result


def answer_pending(db, pending, text, session):
    if isinstance(pending, Ask):
        return pending.answer(db, text, session)
    if text.lower() in YES:
        return handle(db, pending.command, session)
    if text.lower() in NO:
        return NEVER_MIND
    return None


def handle(db, text, session=None):
    session = Session() if session is None else session
    pending, session.pending = session.pending, None
    text = clean(text)

    if pending and text.lower() in DISMISS:
        return NEVER_MIND

    stripped = strip_filler(text)
    found = find_command(text) or find_command(stripped)
    if found:
        return render(db, run(db, found, session), session)

    if pending:
        result = answer_pending(db, pending, text, session)
        if result is not None:
            return render(db, result, session)

    suggestion = fuzzy.suggest(stripped or text, lambda c: find_command(c) is not None)
    if suggestion:
        return render(db, Confirm(suggestion), session)
    return FALLBACK

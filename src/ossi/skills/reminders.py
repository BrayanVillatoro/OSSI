"""Reminders. The desktop app watches for due ones and shows the notifications."""

import re
import time

from ossi.references import is_number, is_pronoun, normalize, resolve, which_one
from ossi.registry import Ask, Confirm, command
from ossi.when import corrected_time_words, describe, parse_when

HOW_TO = "Try: remind me tomorrow at 3pm to call the bank"


def upcoming(db):
    return db.execute(
        "SELECT id, message, due_at FROM reminders WHERE fired_at IS NULL ORDER BY due_at, id"
    ).fetchall()


def fire_due(db, now=None):
    """Mark due reminders as fired and return them.

    Claiming each one with its own update means that if two copies of OSSI are ever
    running, only one of them announces it.
    """
    now = time.time() if now is None else now
    due = db.execute(
        "SELECT id, message, due_at FROM reminders "
        "WHERE fired_at IS NULL AND due_at <= ? ORDER BY due_at, id",
        (now,),
    ).fetchall()
    fired = []
    with db:
        for row in due:
            cursor = db.execute(
                "UPDATE reminders SET fired_at = ? WHERE id = ? AND fired_at IS NULL",
                (now, row["id"]),
            )
            if cursor.rowcount:
                fired.append(row)
    return fired


def split_request(text):
    """Separate the time from the message. Either can come back as None."""
    # "tomorrow at 3pm to call the bank"
    for match in re.finditer(r" to ", text, re.IGNORECASE):
        due = parse_when(text[: match.start()])
        if due is not None:
            return text[match.end() :].strip(), due

    # "to call the bank tomorrow at 3pm". The longest ending that's a time wins.
    message = re.sub(r"^(?:to|about|that) ", "", text, flags=re.IGNORECASE)
    words = message.split()
    for start in range(len(words)):
        due = parse_when(" ".join(words[start:]))
        if due is not None:
            return " ".join(words[:start]) or None, due
    return message, None


def save_reminder(db, session, message, due):
    if due <= time.time():
        return f"That time has already passed ({describe(due)}). {HOW_TO}"
    with db:
        cursor = db.execute(
            "INSERT INTO reminders (message, due_at) VALUES (?, ?)", (message, due)
        )
    session.focus["reminder"] = cursor.lastrowid
    return f"Reminder {cursor.lastrowid} set for {describe(due)}: {message}"


def ask_what(due=None):
    def answer(db, text, session):
        if due is not None:
            return save_reminder(db, session, text, due)
        return add_reminder(db, session, text)

    return Ask("What should I remind you about?", answer)


def ask_when(message):
    def answer(db, text, session):
        due = parse_when(text)
        if due is None:
            due = next(filter(None, map(parse_when, corrected_time_words(text))), None)
        if due is None:
            return Ask(
                "I didn't catch that time. Try 'tomorrow at 3pm' or 'in 20 minutes', "
                "or say never mind.",
                answer,
            )
        return save_reminder(db, session, message, due)

    return Ask(f"When should I remind you to {message}?", answer)


@command(
    "remind me <when> to <what>",
    r"remind me(?: (?P<request>.+))?",
    r"(?:set|add|create|make)(?: me)?(?: a)? reminder(?: for)?(?: (?P<request>.+))?",
)
def add_reminder(db, session, request=None):
    if not request:
        return ask_what()
    message, due = split_request(request)
    if due is None:
        for candidate in corrected_time_words(request):
            fixed_message, fixed_due = split_request(candidate)
            if fixed_due is not None and fixed_message:
                return Confirm(f"remind me {candidate}")
        return ask_when(message)
    if not message:
        return ask_what(due)
    return save_reminder(db, session, message, due)


@command(
    "reminders",
    r"(?:show (?:me )?|list |what are )?(?:my )?(?:upcoming )?reminders",
)
def list_reminders(db, session):
    rows = upcoming(db)
    session.listing["reminder"] = [row["id"] for row in rows]
    if not rows:
        return "No upcoming reminders."
    return "\n".join(
        f"{row['id']}. {describe(row['due_at'])}: {row['message']}" for row in rows
    )


@command(
    "cancel reminder <number or name>",
    r"cancel(?: (?P<ref>.+))?",
    r"(?:delete|remove) reminder(?: (?P<ref>.+))?",
    r"(?:delete|remove) (?P<ref>.+ reminder)",
)
def cancel_reminder(db, session, ref=None):
    rows = upcoming(db)
    if not rows and is_pronoun(ref):
        return "No upcoming reminders."
    focus = session.focus.get("reminder")
    matches = resolve(ref, rows, "message", focus, session.listing.get("reminder"))
    if not matches:
        if is_number(ref):
            return f"There's no upcoming reminder {normalize(ref)}."
        return f"I can't find an upcoming reminder matching '{normalize(ref)}'."
    # Canceling can't be undone, so a bare "cancel", or "cancel it" with nothing in mind,
    # has to ask.
    guessing = ref is None or (is_pronoun(ref) and focus not in [row["id"] for row in rows])
    if len(matches) > 1 or guessing:
        session.listing["reminder"] = [row["id"] for row in matches]
        return Ask(
            which_one(matches, "message"),
            lambda db, text, session: cancel_reminder(db, session, text),
        )

    reminder = matches[0]
    with db:
        db.execute("DELETE FROM reminders WHERE id = ?", (reminder["id"],))
    return f"Canceled reminder {reminder['id']}: {reminder['message']}"

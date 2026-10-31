"""Sends what the user typed to the command that matches it."""

from ossi import skills  # noqa: F401  (importing this registers every skill)
from ossi.registry import COMMANDS

FALLBACK = "Sorry, I don't know how to do that yet. Type 'help' to see what I can do."


def handle(db, text):
    text = text.strip().rstrip("?!.")
    for cmd in COMMANDS:
        for pattern in cmd.patterns:
            match = pattern.fullmatch(text)
            if match:
                return cmd.handler(db, **match.groupdict())
    return FALLBACK

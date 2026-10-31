"""The list of commands OSSI understands. Skills add to it with @command."""

import re
from dataclasses import dataclass
from typing import Callable

COMMANDS = []


@dataclass
class Command:
    usage: str
    patterns: list[re.Pattern]
    handler: Callable[..., str]


def command(usage, *patterns):
    """Register a function to run when the whole input matches one of the patterns.

    The function receives the database connection, plus each named group in the
    pattern as a keyword argument. It returns the reply as a string.
    """

    def register(handler):
        compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
        COMMANDS.append(Command(usage, compiled, handler))
        return handler

    return register

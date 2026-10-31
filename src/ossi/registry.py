"""The list of commands OSSI understands. Skills add to it with @command."""

import inspect
import re
from dataclasses import dataclass
from typing import Callable

COMMANDS = []


@dataclass
class Command:
    usage: str | None
    patterns: list[re.Pattern]
    handler: Callable[..., object]
    wants_session: bool
    catch_all: bool = False


@dataclass
class Confirm:
    """A skill's reply that asks whether this corrected command is what the user meant."""

    command: str


@dataclass
class Ask:
    """A skill's reply that asks a question and waits for the answer.

    `answer(db, text, session)` receives the next message and returns a reply as usual.
    Any message that is itself a command runs as that command instead.
    """

    prompt: str
    answer: Callable[..., object]


@dataclass
class Fetch:
    """A skill's reply that has to wait on something slow, like the internet.

    `work()` may run on another thread, so it can't touch the database. `finish(db, value,
    session)` then receives what it returned, or None if it failed, and returns the reply
    as usual. The window shows `prompt` in the meantime.
    """

    prompt: str
    work: Callable[[], object]
    finish: Callable[..., object]

    def get_value(self):
        try:
            return self.work()
        except Exception:  # The network or a bad response can fail in many ways.
            return None


def command(usage, *patterns, catch_all=False):
    """Register a function to run when the whole input matches one of the patterns.

    The function receives the database connection, plus each named group in the pattern
    as a keyword argument. If it has a `session` parameter, it also receives the
    conversation so far. It returns the reply as a string, a Confirm, an Ask, or a Fetch.

    A usage of None leaves the command out of `help`. Set catch_all on patterns broad enough
    to swallow other skills' commands, like "<anything> is done". They're tried last, so
    the order skills happen to load in can't change what a message means.
    """

    def register(handler):
        compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
        wants_session = "session" in inspect.signature(handler).parameters
        COMMANDS.append(Command(usage, compiled, handler, wants_session, catch_all))
        COMMANDS.sort(key=lambda cmd: cmd.catch_all)  # A stable sort keeps the rest in order.
        return handler

    return register

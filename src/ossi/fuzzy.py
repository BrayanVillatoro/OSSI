"""Works out what a mistyped command probably meant.

The words come from the skills' own patterns, so new skills get this automatically.
OSSI always asks before running a correction, so a wrong guess costs one "no".
"""

import difflib
import re

from ossi.registry import COMMANDS

MAX_WORDS_TO_CORRECT = 4
WORD_CUTOFF = 0.75
PHRASE_CUTOFF = 0.75
# Real words that sit too close to command words, like "not" and "note".
LEAVE_ALONE = {"not", "the", "and", "for", "you", "are", "was", "but", "now", "new"}


def vocabulary():
    words = set()
    for cmd in COMMANDS:
        for pattern in cmd.patterns:
            source = re.sub(r"\?P<\w+>", "", pattern.pattern)
            source = re.sub(r"\\[a-zA-Z]", "", source)
            words.update(re.findall(r"[a-z][a-z'-]+", source))
    return words


def phrases():
    return [cmd.usage for cmd in COMMANDS if cmd.usage and "<" not in cmd.usage]


def suggest(text, is_command):
    """Return a corrected version of the text that is a command, or None."""
    return correct_leading_words(text, is_command) or closest_phrase(text)


def correct_leading_words(text, is_command):
    # Only the start of a message is command words. The rest could be a task's title,
    # which should keep the user's own spelling.
    known = vocabulary()
    words = text.split()
    for index in range(min(MAX_WORDS_TO_CORRECT, len(words))):
        word = words[index].lower()
        if len(word) < 3 or word in known or word in LEAVE_ALONE:
            continue
        options = difflib.get_close_matches(word, known, n=3, cutoff=WORD_CUTOFF)
        for option in options:
            candidate = " ".join([*words[:index], option, *words[index + 1 :]])
            if is_command(candidate):
                return candidate
        if options:
            words[index] = options[0]
    return None


def closest_phrase(text):
    matches = difflib.get_close_matches(text.lower(), phrases(), n=1, cutoff=PHRASE_CUTOFF)
    return matches[0] if matches else None

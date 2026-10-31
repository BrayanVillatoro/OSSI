"""Works out which task or reminder someone means: "3", "it", "the second one", "milk"."""

import difflib

ORDINALS = {
    "first": 0,
    "1st": 0,
    "second": 1,
    "2nd": 1,
    "third": 2,
    "3rd": 2,
    "fourth": 3,
    "4th": 3,
    "fifth": 4,
    "5th": 4,
    "last": -1,
}
LEADING_WORDS = {"the", "my", "that", "this", "task", "reminder", "number"}
TRAILING_WORDS = {"task", "reminder", "one"}
NAME_CUTOFF = 0.6


def normalize(ref):
    words = (ref or "").lower().replace("#", "").split()
    while words and words[0] in LEADING_WORDS:
        words.pop(0)
    while words and words[-1] in TRAILING_WORDS:
        words.pop()
    return " ".join(words)


def is_pronoun(ref):
    return normalize(ref) in ("", "it")


def is_number(ref):
    return normalize(ref).isdigit()


def resolve(ref, rows, label, focus=None, listing=None):
    """Return the rows the reference could mean.

    One row is a clear answer, none means nothing matched, and several means it's
    worth asking which one.
    """
    wanted = normalize(ref)
    if is_pronoun(ref):
        focused = [row for row in rows if row["id"] == focus]
        return focused or list(rows)
    if wanted.isdigit():
        return [row for row in rows if row["id"] == int(wanted)]
    if wanted in ORDINALS:
        return by_position(ORDINALS[wanted], rows, listing)

    named = [row for row in rows if wanted in row[label].lower()]
    if named:
        return named
    labels = [row[label].lower() for row in rows]
    close = difflib.get_close_matches(wanted, labels, n=5, cutoff=NAME_CUTOFF)
    return [row for row in rows if row[label].lower() in close]


def by_position(position, rows, listing):
    by_id = {row["id"]: row for row in rows}
    ordered = [by_id[i] for i in listing or [] if i in by_id] or list(rows)
    try:
        return [ordered[position]]
    except IndexError:
        return []


def which_one(rows, label):
    lines = "\n".join(f"{row['id']}. {row[label]}" for row in rows)
    return f"Which one?\n{lines}"

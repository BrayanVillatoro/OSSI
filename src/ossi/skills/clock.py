"""Time and date."""

from datetime import datetime

from ossi.registry import command


@command("what time is it", r"time", r"what time is it", r"what's the time", r"what is the time")
def current_time(db):
    return datetime.now().strftime("It's %I:%M %p.")


@command(
    "what's the date",
    r"date",
    r"today",
    r"what(?:'s| is) (?:the date|today's date)",
    r"what day is (?:it|today)",
)
def current_date(db):
    return datetime.now().strftime("It's %A, %B %d, %Y.")

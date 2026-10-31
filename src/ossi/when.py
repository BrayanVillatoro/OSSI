"""Turns phrases like "tomorrow at 3pm" into a time, and a time back into words.

Times are Unix timestamps, so daylight saving changes can't shift a reminder.
"""

import difflib
import re
from datetime import date, datetime, time, timedelta

MAX_SECONDS_AHEAD = 10 * 366 * 86400
DEFAULT_HOUR = 9
TONIGHT_HOUR = 20

UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
UNIT = r"(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?)"
AMOUNT = r"(\d+|an?|one|half an?)"
RELATIVE_PART = rf"{AMOUNT} ?{UNIT}"
RELATIVE = re.compile(rf"in {RELATIVE_PART}(?:(?:,| and)? {RELATIVE_PART})*")

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
MONTH = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
DAY = re.compile(
    r"\b(?:"
    r"(?P<word>today|tonight|tomorrow|tmrw)"
    rf"|(?:(?P<prefix>next|this|on) )?(?P<weekday>{'|'.join(WEEKDAYS)})"
    rf"|(?:on )?(?P<month>{MONTH}) (?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:,? (?P<year>\d{{4}}))?"
    r"|(?:on )?(?P<m>\d{1,2})/(?P<d>\d{1,2})(?:/(?P<y>\d{2}|\d{4}))?"
    r")\b"
)

TIME_WORDS = {
    *WEEKDAYS,
    "january", "february", "march", "april", "june", "july", "august",
    "september", "october", "november", "december",
    "today", "tonight", "tomorrow", "next", "noon", "midnight",
    "morning", "afternoon", "evening", "night",
    "second", "seconds", "minute", "minutes", "hour", "hours",
    "day", "days", "week", "weeks",
}  # fmt: skip

NAMED_TIMES = {"noon": 12, "midnight": 0}
PARTS_OF_DAY = {"morning": 9, "afternoon": 15, "evening": 18, "night": 20}
NAMED_TIME = re.compile(r"(at |in the |this )?(noon|midnight|morning|afternoon|evening|night)")
CLOCK = re.compile(
    r"(?P<at>at |@ ?)?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))? ?(?P<meridiem>am|pm)?"
    r"(?: (?:in the|at|this) (?P<part>morning|afternoon|evening|night))?"
)


def parse_when(text, now=None):
    """Return the timestamp a phrase means, or None if it isn't a time.

    The result can be in the past for a phrase like "today at 3pm", so callers check.
    """
    now = now or datetime.now()
    text = re.sub(r"\b([ap])\.m\b\.?", r"\1m", " ".join(text.lower().split()))
    if not text:
        return None

    seconds = parse_relative(text)
    if seconds is not None:
        return now.timestamp() + seconds

    match = DAY.search(text)
    rest = " ".join((text[: match.start()] + " " + text[match.end() :]).split()) if match else text
    clock = parse_clock(rest, has_day=bool(match)) if rest else None
    if rest and clock is None:
        return None
    if not match:
        return next_occurrence(clock, now)
    return resolve_day(match, clock, now)


def corrected_time_words(text):
    """Yield the text with one likely misspelled time word fixed, one word at a time."""
    words = text.split()
    for index, word in enumerate(words):
        lowered = word.lower()
        if len(lowered) < 4 or lowered in TIME_WORDS:
            continue
        for option in difflib.get_close_matches(lowered, TIME_WORDS, n=1, cutoff=0.8):
            yield " ".join([*words[:index], option, *words[index + 1 :]])


def parse_relative(text):
    if not RELATIVE.fullmatch(text):
        return None
    total = 0
    for amount, unit in re.findall(RELATIVE_PART, text[3:]):
        seconds = UNIT_SECONDS[unit[0]]
        if amount.startswith("half"):
            total += seconds // 2
        elif amount.isdigit():
            total += int(amount) * seconds
        else:
            total += seconds
    return total if total <= MAX_SECONDS_AHEAD else None


def parse_clock(text, has_day):
    """Return (hour, minute, ambiguous). Ambiguous means it could be AM or PM."""
    named = NAMED_TIME.fullmatch(text)
    if named:
        prefix, name = named.groups()
        if name in NAMED_TIMES:
            return NAMED_TIMES[name], 0, False
        # A lone "morning" is more likely part of the reminder's text than a time.
        return (PARTS_OF_DAY[name], 0, False) if has_day or prefix else None

    match = CLOCK.fullmatch(text)
    if not match:
        return None
    hour = int(match["hour"])
    minute = int(match["minute"] or 0)
    # A bare number like the 3 in "read chapter 3" isn't a time.
    if not (match["at"] or match["minute"] or match["meridiem"] or match["part"]):
        return None
    if hour > 23 or minute > 59:
        return None

    meridiem = match["meridiem"]
    if match["part"] and not meridiem:
        meridiem = "am" if match["part"] == "morning" else "pm"
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        return hour % 12 + (12 if meridiem == "pm" else 0), minute, False
    ambiguous = 1 <= hour <= 12 and not match["hour"].startswith("0")
    return hour, minute, ambiguous


def next_occurrence(clock, now):
    hour, minute, ambiguous = clock
    hours = [hour]
    if ambiguous:
        hours.append(0 if hour == 12 else hour + 12)
    options = [datetime.combine(now.date(), time(h, minute)) for h in hours]
    upcoming = [option for option in options if option > now]
    if upcoming:
        return min(upcoming).timestamp()
    return at(now.date() + timedelta(days=1), clock)


def resolve_day(match, clock, now):
    if match["word"] == "today":
        return at(now.date(), clock) if clock else None
    if match["word"] == "tonight":
        if clock is None:
            return at(now.date(), (TONIGHT_HOUR, 0, False))
        hour, minute, ambiguous = clock
        return at(now.date(), (hour % 12 + 12 if ambiguous else hour, minute, False))
    if match["word"]:
        return at(now.date() + timedelta(days=1), clock)
    if match["weekday"]:
        return next_weekday(match, clock, now)
    if match["month"]:
        month = MONTHS.index(match["month"][:3]) + 1
        return on_date(month, int(match["day"]), match["year"], clock, now)
    year = match["y"]
    if year and len(year) == 2:
        year = f"20{year}"
    return on_date(int(match["m"]), int(match["d"]), year, clock, now)


def next_weekday(match, clock, now):
    ahead = (WEEKDAYS.index(match["weekday"]) - now.weekday()) % 7
    if match["prefix"] == "next":
        ahead = ahead or 7
    result = at(now.date() + timedelta(days=ahead), clock)
    if result <= now.timestamp():
        result = at(now.date() + timedelta(days=ahead + 7), clock)
    return result


def on_date(month, day, year, clock, now):
    chosen_year = int(year) if year else now.year
    if chosen_year > now.year + 10:
        return None
    try:
        result = at(date(chosen_year, month, day), clock)
        if year is None and result <= now.timestamp():
            result = at(date(chosen_year + 1, month, day), clock)
    except ValueError:
        return None
    return result


def at(day, clock):
    if clock is None:
        return datetime.combine(day, time(DEFAULT_HOUR)).timestamp()
    hour, minute, ambiguous = clock
    if ambiguous and hour < 8:
        hour += 12
    return datetime.combine(day, time(hour, minute)).timestamp()


def describe(timestamp, now=None):
    """Put a timestamp into words, like "tomorrow at 3:00 PM"."""
    now = now or datetime.now()
    moment = datetime.fromtimestamp(timestamp)
    clock = moment.strftime("%I:%M %p").lstrip("0")
    days = (moment.date() - now.date()).days
    if days == 0:
        day = "today"
    elif days == 1:
        day = "tomorrow"
    elif days == -1:
        day = "yesterday"
    elif 2 <= days <= 6:
        day = moment.strftime("%A")
    else:
        day = f"{moment:%b} {moment.day}"
        if moment.year != now.year:
            day += f", {moment.year}"
    return f"{day} at {clock}"

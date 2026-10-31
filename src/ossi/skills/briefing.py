"""The daily briefing: weather, today's reminders, and open tasks."""

from datetime import datetime

from ossi import storage
from ossi.registry import Fetch, command
from ossi.skills.reminders import upcoming
from ossi.skills.tasks import open_tasks
from ossi.skills.weather import CHECKING, forecast_text, saved_location
from ossi.when import describe

# A window left open overnight shouldn't brief an empty room at midnight.
FIRST_HOUR = 5
MOST_TASKS_SHOWN = 5


def greeting(now):
    if now.hour < 12:
        part = "morning"
    elif now.hour < 18:
        part = "afternoon"
    else:
        part = "evening"
    return f"Good {part}! It's {now:%A, %B} {now.day}."


def reminders_section(db, session, now):
    rows = upcoming(db)
    today = [row for row in rows if datetime.fromtimestamp(row["due_at"]).date() == now.date()]
    session.listing["reminder"] = [row["id"] for row in today or rows]
    if today:
        lines = [
            f"{row['id']}. {describe(row['due_at'], now).removeprefix('today at ')}: {row['message']}"
            for row in today
        ]
        return "\n".join(["Reminders today:", *lines])
    if rows:
        return f"No reminders today. Next: {describe(rows[0]['due_at'], now)}: {rows[0]['message']}"
    return "No reminders today."


def tasks_section(db, session):
    rows = open_tasks(db)
    session.listing["task"] = [row["id"] for row in rows]
    if not rows:
        return "No open tasks."
    lines = [f"{row['id']}. {row['title']}" for row in rows[:MOST_TASKS_SHOWN]]
    if len(rows) > MOST_TASKS_SHOWN:
        lines.append(f"...and {len(rows) - MOST_TASKS_SHOWN} more. Type 'tasks' to see them all.")
    return "\n".join([f"Open tasks ({len(rows)}):", *lines])


def weather_section(db, weather_text, location):
    if location:
        return weather_text or "I couldn't reach the weather service, so there's no weather today."
    # Only mention this once, so someone who wants no weather isn't nagged every morning.
    if storage.get_setting(db, "weather_hint_shown"):
        return None
    storage.set_setting(db, "weather_hint_shown", "yes")
    return "To add the weather, type 'set my location to' and your city or ZIP code."


def compose(db, session, weather_text, location, now=None):
    now = now or datetime.now()
    sections = [
        greeting(now),
        weather_section(db, weather_text, location),
        reminders_section(db, session, now),
        tasks_section(db, session),
    ]
    return "\n\n".join(section for section in sections if section)


@command(
    "briefing",
    r"(?:(?:give me|show me|show|get) )?(?:my |the |a )?(?:daily |morning )?briefing",
    r"good morning",
    r"what(?:'?s| is| does) (?:on )?(?:my day|today)(?: look like| have| looking like)?",
    r"what(?:'?s| is) on (?:my calendar|for) today",
    r"what do i have (?:on )?today",
)
def give_briefing(db, session=None):
    location = saved_location(db)
    if not location:
        return compose(db, session, None, None)
    return Fetch(
        CHECKING,
        lambda: forecast_text(location),
        lambda db, text, session: compose(db, session, text, location),
    )


@command(
    "turn off the daily briefing",
    r"(?:turn off|stop|disable)(?: the| my)?(?: daily| morning)? briefings?",
)
def turn_off(db):
    storage.set_setting(db, "briefing_automatic", "off")
    return "OK, I won't brief you automatically. You can still type 'briefing' any time."


@command(
    "turn on the daily briefing",
    r"(?:turn on|start|enable)(?: the| my)?(?: daily| morning)? briefings?",
)
def turn_on(db):
    storage.delete_setting(db, "briefing_automatic")
    return "OK, I'll brief you the first time you open the window each day."


def is_due(db, now=None):
    """Whether the automatic briefing should run now."""
    now = now or datetime.now()
    if now.hour < FIRST_HOUR or storage.get_setting(db, "briefing_automatic") == "off":
        return False
    return storage.get_setting(db, "briefing_last_date") != now.date().isoformat()


def mark_given(db, now=None):
    now = now or datetime.now()
    storage.set_setting(db, "briefing_last_date", now.date().isoformat())

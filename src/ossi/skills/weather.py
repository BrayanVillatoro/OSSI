"""Weather, and the saved location it's for."""

import json

from ossi import storage, weather
from ossi.registry import Ask, Fetch, command

CHECKING = "Checking the weather..."
UNREACHABLE = "I couldn't reach the weather service. Check your connection and try again."
HOW_TO = "Type 'set my location to' and your city or ZIP code, and I'll check the weather."


def saved_location(db):
    value = storage.get_setting(db, "location")
    return json.loads(value) if value else None


def forecast_text(place, day=None):
    forecast = weather.get_forecast(place)
    if day and day.lower() == "tomorrow":
        return weather.describe_tomorrow(forecast, place)
    return weather.describe_today(forecast, place)


def ask_location(db, text, session):
    return set_location(db, session, text)


@command(
    "set my location to <city or ZIP code>",
    r"(?:set|change|update) (?:my )?(?:location|city|zip(?: code)?)(?: (?:to|as) (?P<place>.+))?",
    r"my (?:location|city|zip(?: code)?) is (?P<place>.+)",
    r"i live in (?P<place>.+)",
)
def set_location(db, session, place=None):
    if not place:
        return Ask("What city or ZIP code should I use?", ask_location)

    def finish(db, found, session):
        if found is None:
            return UNREACHABLE
        if not found:
            return f"I can't find a place called '{place}'. Try a city and state, or a ZIP code."
        storage.set_setting(db, "location", json.dumps(found))
        reply = f"Your location is now {found['name']}."
        if "," not in place and not place.isdigit():
            reply += " If that's the wrong one, add the state, like 'Springfield, IL'."
        return reply

    return Fetch("Looking that up...", lambda: weather.find_place(place), finish)


@command(
    None,
    r"(?:what's |whats |what is )?my (?:location|city)",
    r"where do i live",
)
def show_location(db):
    place = saved_location(db)
    return f"Your location is {place['name']}." if place else f"I don't have one yet. {HOW_TO}"


@command(
    "forget my location",
    r"(?:forget|clear|remove|delete) my (?:location|city|zip(?: code)?)",
)
def forget_location(db):
    storage.delete_setting(db, "location")
    return "OK, I've forgotten your location. I won't check the weather until you set a new one."


@command(
    "weather",
    r"(?:(?:what's|whats|what is|how's|hows|how is|check|show me|get) )?(?:the )?"
    r"(?:weather|forecast)(?: like)?"
    r"(?: (?:in|for|at) (?!(?:today|tomorrow)$)(?P<place>.+?))?"
    r"(?: (?:for )?(?P<day>today|tomorrow))?(?: like)?",
    r"(?:is it going to|will it|is it gonna) (?:rain|snow)(?: (?P<day>today|tomorrow))?",
)
def show_weather(db, place=None, day=None):
    saved = saved_location(db)
    if not place and not saved:
        return HOW_TO

    def work():
        found = weather.find_place(place) if place else saved
        return forecast_text(found, day) if found else ""

    def finish(db, text, session):
        if text is None:
            return UNREACHABLE
        return text or f"I can't find a place called '{place}'."

    return Fetch(CHECKING, work, finish)

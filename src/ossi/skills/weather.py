"""Weather, and the saved location it's for."""

import json

from ossi import storage, weather
from ossi.references import ask_which
from ossi.registry import Ask, Fetch, command
from ossi.when import WEEKDAYS

CHECKING = "Checking the weather..."
UNREACHABLE = "I couldn't reach the weather service. Check your connection and try again."
HOW_TO = "Type 'set my location to' and your city or ZIP code, and I'll check the weather."
DAY = rf"today|tomorrow|{'|'.join(WEEKDAYS)}|week|weekend"
DAY_WORDS = r"(?:for |on |this |the )*"


def saved_location(db):
    value = storage.get_setting(db, "location")
    return json.loads(value) if value else None


def preferred_unit(db):
    return storage.get_setting(db, "temperature_unit")


def forecast_text(place, day=None, unit=None):
    """Fetch and describe a forecast. A chosen unit overrides the place's usual one."""
    place = {**place, "unit": unit} if unit else place
    return weather.describe(weather.get_forecast(place), place, day)


def not_found(place):
    return f"I can't find a place called '{place}'. Try a city and state, or a ZIP code."


def ask_location(db, text, session):
    return set_location(db, session, text)


def save_location(db, place, session=None):
    storage.set_setting(db, "location", json.dumps(place))
    return f"Your location is now {place['name']}."


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
            return not_found(place)
        if len(found) > 1:
            return ask_which(found, save_location)
        reply = save_location(db, found[0])
        gave_only_a_name = weather.readings(place) == [(place, "")]
        if gave_only_a_name and not place.isdigit():
            reply += " If that's the wrong one, add the state, like 'Springfield, IL'."
        return reply

    return Fetch("Looking that up...", lambda: weather.find_places(place), finish)


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
    "use celsius or use fahrenheit",
    r"(?:use|switch to|change to|show|show me) (?P<unit>celsius|fahrenheit)",
    r"(?:set|change) (?:the )?(?:units?|temperatures?) to (?P<unit>celsius|fahrenheit)",
)
def set_unit(db, unit):
    storage.set_setting(db, "temperature_unit", unit.lower())
    return f"OK, I'll show temperatures in {unit.capitalize()}."


@command(
    "weather",
    r"(?:(?:what's|whats|what is|how's|hows|how is|check|show me|get) )?(?:the )?"
    r"(?:weather|forecast)(?: like)?"
    rf"(?: (?:in|for|at) (?!{DAY_WORDS}(?:{DAY})$)(?P<place>.+?))?"
    rf"(?: (?:like )?{DAY_WORDS}(?P<day>{DAY}))?(?: like)?",
    rf"(?:is it going to|will it|is it gonna) (?:rain|snow)(?: {DAY_WORDS}(?P<day>{DAY}))?",
)
def show_weather(db, place=None, day=None):
    saved = saved_location(db)
    if not place and not saved:
        return HOW_TO
    unit = preferred_unit(db)

    def work():
        found = weather.find_places(place) if place else [saved]
        # A list of choices needs an answer before there's a forecast to get.
        return found, forecast_text(found[0], day, unit) if len(found) == 1 else None

    def finish(db, result, session):
        if result is None:
            return UNREACHABLE
        found, text = result
        if not found:
            return not_found(place)
        if len(found) > 1:
            return ask_which(found, after_choosing)
        return text

    def after_choosing(db, chosen, session):
        return Fetch(
            CHECKING,
            lambda: forecast_text(chosen, day, unit),
            lambda db, text, session: text or UNREACHABLE,
        )

    return Fetch(CHECKING, work, finish)

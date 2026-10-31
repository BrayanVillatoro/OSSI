"""Weather from Open-Meteo, a free service that needs no key.

This is the only part of OSSI that uses the internet. It sends the place being looked up,
and nothing else from the database. Callers must not run these functions on the window's
thread, because a slow connection would freeze it.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

from ossi.when import WEEKDAYS

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ZIP_URL = "https://api.zippopotam.us/us"
TIMEOUT_SECONDS = 8
PLACES_TO_COMPARE = 20
MOST_CHOICES = 5
FORECAST_DAYS = 7
# The biggest place wins outright when it's this many times bigger than the next one, so
# "Paris" doesn't ask about Texas, but "Springfield" asks which one.
CLEAR_WINNER = 10
SATURDAY = 5

COUNTRY_NICKNAMES = {"usa": "us", "uk": "gb", "america": "us"}

STATES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california",
    "co": "colorado", "ct": "connecticut", "de": "delaware", "dc": "district of columbia",
    "fl": "florida", "ga": "georgia", "hi": "hawaii", "id": "idaho", "il": "illinois",
    "in": "indiana", "ia": "iowa", "ks": "kansas", "ky": "kentucky", "la": "louisiana",
    "me": "maine", "md": "maryland", "ma": "massachusetts", "mi": "michigan",
    "mn": "minnesota", "ms": "mississippi", "mo": "missouri", "mt": "montana",
    "ne": "nebraska", "nv": "nevada", "nh": "new hampshire", "nj": "new jersey",
    "nm": "new mexico", "ny": "new york", "nc": "north carolina", "nd": "north dakota",
    "oh": "ohio", "ok": "oklahoma", "or": "oregon", "pa": "pennsylvania",
    "ri": "rhode island", "sc": "south carolina", "sd": "south dakota", "tn": "tennessee",
    "tx": "texas", "ut": "utah", "vt": "vermont", "va": "virginia", "wa": "washington",
    "wv": "west virginia", "wi": "wisconsin", "wy": "wyoming",
}  # fmt: skip

CONDITIONS = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy",
    45: "foggy", 48: "foggy",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow",
    80: "rain showers", 81: "rain showers", 82: "heavy rain showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail",
}  # fmt: skip
SNOW_CODES = {71, 73, 75, 77, 85, 86}


def get_json(url, params):
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": "OSSI"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.load(response)


def find_places(text):
    """Return the places a city or ZIP code could mean.

    One is a clear answer, none means nothing matched, and several means it's worth asking.
    """
    if text.isdigit() and len(text) == 5:
        return find_zip(text)
    for name, qualifier in readings(text):
        results = search(name)
        if qualifier:
            results = [result for result in results if matches_qualifier(result, qualifier)]
        if results:
            return choices(named(results, name))
    return []


def named(results, name):
    """Drop fuzzy matches, like a neighborhood of Paris when searching for Paris.

    Capitals stay so that "Washington" finds Washington D.C. A name spelled differently
    from every result, like "St Louis" for "St. Louis", keeps them all.
    """
    name = name.lower()
    close = [
        result
        for result in results
        if result["name"].lower() == name
        or (result.get("feature_code") == "PPLC" and result["name"].lower().startswith(name))
    ]
    return close or results


def readings(text):
    """The ways to split text into a name and a state or country, most likely first.

    The service only searches by name, so "Springfield, IL" gets narrowed down here.
    """
    if "," in text:
        name, _, qualifier = text.partition(",")
        return [(name.strip(), qualifier.strip().lower())]
    words = text.split()
    options = []
    for size in (2, 1):  # "Charleston West Virginia", then "Boston MA"
        ending = " ".join(words[-size:]).lower()
        if len(words) > size and (ending in STATES or ending in STATES.values()):
            options.append((" ".join(words[:-size]), ending))
    options.append((text, ""))
    if len(words) > 1:  # "Paris France"
        options.append((" ".join(words[:-1]), words[-1].lower()))
    return options


def search(name):
    data = get_json(
        GEOCODE_URL, {"name": name, "count": PLACES_TO_COMPARE, "language": "en", "format": "json"}
    )
    return data.get("results") or []


def find_zip(code):
    # A five-digit number is usually a US ZIP code, though other countries use them too.
    results = search(code)
    american = [result for result in results if result.get("country_code") == "US"]
    if american:
        return [to_place(american[0])]
    # Open-Meteo misses some small towns, so only those lookups go to a second service.
    place = find_zip_elsewhere(code)
    if place:
        return [place]
    return [to_place(results[0])] if results else []


def find_zip_elsewhere(code):
    try:
        data = get_json(f"{ZIP_URL}/{code}", {})
    except urllib.error.HTTPError as error:
        if error.code == 404:  # This is how the service says there's no such ZIP code.
            return None
        raise
    place = data["places"][0]
    return {
        "name": f"{place['place name']}, {place['state']}",
        "latitude": float(place["latitude"]),
        "longitude": float(place["longitude"]),
        "unit": "fahrenheit",
    }


def choices(results):
    """Pick the one place that's clearly meant, or else list the likely ones."""
    sized = [result for result in results if result.get("population")] or results
    sized = sorted(sized, key=lambda result: result.get("population") or 0, reverse=True)
    places, seen = [], set()
    for result in sized:
        place = to_place(result)
        if place["name"] not in seen:
            seen.add(place["name"])
            places.append((place, result.get("population") or 0))
    if len(places) == 1 or places[0][1] >= CLEAR_WINNER * places[1][1] > 0:
        return [places[0][0]]
    return [place for place, _ in places[:MOST_CHOICES]]


def matches_qualifier(result, qualifier):
    wanted = STATES.get(qualifier, COUNTRY_NICKNAMES.get(qualifier, qualifier))
    return wanted in (
        (result.get("admin1") or "").lower(),
        (result.get("country") or "").lower(),
        (result.get("country_code") or "").lower(),
    )


def to_place(result):
    country = result.get("country_code")
    region = result.get("admin1") if country == "US" else result.get("country")
    return {
        "name": ", ".join(part for part in (result["name"], region) if part),
        "latitude": result["latitude"],
        "longitude": result["longitude"],
        "unit": "fahrenheit" if country == "US" else "celsius",
    }


def get_forecast(place):
    return get_json(
        FORECAST_URL,
        {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,weather_code",
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
            ),
            "temperature_unit": place["unit"],
            "timezone": "auto",
            "forecast_days": FORECAST_DAYS,
        },
    )


def degrees(value, place):
    return f"{round(value)}°{'F' if place['unit'] == 'fahrenheit' else 'C'}"


def describe_day(forecast, place, index):
    """Put one day of a forecast into words: index 0 is today and 1 is tomorrow."""
    daily = forecast["daily"]
    code = daily["weather_code"][index]
    parts = [
        f"high {degrees(daily['temperature_2m_max'][index], place)}",
        f"low {degrees(daily['temperature_2m_min'][index], place)}",
    ]
    chance = daily["precipitation_probability_max"][index]
    if chance is not None:
        parts.append(f"{chance}% chance of {'snow' if code in SNOW_CODES else 'rain'}")
    return CONDITIONS.get(code, "mixed conditions"), ", ".join(parts)


def describe_today(forecast, place):
    current = forecast["current"]
    now = CONDITIONS.get(current["weather_code"], "mixed conditions")
    _, summary = describe_day(forecast, place, 0)
    return (
        f"Right now in {place['name']} it's {degrees(current['temperature_2m'], place)} "
        f"and {now}. Today: {summary}."
    )


def describe_tomorrow(forecast, place):
    conditions, summary = describe_day(forecast, place, 1)
    return f"Tomorrow in {place['name']}: {conditions}, {summary}."


def forecast_dates(forecast):
    return [date.fromisoformat(day) for day in forecast["daily"]["time"]]


def describe_weekday(forecast, place, weekday):
    number = WEEKDAYS.index(weekday)
    index = next(i for i, day in enumerate(forecast_dates(forecast)) if day.weekday() == number)
    conditions, summary = describe_day(forecast, place, index)
    return f"{weekday.capitalize()} in {place['name']}: {conditions}, {summary}."


def describe_days(forecast, place, indexes):
    dates = forecast_dates(forecast)
    lines = []
    for index in indexes:
        conditions, summary = describe_day(forecast, place, index)
        lines.append(f"{dates[index]:%A}: {conditions}, {summary}")
    return "\n".join([f"{place['name']}:", *lines])


def describe_week(forecast, place):
    return describe_days(forecast, place, range(len(forecast_dates(forecast))))


def describe_weekend(forecast, place):
    days = [i for i, day in enumerate(forecast_dates(forecast)) if day.weekday() >= SATURDAY]
    # On a Sunday, the next entry is the following Saturday, which isn't this weekend.
    if forecast_dates(forecast)[days[0]].weekday() != SATURDAY:
        days = days[:1]
    return describe_days(forecast, place, days[:2])


def describe(forecast, place, day=None):
    """Put the forecast for "tomorrow", "friday", "week" or "weekend" into words."""
    day = (day or "today").lower()
    if day == "today":
        return describe_today(forecast, place)
    if day == "tomorrow":
        return describe_tomorrow(forecast, place)
    if day == "week":
        return describe_week(forecast, place)
    if day == "weekend":
        return describe_weekend(forecast, place)
    return describe_weekday(forecast, place, day)

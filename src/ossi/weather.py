"""Weather from Open-Meteo, a free service that needs no key.

This is the only part of OSSI that uses the internet. It sends the place being looked up,
and nothing else from the database. Callers must not run these functions on the window's
thread, because a slow connection would freeze it.
"""

import json
import urllib.parse
import urllib.request

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_SECONDS = 8
PLACES_TO_COMPARE = 20

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


def find_place(text):
    """Look up a city or ZIP code. Returns an empty dict if nothing matches.

    The service only searches by name, so "Springfield, IL" is narrowed down here.
    """
    name, _, qualifier = text.partition(",")
    name, qualifier = name.strip(), qualifier.strip().lower()
    data = get_json(
        GEOCODE_URL, {"name": name, "count": PLACES_TO_COMPARE, "language": "en", "format": "json"}
    )
    results = data.get("results") or []
    if qualifier:
        results = [result for result in results if matches_qualifier(result, qualifier)]
    # A five-digit number is usually a US ZIP code, though other countries use them too.
    if name.isdigit() and len(name) == 5 and not qualifier:
        results.sort(key=lambda result: result.get("country_code") != "US")
    return to_place(results[0]) if results else {}


def matches_qualifier(result, qualifier):
    wanted = STATES.get(qualifier, qualifier)
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
            "forecast_days": 2,
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

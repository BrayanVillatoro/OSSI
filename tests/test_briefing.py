import gc
import json
import sys
import threading
import time
import tkinter as tk
import unittest
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ossi import autostart, gui, router, storage, weather
from ossi.skills import briefing

BOSTON = {
    "name": "Boston",
    "admin1": "Massachusetts",
    "country": "United States",
    "country_code": "US",
    "latitude": 42.36,
    "longitude": -71.06,
}
SPRINGFIELD_MO = {**BOSTON, "name": "Springfield", "admin1": "Missouri"}
SPRINGFIELD_IL = {**BOSTON, "name": "Springfield", "admin1": "Illinois"}
PARIS = {
    "name": "Paris",
    "admin1": "Île-de-France",
    "country": "France",
    "country_code": "FR",
    "latitude": 48.85,
    "longitude": 2.35,
}
FRENCH_POSTCODE = {**PARIS, "name": "Saint-Quentin"}
# Starts on a Saturday.
FORECAST = {
    "current": {"temperature_2m": 51.4, "weather_code": 2},
    "daily": {
        "time": [f"2026-1{day}" for day in (
            "0-31", "1-01", "1-02", "1-03", "1-04", "1-05", "1-06",
        )],
        "weather_code": [0, 73, 3, 3, 63, 1, 0],
        "temperature_2m_max": [54.4, 33.6, 40, 41, 42, 43, 44],
        "temperature_2m_min": [37.5, 28.2, 30, 31, 32, 33, 34],
        "precipitation_probability_max": [10, 80, 5, 6, 70, 8, 9],
    },
}  # fmt: skip
ZIP_REPLY = {
    "places": [
        {
            "place name": "Coeur D Alene",
            "state": "Idaho",
            "latitude": "47.6928",
            "longitude": "-116.785",
        }
    ]
}


def no_such_zip(url):
    return urllib.error.HTTPError(url, 404, "Not Found", None, None)


def fake_service(places=(BOSTON,), forecast=FORECAST, zip_reply=None):
    """Stands in for the weather service, so tests never use the internet."""

    def get_json(url, params):
        if url.startswith(weather.ZIP_URL):
            if zip_reply is None:
                raise no_such_zip(url)
            return zip_reply
        if url == weather.GEOCODE_URL:
            return {"results": list(places)} if places else {}
        return forecast

    return mock.patch.object(weather, "get_json", get_json)


def by_name_loosely(places):
    """A service that, like the real one, returns everything starting with the name."""

    def get_json(url, params):
        wanted = params["name"].lower()
        return {"results": [p for p in places if p["name"].lower().startswith(wanted)]}

    return mock.patch.object(weather, "get_json", get_json)


def by_name(places):
    """A service that, like the real one, only finds a place by its exact search name."""

    def get_json(url, params):
        if url == weather.GEOCODE_URL:
            return {"results": [p for p in places if p["name"].lower() == params["name"].lower()]}
        return FORECAST

    return mock.patch.object(weather, "get_json", get_json)


def offline():
    return mock.patch.object(weather, "get_json", side_effect=OSError("no connection"))


class Base(unittest.TestCase):
    def setUp(self):
        self.db = storage.connect(":memory:")
        self.addCleanup(self.db.close)
        self.session = router.Session()

    def say(self, text):
        return router.handle(self.db, text, self.session)

    def add_reminder(self, message, due):
        with self.db:
            self.db.execute(
                "INSERT INTO reminders (message, due_at) VALUES (?, ?)",
                (message, due.timestamp()),
            )


class FindPlacesTest(unittest.TestCase):
    def names(self, text):
        return [place["name"] for place in weather.find_places(text)]

    def test_uses_the_right_unit(self):
        with fake_service():
            self.assertEqual(weather.find_places("Boston")[0]["unit"], "fahrenheit")
        with fake_service([PARIS]):
            place = weather.find_places("Paris")[0]
        self.assertEqual(place["name"], "Paris, France")
        self.assertEqual(place["unit"], "celsius")

    def test_nothing_found_is_an_empty_list(self):
        with fake_service(()):
            self.assertEqual(weather.find_places("Nowhereville"), [])

    def test_a_much_bigger_place_wins_outright(self):
        big = {**PARIS, "population": 2138551}
        small = {**BOSTON, "name": "Paris", "admin1": "Texas", "population": 24782}
        with fake_service([small, big]):
            self.assertEqual(self.names("Paris"), ["Paris, France"])

    def test_similar_sizes_become_choices_biggest_first(self):
        options = [
            {**SPRINGFIELD_IL, "population": 114394},
            {**SPRINGFIELD_MO, "population": 170188},
        ]
        with fake_service(options):
            self.assertEqual(
                self.names("Springfield"), ["Springfield, Missouri", "Springfield, Illinois"]
            )

    def test_fuzzy_matches_are_dropped(self):
        paris = {**PARIS, "population": 2138551, "feature_code": "PPLC"}
        district = {**PARIS, "name": "Paris 15 Vaugirard", "population": 229713}
        with by_name_loosely([district, paris]):
            self.assertEqual(self.names("Paris France"), ["Paris, France"])
        other = {**BOSTON, "name": "Blue Island", "admin1": "Illinois", "population": 23652}
        portland = {**BOSTON, "name": "Portland", "admin1": "Oregon", "population": 652503}
        maine = {**BOSTON, "name": "Portland", "admin1": "Maine", "population": 66881}
        with fake_service([portland, maine, other]):
            self.assertEqual(self.names("Portland"), ["Portland, Oregon", "Portland, Maine"])

    def test_a_capital_can_have_a_longer_name(self):
        capital = {
            **BOSTON,
            "name": "Washington D.C.",
            "admin1": "District of Columbia",
            "feature_code": "PPLC",
            "population": 689545,
        }
        town = {**BOSTON, "name": "Washington", "admin1": "Utah", "population": 24299}
        with fake_service([town, capital]):
            self.assertEqual(self.names("Washington"), ["Washington D.C., District of Columbia"])

    def test_a_different_spelling_keeps_every_result(self):
        city = {**BOSTON, "name": "St. Louis", "admin1": "Missouri"}
        with fake_service([city]):
            self.assertEqual(self.names("St Louis"), ["St. Louis, Missouri"])

    def test_places_with_no_population_are_ignored_when_others_have_one(self):
        park = {**BOSTON, "name": "Coeur D'Alene Park"}
        town = {**BOSTON, "name": "Coeur d'Alene", "admin1": "Idaho", "population": 49122}
        with fake_service([town, park]):
            self.assertEqual(self.names("Coeur d'Alene"), ["Coeur d'Alene, Idaho"])

    def test_the_same_name_only_appears_once(self):
        with fake_service([BOSTON, BOSTON]):
            self.assertEqual(self.names("Boston"), ["Boston, Massachusetts"])

    def test_a_state_narrows_it_down_with_or_without_a_comma(self):
        options = [SPRINGFIELD_MO, SPRINGFIELD_IL]
        for text in ("Springfield, IL", "springfield, illinois", "Springfield IL",
                     "Springfield Illinois"):  # fmt: skip
            with by_name(options):
                self.assertEqual(self.names(text), ["Springfield, Illinois"], text)

    def test_two_word_state_without_a_comma(self):
        charleston = {**BOSTON, "name": "Charleston", "admin1": "West Virginia"}
        with by_name([charleston]):
            self.assertEqual(self.names("Charleston West Virginia"), ["Charleston, West Virginia"])

    def test_a_country_without_a_comma(self):
        with by_name([PARIS]):
            self.assertEqual(self.names("Paris France"), ["Paris, France"])

    def test_country_nicknames(self):
        london = {**PARIS, "name": "London", "country": "United Kingdom", "country_code": "GB"}
        with by_name([london]):
            self.assertEqual(self.names("London, UK"), ["London, United Kingdom"])

    def test_a_name_ending_in_a_state_code_still_works_as_a_whole(self):
        place = {**BOSTON, "name": "Mount Me"}
        with by_name([place]):
            self.assertEqual(self.names("Mount Me"), ["Mount Me, Massachusetts"])

    def test_a_state_with_no_match_finds_nothing(self):
        with fake_service([SPRINGFIELD_MO]):
            self.assertEqual(weather.find_places("Springfield, IL"), [])

    def test_a_zip_code_prefers_the_united_states(self):
        with fake_service([FRENCH_POSTCODE, BOSTON]):
            self.assertEqual(self.names("02108"), ["Boston, Massachusetts"])

    def test_a_zip_code_open_meteo_misses_goes_to_the_second_service(self):
        with fake_service((), zip_reply=ZIP_REPLY):
            place = weather.find_places("83814")[0]
        self.assertEqual(place["name"], "Coeur D Alene, Idaho")
        self.assertEqual(place["latitude"], 47.6928)
        self.assertEqual(place["unit"], "fahrenheit")

    def test_second_service_is_not_asked_when_open_meteo_knows_the_zip(self):
        calls = []

        def get_json(url, params):
            calls.append(url)
            return {"results": [BOSTON]}

        with mock.patch.object(weather, "get_json", get_json):
            weather.find_places("02108")
        self.assertEqual(calls, [weather.GEOCODE_URL])

    def test_a_zip_code_that_does_not_exist_finds_nothing(self):
        with fake_service(()):
            self.assertEqual(weather.find_places("00000"), [])

    def test_a_foreign_postcode_is_still_found(self):
        with fake_service([FRENCH_POSTCODE]):
            self.assertEqual(self.names("02100"), ["Saint-Quentin, France"])

    def test_the_second_service_being_down_is_a_failure_not_a_missing_zip(self):
        def get_json(url, params):
            if url.startswith(weather.ZIP_URL):
                raise urllib.error.URLError("down")
            return {}

        with mock.patch.object(weather, "get_json", get_json):
            with self.assertRaises(urllib.error.URLError):
                weather.find_places("83814")


class ChoosingAPlaceTest(Base):
    OPTIONS = [
        {**SPRINGFIELD_MO, "population": 170188},
        {**SPRINGFIELD_IL, "population": 114394},
    ]

    def test_asks_then_accepts_a_number_a_position_or_a_name(self):
        for answer in ("2", "the second one", "illinois"):
            with fake_service(self.OPTIONS):
                self.assertIn("Which one?", self.say("set my location to Springfield"))
                self.assertIn("Springfield, Illinois", self.say(answer), answer)

    def test_asks_again_after_an_answer_it_does_not_understand(self):
        with fake_service(self.OPTIONS):
            self.say("set my location to Springfield")
            self.assertIn("didn't catch", self.say("the purple one"))
            self.assertIn("Springfield, Missouri", self.say("1"))

    def test_never_mind_saves_nothing(self):
        with fake_service(self.OPTIONS):
            self.say("set my location to Springfield")
            self.assertIn("never mind", self.say("never mind"))
        self.assertIsNone(storage.get_setting(self.db, "location"))

    def test_weather_somewhere_ambiguous_asks_then_keeps_the_day(self):
        with fake_service(self.OPTIONS):
            self.assertIn("Which one?", self.say("weather in Springfield tomorrow"))
            self.assertIn("Tomorrow in Springfield, Illinois", self.say("2"))
        self.assertIsNone(storage.get_setting(self.db, "location"))


class UnitTest(Base):
    def setUp(self):
        super().setUp()
        with fake_service():
            self.say("set my location to Boston")

    def test_a_chosen_unit_is_requested_from_the_service_and_shown(self):
        seen = []

        def get_json(url, params):
            seen.append(params["temperature_unit"])
            return FORECAST

        self.assertIn("Celsius", self.say("use celsius"))
        with mock.patch.object(weather, "get_json", get_json):
            self.assertIn("51°C", self.say("weather"))
            self.assertIn("°C", self.say("briefing"))
        self.assertEqual(seen, ["celsius", "celsius"])

    def test_switching_back(self):
        self.say("use celsius")
        self.say("show fahrenheit")
        with fake_service():
            self.assertIn("51°F", self.say("weather"))

    def test_applies_to_other_places_too(self):
        self.say("use fahrenheit")
        with fake_service([PARIS]):
            self.assertIn("51°F", self.say("weather in Paris"))

    def test_misspelling_is_corrected(self):
        self.assertIn("Did you mean: use celsius", self.say("use celcius"))


class WeatherWordsTest(unittest.TestCase):
    PLACE = {"name": "Boston, Massachusetts", "unit": "fahrenheit"}

    def test_today(self):
        self.assertEqual(
            weather.describe_today(FORECAST, self.PLACE),
            "Right now in Boston, Massachusetts it's 51°F and partly cloudy. "
            "Today: high 54°F, low 38°F, 10% chance of rain.",
        )

    def test_tomorrow_with_snow(self):
        self.assertEqual(
            weather.describe_tomorrow(FORECAST, self.PLACE),
            "Tomorrow in Boston, Massachusetts: snow, high 34°F, low 28°F, 80% chance of snow.",
        )

    def test_celsius(self):
        place = {"name": "Paris, France", "unit": "celsius"}
        self.assertIn("51°C", weather.describe_today(FORECAST, place))

    def test_a_named_day(self):
        self.assertEqual(
            weather.describe(FORECAST, self.PLACE, "wednesday"),
            "Wednesday in Boston, Massachusetts: rain, high 42°F, low 32°F, 70% chance of rain.",
        )

    def test_a_named_day_that_is_today_uses_todays_entry(self):
        self.assertIn("high 54°F", weather.describe(FORECAST, self.PLACE, "saturday"))

    def test_the_whole_week(self):
        lines = weather.describe(FORECAST, self.PLACE, "week").split("\n")
        self.assertEqual(lines[0], "Boston, Massachusetts:")
        self.assertEqual(len(lines), 8)
        self.assertTrue(lines[1].startswith("Saturday: clear"))
        self.assertTrue(lines[7].startswith("Friday: clear"))

    def test_the_weekend_starting_on_a_saturday(self):
        lines = weather.describe(FORECAST, self.PLACE, "weekend").split("\n")
        self.assertEqual([line.split(":")[0] for line in lines[1:]], ["Saturday", "Sunday"])

    def test_the_weekend_from_a_weekday_looks_ahead(self):
        daily = {**FORECAST["daily"], "time": [
            "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05",
            "2026-11-06", "2026-11-07", "2026-11-08",
        ]}  # fmt: skip
        lines = weather.describe({**FORECAST, "daily": daily}, self.PLACE, "weekend").split("\n")
        self.assertEqual([line.split(":")[0] for line in lines[1:]], ["Saturday", "Sunday"])

    def test_the_weekend_on_a_sunday_is_just_today(self):
        daily = {**FORECAST["daily"], "time": [
            "2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04",
            "2026-11-05", "2026-11-06", "2026-11-07",
        ]}  # fmt: skip
        lines = weather.describe({**FORECAST, "daily": daily}, self.PLACE, "weekend").split("\n")
        self.assertEqual([line.split(":")[0] for line in lines[1:]], ["Sunday"])

    def test_missing_chance_and_unknown_code_still_read_well(self):
        daily = {**FORECAST["daily"], "precipitation_probability_max": [None, None]}
        forecast = {
            "current": {"temperature_2m": 50, "weather_code": 1234},
            "daily": daily,
        }
        text = weather.describe_today(forecast, self.PLACE)
        self.assertIn("mixed conditions", text)
        self.assertNotIn("None", text)
        self.assertNotIn("chance", text)


class LocationTest(Base):
    def test_setting_saves_it_and_says_where(self):
        with fake_service():
            reply = self.say("set my location to Boston")
        self.assertIn("Boston, Massachusetts", reply)
        self.assertIn("Boston", self.say("what's my location"))

    def test_only_a_bare_city_name_gets_the_hint_about_adding_a_state(self):
        with fake_service():
            self.assertIn("add the state", self.say("set my location to Boston"))
            self.assertNotIn("add the state", self.say("set my location to Boston, MA"))
            self.assertNotIn("add the state", self.say("set my location to Boston MA"))
            self.assertNotIn("add the state", self.say("set my location to 02108"))

    def test_other_ways_of_saying_it(self):
        for text in ("my city is Boston", "i live in Boston", "change my zip code to 02108"):
            with fake_service():
                self.assertIn("Your location is now", self.say(text))

    def test_asks_when_no_place_is_given(self):
        self.assertIn("What city or ZIP", self.say("set my location"))
        with fake_service():
            self.assertIn("Boston, Massachusetts", self.say("Boston"))

    def test_a_place_that_does_not_exist_is_not_saved(self):
        with fake_service(()):
            self.assertIn("can't find", self.say("set my location to Nowhereville"))
        self.assertIsNone(storage.get_setting(self.db, "location"))

    def test_no_connection_keeps_the_old_location(self):
        with fake_service():
            self.say("set my location to Boston")
        with offline():
            self.assertIn("couldn't reach", self.say("set my location to Paris"))
        self.assertIn("Boston", self.say("what's my location"))

    def test_forgetting(self):
        with fake_service():
            self.say("set my location to Boston")
        self.assertIn("forgotten", self.say("forget my location"))
        self.assertIsNone(storage.get_setting(self.db, "location"))


class WeatherCommandTest(Base):
    def test_never_uses_the_internet_without_a_location(self):
        with mock.patch.object(weather, "get_json") as get_json:
            self.assertIn("set my location", self.say("weather"))
            self.say("briefing")
        get_json.assert_not_called()

    def test_ways_of_asking(self):
        with fake_service():
            self.say("set my location to Boston")
            for text in (
                "weather",
                "what's the weather",
                "what's the weather like",
                "how's the weather today",
                "hey ossi what's the weather please",
                "forecast",
            ):
                self.assertIn("Right now in Boston", self.say(text), text)

    def test_tomorrow(self):
        with fake_service():
            self.say("set my location to Boston")
            for text in (
                "weather tomorrow",
                "weather for tomorrow",
                "what's the weather like tomorrow",
                "will it snow tomorrow",
            ):
                self.assertIn("Tomorrow in Boston", self.say(text), text)

    def test_other_days(self):
        with fake_service():
            self.say("set my location to Boston")
            for text in ("weather friday", "what's the weather on friday",
                         "will it rain this friday", "weather for friday"):  # fmt: skip
                self.assertIn("Friday in Boston", self.say(text), text)
            for text in ("weather this week", "what's the forecast for the week"):
                self.assertIn("Friday:", self.say(text), text)
            for text in ("weather this weekend", "will it rain this weekend"):
                self.assertIn("Sunday:", self.say(text), text)
        with fake_service([PARIS]):
            self.assertIn("Friday in Paris", self.say("weather in Paris on friday"))
            self.assertIn("Paris, France:", self.say("what's the weather in Paris this week"))

    def test_another_place_does_not_change_the_saved_one(self):
        with fake_service():
            self.say("set my location to Boston")
        with fake_service([PARIS]):
            self.assertIn("in Paris, France", self.say("what's the weather in Paris"))
            self.assertIn("Tomorrow in Paris", self.say("weather in Paris tomorrow"))
        self.assertIn("Boston", self.say("what's my location"))

    def test_another_place_works_with_no_saved_location(self):
        with fake_service([PARIS]):
            self.assertIn("in Paris", self.say("weather in Paris"))
        self.assertIsNone(storage.get_setting(self.db, "location"))

    def test_unknown_place(self):
        with fake_service(()):
            self.assertIn("can't find a place", self.say("weather in Nowhereville"))

    def test_no_connection_says_so(self):
        with fake_service():
            self.say("set my location to Boston")
        with offline():
            self.assertIn("couldn't reach", self.say("weather"))

    def test_a_garbled_response_says_so(self):
        with fake_service():
            self.say("set my location to Boston")
        with fake_service(forecast={"unexpected": True}):
            self.assertIn("couldn't reach", self.say("weather"))

    def test_typos_are_understood(self):
        reply = self.say("wether")
        self.assertIn("Did you mean: weather", reply)


class SkillOrderTest(Base):
    def test_broad_task_patterns_do_not_swallow_other_skills(self):
        self.assertEqual(self.say("remember that the laundry is done"), "Saved.")
        self.assertIn("No notes mention", self.say("find note rent is done"))
        self.assertIn("When should I remind you", self.say("remind me the laundry is done"))
        self.say("never mind")
        self.assertIn("No upcoming", self.say("reminders"))

    def test_task_number_is_done_still_completes_it(self):
        self.say("add task buy milk")
        self.assertEqual(self.say("task 1 is done"), "Completed task 1: buy milk")


class BriefingTest(Base):
    def test_empty_day(self):
        reply = self.say("briefing")
        self.assertIn("No reminders today.", reply)
        self.assertIn("No open tasks.", reply)

    def test_greeting_follows_the_clock(self):
        self.assertIn("Good morning", briefing.greeting(datetime(2026, 11, 2, 8)))
        self.assertIn("Good afternoon", briefing.greeting(datetime(2026, 11, 2, 13)))
        self.assertIn("Good evening", briefing.greeting(datetime(2026, 11, 2, 19)))
        self.assertIn("Monday, November 2", briefing.greeting(datetime(2026, 11, 2, 8)))

    def test_includes_todays_reminders_but_not_later_ones(self):
        now = datetime(2026, 11, 2, 8)
        self.add_reminder("call the bank", datetime(2026, 11, 2, 15))
        self.add_reminder("pay rent", datetime(2026, 11, 3, 15))
        reply = briefing.compose(self.db, self.session, None, None, now)
        self.assertIn("3:00 PM: call the bank", reply)
        self.assertNotIn("pay rent", reply)

    def test_with_nothing_today_it_mentions_the_next_reminder(self):
        now = datetime(2026, 11, 2, 8)
        self.add_reminder("pay rent", datetime(2026, 11, 3, 15))
        reply = briefing.compose(self.db, self.session, None, None, now)
        self.assertIn("No reminders today. Next: tomorrow at 3:00 PM: pay rent", reply)

    def test_a_long_task_list_is_shortened(self):
        for number in range(8):
            self.say(f"add task job {number}")
        reply = self.say("briefing")
        self.assertIn("Open tasks (8):", reply)
        self.assertIn("job 4", reply)
        self.assertNotIn("job 5", reply)
        self.assertIn("and 3 more", reply)

    def test_includes_the_weather(self):
        with fake_service():
            self.say("set my location to Boston")
            self.assertIn("Right now in Boston", self.say("good morning"))

    def test_still_works_with_no_connection(self):
        self.say("add task buy milk")
        with fake_service():
            self.say("set my location to Boston")
        with offline():
            reply = self.say("briefing")
        self.assertIn("couldn't reach the weather", reply)
        self.assertIn("buy milk", reply)

    def test_follow_ups_work_after_a_briefing(self):
        self.say("add task call the bank")
        self.say("add task buy milk")
        self.say("briefing")
        self.assertEqual(self.say("finish the second one"), "Completed task 2: buy milk")

    def test_weather_hint_only_appears_once(self):
        self.assertIn("set my location", self.say("briefing"))
        self.assertNotIn("set my location", self.say("briefing"))

    def test_other_ways_of_asking(self):
        for text in (
            "daily briefing",
            "give me my briefing",
            "what's my day look like",
            "what does today look like",
            "what's on today",
            "what do i have today",
        ):
            self.assertIn("No open tasks", self.say(text), text)


class WhenBriefingRunsTest(Base):
    MORNING = datetime(2026, 11, 2, 8)

    def test_runs_once_a_day(self):
        self.assertTrue(briefing.is_due(self.db, self.MORNING))
        briefing.mark_given(self.db, self.MORNING)
        self.assertFalse(briefing.is_due(self.db, self.MORNING + timedelta(hours=4)))
        self.assertTrue(briefing.is_due(self.db, self.MORNING + timedelta(days=1)))

    def test_waits_until_morning(self):
        self.assertFalse(briefing.is_due(self.db, datetime(2026, 11, 2, 0, 1)))
        self.assertTrue(briefing.is_due(self.db, datetime(2026, 11, 2, 5)))

    def test_can_be_turned_off_and_on(self):
        self.say("turn off the daily briefing")
        self.assertFalse(briefing.is_due(self.db, self.MORNING))
        self.say("turn on the daily briefing")
        self.assertTrue(briefing.is_due(self.db, self.MORNING))


class LoginOfferTest(Base):
    def setUp(self):
        super().setUp()
        for patcher in (
            mock.patch.object(autostart, "supported", lambda: True),
            mock.patch.object(autostart, "is_enabled", lambda: False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        # Replacing enable means no test here can change the real login settings.
        enable = mock.patch.object(autostart, "enable")
        self.enable = enable.start()
        self.addCleanup(enable.stop)

    def daily(self):
        return router.render(
            self.db, briefing.give_briefing(self.db, self.session, automatic=True), self.session
        )

    def test_yes_turns_it_on(self):
        self.assertIn("Want it to start when you log in", self.daily())
        self.enable.assert_not_called()
        self.assertIn("will start", self.say("yes"))
        self.enable.assert_called_once()

    def test_no_leaves_it_off(self):
        self.daily()
        self.assertIn("leave that off", self.say("no"))
        self.enable.assert_not_called()

    def test_any_other_command_leaves_it_off(self):
        self.daily()
        self.assertEqual(self.say("add task buy milk"), "Added task 1: buy milk")
        self.say("yes")
        self.enable.assert_not_called()

    def test_only_asks_once(self):
        self.assertIn("log in", self.daily())
        self.say("no")
        self.assertNotIn("log in", self.daily())

    def test_a_briefing_someone_asked_for_does_not_ask(self):
        self.assertNotIn("log in", self.say("briefing"))
        self.assertIn("log in", self.daily())

    def test_does_not_ask_when_already_on(self):
        with mock.patch.object(autostart, "is_enabled", lambda: True):
            self.assertNotIn("log in", self.daily())

    def test_asks_after_the_weather_when_there_is_a_location(self):
        with fake_service():
            self.say("set my location to Boston")
            reply = self.daily()
        self.assertIn("Right now in Boston", reply)
        self.assertTrue(reply.endswith("(yes or no)"))


class WindowTest(unittest.TestCase):
    def setUp(self):
        # Other tests' closed windows must be freed here, not on the worker thread, which
        # would crash Tcl. The running app only ever has the one window.
        gc.collect()
        self.addCleanup(gc.collect)
        self.db = storage.connect(":memory:")
        self.addCleanup(self.db.close)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"no display: {error}")
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        self.app = gui.App(self.root, self.db, notify=lambda title, body: None)

    def transcript(self):
        return self.app.log.get("1.0", "end")

    def wait_for_fetch(self):
        deadline = time.time() + 5
        while self.app.fetched.empty() and time.time() < deadline:
            time.sleep(0.01)
        self.app.check_fetches()

    def set_location(self):
        storage.set_setting(
            self.db,
            "location",
            json.dumps({"name": "Boston", "latitude": 1, "longitude": 2, "unit": "fahrenheit"}),
        )

    def test_network_work_happens_on_another_thread(self):
        self.set_location()
        threads = []

        def get_json(url, params):
            threads.append(threading.current_thread())
            return FORECAST

        with mock.patch.object(weather, "get_json", get_json):
            self.app.run("weather")
            self.assertIn("Checking the weather", self.transcript())
            self.wait_for_fetch()

        self.assertIsNot(threads[0], threading.main_thread())
        self.assertIn("Right now in Boston", self.transcript())

    def test_no_connection_shows_a_message_instead_of_hanging(self):
        self.set_location()
        with offline():
            self.app.run("weather")
            self.wait_for_fetch()
        self.assertIn("couldn't reach", self.transcript())

    def test_automatic_briefing_runs_once_while_the_window_is_in_use(self):
        with mock.patch.object(self.app, "is_in_use", return_value=True):
            self.app.check_briefing()
            self.app.check_briefing()
        self.assertEqual(self.transcript().count("No open tasks"), 1)

    def test_a_hidden_window_waits_to_brief(self):
        self.app.check_briefing()
        self.assertNotIn("No open tasks", self.transcript())
        self.assertTrue(briefing.is_due(self.db, datetime(2026, 11, 2, 8)))


class MigrationTest(unittest.TestCase):
    def test_settings_start_empty_and_can_change(self):
        db = storage.connect(":memory:")
        self.addCleanup(db.close)
        self.assertIsNone(storage.get_setting(db, "a"))
        storage.set_setting(db, "a", "1")
        storage.set_setting(db, "a", "2")
        self.assertEqual(storage.get_setting(db, "a"), "2")
        storage.delete_setting(db, "a")
        self.assertIsNone(storage.get_setting(db, "a"))


if __name__ == "__main__":
    unittest.main()

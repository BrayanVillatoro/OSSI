import gc
import json
import sys
import threading
import time
import tkinter as tk
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ossi import gui, router, storage, weather
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
FORECAST = {
    "current": {"temperature_2m": 51.4, "weather_code": 2},
    "daily": {
        "weather_code": [0, 73],
        "temperature_2m_max": [54.4, 33.6],
        "temperature_2m_min": [37.5, 28.2],
        "precipitation_probability_max": [10, 80],
    },
}


def fake_service(places=(BOSTON,), forecast=FORECAST):
    """Stands in for the weather service, so tests never use the internet."""

    def get_json(url, params):
        if url == weather.GEOCODE_URL:
            return {"results": list(places)} if places else {}
        return forecast

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


class FindPlaceTest(unittest.TestCase):
    def test_uses_the_top_result_and_the_right_unit(self):
        with fake_service():
            place = weather.find_place("Boston")
        self.assertEqual(place["name"], "Boston, Massachusetts")
        self.assertEqual(place["unit"], "fahrenheit")

    def test_other_countries_use_celsius(self):
        with fake_service([PARIS]):
            place = weather.find_place("Paris")
        self.assertEqual(place["name"], "Paris, France")
        self.assertEqual(place["unit"], "celsius")

    def test_nothing_found_is_an_empty_result(self):
        with fake_service(()):
            self.assertEqual(weather.find_place("Nowhereville"), {})

    def test_a_state_narrows_it_down(self):
        options = [SPRINGFIELD_MO, SPRINGFIELD_IL]
        for text in ("Springfield, IL", "springfield, illinois"):
            with fake_service(options):
                self.assertEqual(weather.find_place(text)["name"], "Springfield, Illinois")

    def test_a_state_with_no_match_finds_nothing(self):
        with fake_service([SPRINGFIELD_MO]):
            self.assertEqual(weather.find_place("Springfield, IL"), {})

    def test_only_the_name_is_searched(self):
        seen = {}

        def get_json(url, params):
            seen.update(params)
            return {"results": [SPRINGFIELD_IL]}

        with mock.patch.object(weather, "get_json", get_json):
            weather.find_place("Springfield, IL")
        self.assertEqual(seen["name"], "Springfield")

    def test_a_zip_code_prefers_the_united_states(self):
        with fake_service([FRENCH_POSTCODE, BOSTON]):
            self.assertEqual(weather.find_place("02108")["name"], "Boston, Massachusetts")

    def test_a_city_keeps_the_services_order(self):
        with fake_service([PARIS, BOSTON]):
            self.assertEqual(weather.find_place("Paris")["name"], "Paris, France")


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

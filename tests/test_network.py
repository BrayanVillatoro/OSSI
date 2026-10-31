import gc
import ipaddress
import sys
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ossi import gui, network, router, storage
from ossi.network import Status
from ossi.skills import ai
from ossi.skills import network as skill

ROUTES = """
IPv4 Route Table
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0     192.168.1.1     192.168.1.20     50
          0.0.0.0          0.0.0.0    192.168.86.1    192.168.86.39     25
"""
ARP = """
Interface: 192.168.86.39 --- 0x8
  Internet Address      Physical Address      Type
  192.168.86.1          AA-BB-CC-00-00-01     dynamic
  192.168.86.27         aa-bb-cc-00-00-02     dynamic
  192.168.86.255        ff-ff-ff-ff-ff-ff     static
  224.0.0.22            01-00-5e-00-00-16     static

Interface: 10.0.0.5 --- 0x9
  10.0.0.9              aa-bb-cc-00-00-03     dynamic
"""
HEALTHY = Status(True, 1, 20)
NO_INTERNET = Status(True, 1, None)
NO_ROUTER = Status(True, None, None)
OFFLINE = Status(False)
SLOW = Status(True, 1, 900)


def device(mac, address="192.168.86.50", name="", is_router=False):
    return {"address": address, "mac": mac, "name": name, "is_router": is_router}


class ParsingTest(unittest.TestCase):
    def test_gateway_uses_the_connection_windows_prefers(self):
        self.assertEqual(network.parse_gateway(ROUTES), "192.168.86.1")

    def test_no_gateway(self):
        self.assertIsNone(network.parse_gateway("IPv4 Route Table\nActive Routes:\nNone\n"))

    def test_a_reply_gives_its_time(self):
        reply = "Reply from 1.1.1.1: bytes=32 time=14ms TTL=57"
        self.assertEqual(network.parse_ping(reply), 14)

    def test_under_a_millisecond(self):
        self.assertEqual(network.parse_ping("Reply from 1.1.1.1: bytes=32 time<1ms TTL=64"), 1)

    def test_host_unreachable_is_not_a_reply(self):
        # Windows reports success for this, so the exit code can't be trusted.
        self.assertIsNone(network.parse_ping("Reply from 192.168.86.39: Destination host unreachable."))

    def test_timed_out(self):
        self.assertIsNone(network.parse_ping("Request timed out."))

    def test_other_languages_still_count_as_a_reply(self):
        self.assertEqual(network.parse_ping("Antwort von 1.1.1.1: Bytes=32 Zeit=9ms TTL=57"), 9)

    def test_arp_keeps_only_real_devices_on_this_network(self):
        found = network.parse_arp(ARP, ipaddress.ip_network("192.168.86.0/24"))
        self.assertEqual(
            found, {"192.168.86.1": "aa-bb-cc-00-00-01", "192.168.86.27": "aa-bb-cc-00-00-02"}
        )


class CheckTest(unittest.TestCase):
    def test_no_route_means_not_connected(self):
        with mock.patch.object(network, "gateway", return_value=None), mock.patch.object(
            network, "ping"
        ) as ping:
            self.assertFalse(network.check().connected)
        ping.assert_not_called()

    def test_one_public_server_being_down_is_not_an_outage(self):
        answers = {"192.168.86.1": 1, "1.1.1.1": None, "8.8.8.8": 15}
        with mock.patch.object(network, "gateway", return_value="192.168.86.1"), mock.patch.object(
            network, "ping", lambda host: answers[host]
        ):
            self.assertEqual(network.check(), Status(True, 1, 15))

    def test_a_network_that_is_not_private_is_never_scanned(self):
        with mock.patch.object(network, "gateway", return_value="8.8.8.1"), mock.patch.object(
            network, "ping"
        ) as ping:
            self.assertIsNone(network.find_devices())
        ping.assert_not_called()


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.watch = skill.Watch()

    def texts(self, status, now=0):
        return [message for _, message in self.watch.update(status, now)]

    def test_a_healthy_network_says_nothing(self):
        self.assertEqual(self.texts(HEALTHY), [])
        self.assertEqual(self.texts(HEALTHY), [])

    def test_one_missed_check_says_nothing(self):
        self.assertEqual(self.texts(NO_INTERNET), [])
        self.assertEqual(self.texts(HEALTHY), [])

    def test_an_outage_is_reported_once(self):
        self.texts(NO_INTERNET)
        self.assertIn("internet provider", self.texts(NO_INTERNET)[0])
        self.assertEqual(self.texts(NO_INTERNET), [])

    def test_a_dead_router_gets_different_advice(self):
        self.texts(NO_ROUTER)
        self.assertIn("router isn't answering", self.texts(NO_ROUTER)[0])

    def test_recovery_says_how_long_it_was_out(self):
        self.texts(NO_INTERNET, now=0)
        self.texts(NO_INTERNET, now=60)
        self.assertIn("about 5 minutes", self.texts(HEALTHY, now=360)[0])

    def test_getting_worse_is_reported_but_the_clock_keeps_running(self):
        self.texts(NO_INTERNET, now=0)
        self.texts(NO_INTERNET, now=60)
        self.texts(NO_ROUTER, now=120)
        self.assertIn("router", self.texts(NO_ROUTER, now=180)[0])
        self.assertIn("about 5 minutes", self.texts(HEALTHY, now=360)[0])

    def test_being_slow_takes_several_checks_and_is_reported_once(self):
        self.assertEqual(self.texts(SLOW), [])
        self.assertEqual(self.texts(SLOW), [])
        self.assertIn("slow", self.texts(SLOW)[0])
        self.assertEqual(self.texts(SLOW), [])

    def test_slow_can_be_reported_again_after_recovering(self):
        for _ in range(3):
            self.texts(SLOW)
        self.texts(HEALTHY)
        for _ in range(2):
            self.assertEqual(self.texts(SLOW), [])
        self.assertIn("slow", self.texts(SLOW)[0])


class HowLongTest(unittest.TestCase):
    def test_wording(self):
        self.assertEqual(skill.how_long(70), "about a minute")
        self.assertEqual(skill.how_long(600), "about 10 minutes")
        self.assertEqual(skill.how_long(3 * 3600), "about 3 hours")


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.db = storage.connect(":memory:")
        self.addCleanup(self.db.close)

    def say(self, text):
        return router.handle(self.db, text)

    def status_is(self, status):
        return mock.patch.object(network, "check", return_value=status)

    def test_status_phrases(self):
        with self.status_is(HEALTHY):
            for text in ("network status", "is my internet working", "how's my wifi", "is the internet down"):
                self.assertIn("working", self.say(text), text)

    def test_many_ways_of_asking_for_status(self):
        phrases = (
            "is my internet working?",
            "is the wifi down",
            "hows my internet",
            "hows the wifi doing today",
            "check my internet",
            "test my connection",
            "internet status",
            "wifi speed",
            "my internet is down",
            "my wifi is not working",
            "the wifi isnt working",
            "my internet is slow",
            "why is my internet slow",
            "do i have internet",
            "am i online",
            "hey ossi, is my internet ok right now please",
        )
        with self.status_is(HEALTHY):
            for text in phrases:
                self.assertIn("working", self.say(text), text)

    def test_many_ways_of_asking_for_devices(self):
        phrases = (
            "what's on my network",
            "whats on my network",
            "Whats on my network?",
            "what is on my wifi",
            "whats connected to my wifi",
            "whos on my wifi",
            "who is connected to the network",
            "what devices are on my network",
            "which devices are connected to my wifi",
            "how many devices are on my network",
            "anyone on my wifi",
            "show my network",
            "show me my network",
            "show devices on my network",
            "show me all the devices",
            "list my devices",
            "list devices on the wifi",
            "scan my network",
            "show me whats on my wifi",
            "tell me whats on my network",
            "network devices",
            "my wifi devices",
            "connected devices",
            "whats connected",
            "can you show me whats on my network please",
        )
        with mock.patch.object(network, "find_devices", return_value=[device("aa")]):
            for text in phrases:
                self.assertIn("I found 1 device,", self.say(text), text)

    def test_ordinary_sentences_about_the_internet_are_left_alone(self):
        for text in (
            "the internet is a series of tubes",
            "my network of friends",
            "note my wifi is slow",
            "add task fix the wifi",
            "show me a movie",
        ):
            with mock.patch.object(network, "check") as check, mock.patch.object(
                network, "find_devices"
            ) as scan:
                self.say(text)
            check.assert_not_called()
            scan.assert_not_called()

    def test_status_reports_problems(self):
        with self.status_is(NO_INTERNET):
            self.assertIn("internet is down", self.say("network status"))
        with self.status_is(OFFLINE):
            self.assertIn("isn't connected", self.say("network status"))
        with self.status_is(SLOW):
            self.assertIn("slow", self.say("network status"))

    def test_skipped_apostrophes_work_for_every_command(self):
        self.assertEqual(self.say("whats the date"), self.say("what's the date"))
        self.assertNotEqual(self.say("whats the date"), router.FALLBACK)
        with mock.patch("ossi.autostart.supported", return_value=False):
            self.assertEqual(self.say("dont start at login"), self.say("don't start at login"))
            self.assertNotEqual(self.say("dont start at login"), router.FALLBACK)

    def test_a_note_keeps_its_words_as_typed(self):
        self.say("note whats up with the dont")
        self.assertIn("whats up with the dont", self.say("notes"))

    def test_a_failed_check_says_so(self):
        with mock.patch.object(network, "check", side_effect=OSError):
            self.assertEqual(self.say("network status"), skill.COULDNT_CHECK)

    def test_devices(self):
        devices = [
            device("aa", "192.168.86.27", "printer"),
            device("bb", "192.168.86.1", is_router=True),
            device("cc", "192.168.86.100"),
        ]
        with mock.patch.object(network, "find_devices", return_value=devices):
            reply = self.say("what's on my network")
        self.assertIn("3 devices", reply)
        self.assertLess(reply.index("192.168.86.1 "), reply.index("192.168.86.27"))
        self.assertIn("your router", reply)
        self.assertIn("printer", reply)
        self.assertIn("unnamed device", reply)
        self.assertNotIn("(new)", reply)

    def test_devices_found_since_are_marked_new(self):
        with mock.patch.object(network, "find_devices", return_value=[device("aa")]):
            self.say("network devices")
        with mock.patch.object(
            network, "find_devices", return_value=[device("aa"), device("bb", "192.168.86.51")]
        ):
            reply = self.say("network devices")
        self.assertIn("192.168.86.51  unnamed device  (new)", reply)
        self.assertEqual(reply.count("(new)"), 1)

    def test_not_on_a_network(self):
        with mock.patch.object(network, "find_devices", return_value=None):
            self.assertIn("couldn't look", self.say("network devices"))

    def test_watching_is_off_until_asked(self):
        self.assertFalse(skill.is_watching(self.db))
        self.say("watch my network")
        self.assertTrue(skill.is_watching(self.db))
        self.say("stop watching my network")
        self.assertFalse(skill.is_watching(self.db))

    def test_a_model_cannot_turn_watching_on(self):
        self.assertIn("watch my network", ai.OFF_LIMITS)
        self.assertNotIn("watch my network", ai.build_instructions(self.db))

    def test_looking_needs_no_confirmation_when_spoken(self):
        with self.status_is(HEALTHY):
            reply = router.handle_spoken(self.db, "network status", router.Session())
        self.assertIn("working", reply)

    def test_turning_it_on_by_voice_asks_first(self):
        session = router.Session()
        self.assertEqual(
            router.handle_spoken(self.db, "watch my network", session),
            "Should I watch my network?",
        )
        self.assertFalse(skill.is_watching(self.db))


class DevicesTest(unittest.TestCase):
    def setUp(self):
        self.db = storage.connect(":memory:")
        self.addCleanup(self.db.close)

    def test_the_first_look_reports_nobody_as_new(self):
        self.assertEqual(skill.note_devices(self.db, [device("aa"), device("bb")]), [])

    def test_a_device_seen_later_is_new_once(self):
        skill.note_devices(self.db, [device("aa")])
        self.assertEqual(len(skill.note_devices(self.db, [device("aa"), device("bb")])), 1)
        self.assertEqual(skill.note_devices(self.db, [device("aa"), device("bb")]), [])

    def test_a_device_that_left_and_returned_is_not_new(self):
        skill.note_devices(self.db, [device("aa"), device("bb")])
        skill.note_devices(self.db, [device("aa")])
        self.assertEqual(skill.note_devices(self.db, [device("aa"), device("bb")]), [])

    def test_an_empty_first_look_still_counts_as_the_first(self):
        self.assertEqual(skill.note_devices(self.db, []), [])
        self.assertEqual(len(skill.note_devices(self.db, [device("aa")])), 1)


class WindowTest(unittest.TestCase):
    def setUp(self):
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
        self.toasts = []
        self.app = gui.App(self.root, self.db, notify=self.toast)

    def toast(self, title, body):
        self.toasts.append((title, body))
        return mock.Mock(poll=lambda: 0)

    def log(self):
        return self.app.log.get("1.0", "end")

    def finish_check(self):
        deadline = time.time() + 5
        while self.app.network_checking and time.time() < deadline:
            time.sleep(0.005)
            self.app.check_fetches()
        self.assertFalse(self.app.network_checking)

    def look(self, status, devices=None):
        with mock.patch.object(network, "check", return_value=status), mock.patch.object(
            network, "find_devices", return_value=devices
        ) as scan:
            self.app.next_network_check = 0
            self.app.check_network()
            self.finish_check()
        return scan

    def watching(self):
        storage.set_setting(self.db, "network_watch", "on")

    def test_nothing_is_checked_unless_watching(self):
        with mock.patch.object(network, "check") as check:
            self.app.check_network()
        check.assert_not_called()
        self.assertFalse(self.app.network_checking)

    def test_an_outage_shows_in_the_chat_and_as_a_notification(self):
        self.watching()
        self.look(NO_INTERNET, [])
        self.look(NO_INTERNET)
        self.assertIn("Your internet is down", self.log())
        self.assertEqual(self.toasts[0][0], "Network problem")

    def test_waits_a_minute_between_checks(self):
        self.watching()
        self.look(HEALTHY, [])
        with mock.patch.object(network, "check") as check:
            self.app.check_network()
        check.assert_not_called()

    def test_a_second_check_does_not_start_during_the_first(self):
        self.watching()
        with mock.patch.object(network, "check", return_value=HEALTHY) as check, mock.patch.object(
            network, "find_devices", return_value=[]
        ):
            self.app.check_network()
            self.app.next_network_check = 0
            self.app.check_network()
            self.finish_check()
        check.assert_called_once()

    def test_devices_are_only_scanned_now_and_then(self):
        self.watching()
        self.look(HEALTHY, [device("aa")]).assert_called_once()
        self.look(HEALTHY, [device("aa")]).assert_not_called()

    def test_no_scan_while_disconnected(self):
        self.watching()
        self.look(OFFLINE).assert_not_called()

    def test_a_new_device_is_reported(self):
        self.watching()
        self.look(HEALTHY, [device("aa")])
        self.assertEqual(self.toasts, [])
        self.app.next_device_scan = 0
        self.look(HEALTHY, [device("aa"), device("bb", "192.168.86.77", "phone")])
        self.assertIn("New device on your network: phone at 192.168.86.77", self.log())
        self.assertEqual(self.toasts[0][0], "New network device")

    def test_a_failed_check_is_quiet_and_tries_again_later(self):
        self.watching()
        with mock.patch.object(network, "check", side_effect=OSError):
            self.app.check_network()
            self.finish_check()
        self.assertEqual(self.toasts, [])
        self.assertGreater(self.app.next_network_check, time.time())

    def test_alerts_are_spoken_during_a_conversation(self):
        self.watching()
        with mock.patch.object(self.app.talk, "tell") as tell:
            self.look(NO_INTERNET, [])
            self.look(NO_INTERNET)
        self.assertIn("internet is down", tell.call_args[0][0])

    def test_open_google_home(self):
        link = self.app.google_home_label
        self.assertIn("Open Google Home", link.cget("text"))
        self.assertIs(link.master, self.app.settings_label.master)  # In the sidebar, with Settings.
        self.root.deiconify()  # Clicks only reach a window that's been drawn.
        with mock.patch("webbrowser.open") as browser:
            self.root.update()
            link.event_generate("<Button-1>")
            self.root.update()
        browser.assert_called_once_with("https://home.google.com/")

    def test_the_settings_switch(self):
        with mock.patch("ossi.ollama.list_models", return_value=[]):
            self.app.toggle_settings()
        panel = self.app.settings
        self.assertFalse(panel.network_switch.on)
        panel.network_switch.flip()
        self.assertTrue(skill.is_watching(self.db))
        panel.network_switch.flip()
        self.assertFalse(skill.is_watching(self.db))


if __name__ == "__main__":
    unittest.main()

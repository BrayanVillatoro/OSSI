"""Reports on the connection and home network, and can keep watch for problems."""

import json
import time

from ossi import network, storage
from ossi.registry import Fetch, command

COULDNT_CHECK = "I couldn't check your network just now."
FAILURES_BEFORE_ALERT = 2  # One missed check is often nothing, like the computer waking up.
SLOW_MS = 300
SLOW_CHECKS_BEFORE_ALERT = 3

# The different ways people say the same few things.
HOME_NETWORK = r"(?:network|wifi|wi-fi|wi fi|lan)"
CONNECTION = r"(?:internet|network|wifi|wi-fi|wi fi|connection)"
ON_NETWORK = rf"(?:my|the|our)(?: home)? {HOME_NETWORK}"
SHOW = r"(?:show|list|see|scan|find|give|tell)(?: me)?"
HOW_IT_IS = (
    r"(?:working|not working|up|down|out|doing|connected|ok|okay|slow|fast|good|online|offline)"
)
RIGHT_NOW = r"(?: right now| now| today)?"

OK = "ok"
NO_INTERNET = "no_internet"
NO_ROUTER = "no_router"
NOT_CONNECTED = "not_connected"

PROBLEMS = {
    NO_INTERNET: "Your internet is down. Your router is answering, so the problem is probably "
    "your internet provider or the connection to it.",
    NO_ROUTER: "Your internet is down, and your router isn't answering. Check that it has power.",
    NOT_CONNECTED: "This computer isn't connected to a network.",
}


def condition_of(status):
    if not status.connected:
        return NOT_CONNECTED
    if status.internet_ms is not None:
        return OK
    return NO_ROUTER if status.router_ms is None else NO_INTERNET


def describe_status(status):
    condition = condition_of(status)
    if condition != OK:
        return PROBLEMS[condition]
    reply = f"Your internet is working. It answered in {status.internet_ms:.0f} ms"
    if status.router_ms is not None:
        reply += f", and your router in {status.router_ms:.0f} ms"
    if status.internet_ms >= SLOW_MS:
        reply += ". That's slow"
    return f"{reply}."


def how_long(seconds):
    minutes = round(seconds / 60)
    if minutes < 2:
        return "about a minute"
    if minutes < 90:
        return f"about {minutes} minutes"
    return f"about {round(minutes / 60)} hours"


class Watch:
    """Turns regular checks into alerts, only when something changes and stays changed."""

    def __init__(self):
        self.reported = OK
        self.reported_at = 0.0
        self.pending = OK
        self.pending_count = 0
        self.slow_count = 0
        self.told_slow = False

    def update(self, status, now):
        """Returns the alerts to show, as (title, message) pairs."""
        condition = condition_of(status)
        self.pending_count = self.pending_count + 1 if condition == self.pending else 1
        self.pending = condition
        alerts = self.check_slow(status)
        if condition == self.reported:
            return alerts
        if condition != OK and self.pending_count < FAILURES_BEFORE_ALERT:
            return alerts
        if condition == OK:
            alerts.append(
                ("Network", f"Your connection is back. It was out for {how_long(now - self.reported_at)}.")
            )
        else:
            alerts.append(("Network problem", PROBLEMS[condition]))
            if self.reported == OK:  # Getting worse during an outage doesn't restart its clock.
                self.reported_at = now
        self.reported = condition
        return alerts

    def check_slow(self, status):
        slow = status.internet_ms is not None and status.internet_ms >= SLOW_MS
        self.slow_count = self.slow_count + 1 if slow else 0
        if not slow:
            self.told_slow = False
        if self.slow_count >= SLOW_CHECKS_BEFORE_ALERT and not self.told_slow:
            self.told_slow = True
            return [("Network", f"Your internet is slow. It's taking {status.internet_ms:.0f} ms to answer.")]
        return []


def known_devices(db):
    saved = storage.get_setting(db, "network_devices")
    return json.loads(saved) if saved else None


def note_devices(db, devices):
    """Remember these devices, and return the ones never seen before.

    The first time, everything is new, so nothing is reported as a stranger.
    """
    known = known_devices(db)
    first_time = known is None
    known = known or {}
    new = [device for device in devices if device["mac"] not in known]
    for device in new:
        known[device["mac"]] = {"name": device["name"], "first_seen": time.time()}
    if new or first_time:
        storage.set_setting(db, "network_devices", json.dumps(known))
    return [] if first_time else new


def label(device):
    if device["is_router"]:
        return "your router"
    return device["name"] or "unnamed device"


def describe_new_device(device):
    return (
        f"New device on your network: {label(device)} at {device['address']} ({device['mac']}). "
        "If you don't recognize it, check the Google Home app."
    )


def is_watching(db):
    return storage.get_setting(db, "network_watch") == "on"


@command(
    "network status",
    rf"(?:my |the )?{CONNECTION}(?: connection)? (?:status|check|speed)",
    rf"(?:check|test)(?: on)?(?: my| the)? {CONNECTION}(?: connection)?",
    rf"(?:is|how's|how is|why is)(?: my| the)? {CONNECTION}(?: connection)?(?: {HOW_IT_IS})?{RIGHT_NOW}",
    rf"(?:my |the )?{CONNECTION}(?: connection)? (?:is |isn't |is not |seems |seems to be )?{HOW_IT_IS}{RIGHT_NOW}",
    r"(?:do|have) i (?:have|got)(?: any)? (?:internet|wifi|wi-fi|wi fi)",
    r"am i (?:online|connected)",
)
def show_status(db):
    def finish(db, status, session):
        return describe_status(status) if status else COULDNT_CHECK

    return Fetch("Checking your connection...", network.check, finish)


@command(
    "network devices",
    rf"(?:my |the )?{HOME_NETWORK} devices",
    r"(?:all )?(?:my |the )?(?:connected )?devices",
    rf"(?:all )?(?:my |the )?(?:connected )?devices (?:on|connected to|using) {ON_NETWORK}",
    rf"{SHOW} {ON_NETWORK}(?: devices)?",
    rf"{SHOW}(?: all)?(?: my| the)?(?: connected)? devices(?: (?:on|connected to|using) {ON_NETWORK})?",
    rf"{SHOW}(?: me)? (?:what's|what is|who's|who is|everything|everyone)"
    rf"(?: connected)? (?:on|to|using) {ON_NETWORK}",
    rf"(?:what's|what is|who's|who is|what are|who are|what devices are|which devices are"
    rf"|how many devices are|anyone|anything|everything|everyone)"
    rf"(?: else)?(?: connected)? (?:on|to|using) {ON_NETWORK}",
    r"(?:what's|what is|who's|who is) connected",
)
def show_devices(db):
    def finish(db, devices, session):
        if devices is None:
            return "I couldn't look at your network. Are you connected to one?"
        if not devices:
            return "I didn't find any other devices on your network."
        new = note_devices(db, devices)
        addresses = [device["address"] for device in new]
        lines = [
            f"{device['address']}  {label(device)}"
            + ("  (new)" if device["address"] in addresses else "")
            for device in sorted(devices, key=lambda d: tuple(map(int, d["address"].split("."))))
        ]
        count = "1 device" if len(devices) == 1 else f"{len(devices)} devices"
        return f"I found {count}, besides this computer:\n" + "\n".join(lines)

    return Fetch("Looking around your network...", network.find_devices, finish)


@command("watch my network", r"(?:watch|monitor) (?:my|the) (?:network|internet|wifi|wi-fi)")
def start_watching(db):
    storage.set_setting(db, "network_watch", "on")
    return (
        "I'll check your connection every minute and tell you about outages, slowdowns and new "
        "devices."
    )


@command(
    "stop watching my network",
    r"(?:stop|don't) (?:watching|monitoring) (?:my|the) (?:network|internet|wifi|wi-fi)",
)
def stop_watching(db):
    storage.delete_setting(db, "network_watch")
    return "OK, I've stopped watching your network."

"""Turns starting at login on and off."""

from ossi import autostart
from ossi.registry import command

UNSUPPORTED = "Starting at login only works on Windows."


@command("start at login", r"(?:turn on |enable )?start(?:ing)?(?: ossi)? at login(?: on)?")
def enable(db):
    if not autostart.supported():
        return UNSUPPORTED
    autostart.enable()
    return "OSSI will start in the background when you log in, so reminders keep working."


@command(
    "don't start at login",
    r"(?:don't|do not) start(?: ossi)? at login",
    r"(?:turn off|disable|stop) start(?:ing)?(?: ossi)? at login",
    r"start(?: ossi)? at login off",
)
def disable(db):
    if not autostart.supported():
        return UNSUPPORTED
    autostart.disable()
    return "OSSI won't start at login. Reminders will only fire while it's open."

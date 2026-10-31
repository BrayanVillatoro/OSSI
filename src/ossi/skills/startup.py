"""Turns starting at login on and off."""

from ossi import autostart, storage
from ossi.registry import NO, YES, Ask, command

UNSUPPORTED = "Starting at login only works on Windows."
OFFER = "Reminders only fire while OSSI is running. Want it to start when you log in? (yes or no)"


def should_offer(db):
    if storage.get_setting(db, "login_offer_made") or not autostart.supported():
        return False
    return not autostart.is_enabled()


def offer(db, text):
    """Add a one-time question to the end of a reply. Only a yes changes anything."""
    storage.set_setting(db, "login_offer_made", "yes")

    def answer(db, reply, session):
        if reply.lower() in YES:
            return enable(db)
        if reply.lower() in NO:
            return "OK, I'll leave that off. Type 'start at login' if you change your mind."
        return None

    return Ask(f"{text}\n\n{OFFER}", answer)


@command("start at login", r"(?:turn on |enable )?start(?:ing)?(?: ossi)? at login(?: on)?")
def enable(db):
    if not autostart.supported():
        return UNSUPPORTED
    storage.set_setting(db, "login_offer_made", "yes")
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
    storage.set_setting(db, "login_offer_made", "yes")
    autostart.disable()
    return "OSSI won't start at login. Reminders will only fire while it's open."

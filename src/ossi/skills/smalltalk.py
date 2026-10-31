"""Polite replies, so a greeting or a thank-you doesn't get an error."""

from ossi.registry import command


@command(None, r"hi|hello|hey|hi ossi|hello ossi|hey ossi|good (?:afternoon|evening)")
def greet(db):
    return "Hi! What can I do for you?"


@command(None, r"thanks|thank you|thx|thanks ossi|thank you ossi")
def thanks(db):
    return "You're welcome!"


@command(None, r"who are you|what are you|what's your name|whats your name")
def introduce(db):
    return "I'm OSSI, your personal assistant. I run on this computer."

"""Lists what OSSI can do."""

from ossi.registry import COMMANDS, command


@command("help", r"help", r"what can you do")
def show_help(db):
    return "\n".join(sorted(f"  {cmd.usage}" for cmd in COMMANDS if cmd.usage))

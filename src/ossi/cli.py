"""Entry point: opens the window, or runs one command from the arguments."""

import sys

from ossi import router, storage


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if not args or args == ["--background"]:
        from ossi import gui

        return gui.main(background=bool(args))
    db = storage.connect()
    try:
        print(router.handle(db, " ".join(args)))
    finally:
        db.close()
    return 0

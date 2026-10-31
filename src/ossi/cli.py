"""Entry point: opens the window, or runs one command from the arguments."""

import sys

from ossi import router, storage


def answer_questions(db, session):
    """Let someone at a terminal answer whatever OSSI just asked."""
    while session.pending and sys.stdin.isatty():
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            return
        print(router.handle(db, text, session))


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if not args or args == ["--background"]:
        from ossi import gui

        return gui.main(background=bool(args))
    db = storage.connect()
    session = router.Session()
    try:
        print(router.handle(db, " ".join(args), session))
        answer_questions(db, session)
    finally:
        db.close()
    return 0

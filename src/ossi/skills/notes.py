"""Quick notes."""

from ossi.registry import command


def format_notes(rows):
    return "\n".join(f"{row['created_at'][:10]}  {row['body']}" for row in rows)


@command(
    "note <text>",
    r"(?:take a |add a? ?)?note(?: that)?[: ]\s*(?P<body>.+)",
    r"remember(?: that)? (?P<body>.+)",
)
def add_note(db, body):
    with db:
        db.execute("INSERT INTO notes (body) VALUES (?)", (body,))
    return "Saved."


@command("notes", r"(?:show |list )?(?:my )?notes")
def list_notes(db):
    rows = db.execute("SELECT body, created_at FROM notes ORDER BY id DESC LIMIT 20").fetchall()
    return format_notes(rows) if rows else "No notes yet."


@command("find note <word>", r"(?:find|search)(?: my)? notes?(?: for)? (?P<query>.+)")
def find_notes(db, query):
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = db.execute(
        "SELECT body, created_at FROM notes WHERE body LIKE ? ESCAPE '\\' ORDER BY id DESC",
        (f"%{escaped}%",),
    ).fetchall()
    return format_notes(rows) if rows else f"No notes mention '{query}'."

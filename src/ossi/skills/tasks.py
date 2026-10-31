"""To-do list."""

from ossi.references import is_number, is_pronoun, normalize, resolve, which_one
from ossi.registry import Ask, command


def open_tasks(db):
    return db.execute("SELECT id, title FROM tasks WHERE done_at IS NULL ORDER BY id").fetchall()


def last_completed(db):
    return db.execute(
        "SELECT id, title FROM tasks WHERE done_at IS NOT NULL ORDER BY done_at DESC, id DESC LIMIT 1"
    ).fetchone()


@command(
    "tasks",
    r"(?:show (?:me )?|list )?(?:my )?(?:tasks|todos|to-dos|to-do list)",
    r"what do i (?:need|have) to do",
    r"what(?:'?s| is) on my (?:list|plate|to-do list|todo list)",
)
def list_tasks(db, session):
    rows = open_tasks(db)
    session.listing["task"] = [row["id"] for row in rows]
    if not rows:
        return "No open tasks."
    return "\n".join(f"{row['id']}. {row['title']}" for row in rows)


@command(
    "done <number or name>",
    r"(?:done|finish|finished|complete|completed)(?: with)?(?: (?P<ref>.+))?",
    r"mark (?P<ref>.+?) (?:as )?(?:done|finished|complete|completed)",
    r"i(?:'m| am)? (?:finished|completed|done with|did) (?P<ref>.+)",
    r"(?:it's|its|that's|thats) (?:done|finished)",
)
@command(
    None,
    r"(?!add\b|todo\b|to-do\b)(?P<ref>.+?) (?:is|are) (?:done|finished|complete|completed)",
    catch_all=True,
)
def complete_task(db, session, ref=None):
    rows = open_tasks(db)
    if not rows and is_pronoun(ref):
        return "No open tasks."
    matches = resolve(ref, rows, "title", session.focus.get("task"), session.listing.get("task"))
    if not matches:
        if is_number(ref):
            return f"There's no open task {normalize(ref)}."
        return f"I can't find an open task matching '{normalize(ref)}'."
    if len(matches) > 1:
        session.listing["task"] = [row["id"] for row in matches]
        return Ask(
            which_one(matches, "title"),
            lambda db, text, session: complete_task(db, session, text),
        )

    task = matches[0]
    with db:
        # Fractional seconds keep the order right for undo when tasks finish close together.
        db.execute(
            "UPDATE tasks SET done_at = strftime('%Y-%m-%d %H:%M:%f', 'now') WHERE id = ?",
            (task["id"],),
        )
    session.focus["task"] = task["id"]
    return f"Completed task {task['id']}: {task['title']}"


# This comes after complete_task so that "task 3 is done" isn't added as a new task.
@command(
    "add task <title>",
    r"(?:add )?(?:task|todo|to-do)[: ]\s*(?P<title>.+)",
    r"add (?P<title>.+) to my (?:list|tasks|todo|to-do list)",
    catch_all=True,
)
def add_task(db, session, title):
    with db:
        cursor = db.execute("INSERT INTO tasks (title) VALUES (?)", (title,))
    session.focus["task"] = cursor.lastrowid
    return f"Added task {cursor.lastrowid}: {title}"


@command("undo", r"undo", r"undo (?:that|last|it)", r"reopen(?: last)?(?: task)?")
def undo_complete(db, session):
    task = last_completed(db)
    if task is None:
        return "There's nothing to undo."
    with db:
        db.execute("UPDATE tasks SET done_at = NULL WHERE id = ?", (task["id"],))
    session.focus["task"] = task["id"]
    return f"Reopened task {task['id']}: {task['title']}"

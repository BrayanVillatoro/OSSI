"""To-do list."""

from ossi.registry import command


def open_tasks(db):
    return db.execute("SELECT id, title FROM tasks WHERE done_at IS NULL ORDER BY id").fetchall()


def last_completed(db):
    return db.execute(
        "SELECT id, title FROM tasks WHERE done_at IS NOT NULL ORDER BY done_at DESC, id DESC LIMIT 1"
    ).fetchone()


@command(
    "add task <title>",
    r"(?:add )?(?:task|todo|to-do)[: ]\s*(?P<title>.+)",
    r"add (?P<title>.+) to my (?:list|tasks|todo|to-do list)",
)
def add_task(db, title):
    with db:
        cursor = db.execute("INSERT INTO tasks (title) VALUES (?)", (title,))
    return f"Added task {cursor.lastrowid}: {title}"


@command(
    "tasks",
    r"(?:show |list )?(?:my )?(?:tasks|todos|to-dos|to-do list)",
    r"what do i (?:need|have) to do",
)
def list_tasks(db):
    rows = open_tasks(db)
    if not rows:
        return "No open tasks."
    return "\n".join(f"{row['id']}. {row['title']}" for row in rows)


@command(
    "done <number>",
    r"(?:done|finish|finished|complete|completed)(?: task)? #?(?P<task_id>\d+)",
)
def complete_task(db, task_id):
    with db:
        # Fractional seconds keep the order right for undo when tasks finish close together.
        cursor = db.execute(
            "UPDATE tasks SET done_at = strftime('%Y-%m-%d %H:%M:%f', 'now') "
            "WHERE id = ? AND done_at IS NULL",
            (task_id,),
        )
    if cursor.rowcount == 0:
        return f"There's no open task {task_id}."
    return f"Completed task {task_id}."


@command("undo", r"undo", r"undo (?:that|last)", r"reopen(?: last)?(?: task)?")
def undo_complete(db):
    task = last_completed(db)
    if task is None:
        return "There's nothing to undo."
    with db:
        db.execute("UPDATE tasks SET done_at = NULL WHERE id = ?", (task["id"],))
    return f"Reopened task {task['id']}: {task['title']}"

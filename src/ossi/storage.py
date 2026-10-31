"""Local SQLite storage. Data lives outside the repo so it can't be committed."""

import os
import sqlite3
from pathlib import Path

# Each entry upgrades the database by one version. Never edit one that has shipped:
# add a new entry to the end instead.
MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        done_at TEXT
    );
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE reminders (
        id INTEGER PRIMARY KEY,
        message TEXT NOT NULL,
        due_at REAL NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        fired_at REAL
    );
    """,
    """
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
]


def default_path():
    home = os.environ.get("OSSI_HOME")
    return (Path(home) if home else Path.home() / ".ossi") / "ossi.db"


def connect(path=None):
    if path is None:
        path = default_path()
        path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        migrate(db, path)
    except Exception:
        db.close()
        raise
    return db


def migrate(db, path, migrations=None):
    migrations = MIGRATIONS if migrations is None else migrations
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version > len(migrations):
        raise RuntimeError(
            f"{path} was created by a newer version of OSSI. Update OSSI to open it."
        )
    if version == len(migrations):
        return

    backup(db, path, version)
    for number, script in enumerate(migrations[version:], start=version + 1):
        # All or nothing: a failed upgrade leaves the data as it was.
        try:
            db.executescript(f"BEGIN; {script}; PRAGMA user_version = {number}; COMMIT;")
        except sqlite3.Error:
            db.rollback()
            raise


def get_setting(db, key):
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(db, key, value):
    with db:
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


def delete_setting(db, key):
    with db:
        db.execute("DELETE FROM settings WHERE key = ?", (key,))


def backup(db, path, version):
    """Copy existing data aside before changing its structure."""
    has_tables = db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
    if not has_tables or str(path) == ":memory:":
        return
    copy = sqlite3.connect(Path(path).with_name(f"{Path(path).name}.v{version}.bak"))
    try:
        db.backup(copy)
    finally:
        copy.close()

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ossi import storage

ORIGINAL_SCHEMA = """
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    done_at TEXT
);
CREATE TABLE notes (
    id INTEGER PRIMARY KEY,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO tasks (title) VALUES ('call the bank');
INSERT INTO notes (body) VALUES ('wifi is on the router');
"""


def version(db):
    return db.execute("PRAGMA user_version").fetchone()[0]


class MigrationTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "ossi.db"

    def make_original_database(self):
        db = sqlite3.connect(self.path)
        db.executescript(ORIGINAL_SCHEMA)
        db.close()

    def test_new_database_gets_every_version(self):
        db = storage.connect(self.path)
        self.addCleanup(db.close)
        self.assertEqual(version(db), len(storage.MIGRATIONS))
        self.assertEqual(list(self.path.parent.glob("*.bak")), [])

    def test_upgrading_the_original_database_keeps_its_data_and_backs_it_up(self):
        self.make_original_database()
        db = storage.connect(self.path)
        self.addCleanup(db.close)

        self.assertEqual(db.execute("SELECT title FROM tasks").fetchone()[0], "call the bank")
        self.assertEqual(
            db.execute("SELECT body FROM notes").fetchone()[0], "wifi is on the router"
        )
        self.assertEqual(db.execute("SELECT COUNT(*) FROM reminders").fetchone()[0], 0)

        backup = sqlite3.connect(self.path.with_name("ossi.db.v0.bak"))
        self.addCleanup(backup.close)
        self.assertEqual(backup.execute("SELECT title FROM tasks").fetchone()[0], "call the bank")

    def test_connecting_again_changes_nothing(self):
        storage.connect(self.path).close()
        db = storage.connect(self.path)
        self.addCleanup(db.close)
        self.assertEqual(version(db), len(storage.MIGRATIONS))

    def test_a_failed_upgrade_leaves_the_data_and_version_untouched(self):
        self.make_original_database()
        db = sqlite3.connect(self.path)
        self.addCleanup(db.close)
        broken = [
            "CREATE TABLE first_step (id INTEGER)",
            "CREATE TABLE second_step (id INTEGER); THIS IS NOT SQL",
        ]

        with self.assertRaises(sqlite3.Error):
            storage.migrate(db, self.path, broken)

        self.assertEqual(version(db), 1)
        names = [row[0] for row in db.execute("SELECT name FROM sqlite_master")]
        self.assertIn("first_step", names)
        self.assertNotIn("second_step", names)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)

    def test_a_database_from_a_newer_ossi_is_refused(self):
        db = sqlite3.connect(self.path)
        db.execute(f"PRAGMA user_version = {len(storage.MIGRATIONS) + 1}")
        db.close()
        with self.assertRaisesRegex(RuntimeError, "newer version"):
            storage.connect(self.path)


if __name__ == "__main__":
    unittest.main()

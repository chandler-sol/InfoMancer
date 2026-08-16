from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.migrations import MIGRATIONS


class MigrationTests(unittest.TestCase):
    def test_fresh_database_records_all_numbered_migrations_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "catalog.db")
            database.initialize()
            database.initialize()
            with database.connect() as conn:
                versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
            self.assertEqual(versions, [migration.version for migration in MIGRATIONS])

    def test_legacy_database_receives_missing_columns(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.db"
            conn = sqlite3.connect(path)
            conn.executescript("""
                CREATE TABLE roots(id INTEGER PRIMARY KEY,path TEXT,kind TEXT,label TEXT,enabled INTEGER DEFAULT 1,last_scanned_at TEXT);
                CREATE TABLE titles(id INTEGER PRIMARY KEY,root_id INTEGER,kind TEXT,title TEXT,year INTEGER,folder_path TEXT,metadata_title TEXT,updated_at TEXT);
                CREATE TABLE files(id INTEGER PRIMARY KEY,title_id INTEGER,path TEXT,filename TEXT,extension TEXT,size_bytes INTEGER,modified_at REAL,season INTEGER,episode_start INTEGER,episode_end INTEGER,parsed_title TEXT,seen_scan TEXT);
            """)
            conn.commit()
            conn.close()
            Database(path).initialize()
            with Database(path).connect() as upgraded:
                columns = {row["name"] for row in upgraded.execute("PRAGMA table_info(files)")}
                self.assertIn("edition_name", columns)
                self.assertIn("version_preferred", columns)
                self.assertIsNotNone(upgraded.execute("SELECT 1 FROM schema_migrations WHERE version=11").fetchone())
                self.assertIsNotNone(upgraded.execute("SELECT 1 FROM schema_migrations WHERE version=12").fetchone())
                self.assertIsNotNone(upgraded.execute("SELECT 1 FROM schema_migrations WHERE version=13").fetchone())
                self.assertIsNotNone(upgraded.execute("SELECT 1 FROM schema_migrations WHERE version=14").fetchone())
                rename_columns = {row["name"] for row in upgraded.execute("PRAGMA table_info(rename_proposals)")}
                self.assertTrue({"file_id", "source_path", "destination_path", "source_size", "source_mtime_ns", "status"}.issubset(rename_columns))
                operation_columns = {
                    row["name"] for row in upgraded.execute("PRAGMA table_info(operation_history)")
                }
                self.assertTrue({"operation_type", "status", "undo_kind", "undo_payload", "undone_at"}.issubset(operation_columns))
                saved_view_columns = {
                    row["name"] for row in upgraded.execute("PRAGMA table_info(user_saved_views)")
                }
                self.assertTrue({"user_id", "name", "path", "query_string", "pinned"}.issubset(saved_view_columns))
                lockout_columns = {
                    row["name"] for row in upgraded.execute("PRAGMA table_info(login_lockouts)")
                }
                self.assertEqual(
                    {"scope", "lock_key", "locked_until", "created_at"},
                    lockout_columns,
                )


if __name__ == "__main__":
    unittest.main()

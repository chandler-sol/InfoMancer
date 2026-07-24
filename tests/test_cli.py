from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from app.cli import (
    CliError,
    _database,
    _export_rows,
    build_parser,
    command_backup,
    command_export,
    command_status,
)
from app.db import Database


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.database = Database(self.base / "infomancer.db")
        self.database.initialize()
        with self.database.connect() as conn:
            root_id = conn.execute(
                """INSERT INTO roots(path,kind,label,last_scanned_at)
                   VALUES (?,?,?,CURRENT_TIMESTAMP)""",
                (str(self.base / "Movies"), "movie", "Test Movies"),
            ).lastrowid
            title_id = conn.execute(
                """INSERT INTO titles(root_id,kind,title,year,folder_path,imdb_id)
                   VALUES (?,?,?,?,?,?)""",
                (
                    root_id, "movie", "Example Movie", 2024,
                    str(self.base / "Movies" / "Example Movie (2024).mkv"),
                    "tt1234567",
                ),
            ).lastrowid
            conn.execute(
                """INSERT INTO files(
                     title_id,path,filename,extension,size_bytes,seen_scan
                   ) VALUES (?,?,?,?,?,?)""",
                (
                    title_id,
                    str(self.base / "Movies" / "Example Movie (2024).mkv"),
                    "Example Movie (2024).mkv", ".mkv", 1024, "test",
                ),
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_parser_requires_scan_selection(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["scan"])
        parsed = parser.parse_args(["scan", "--all", "--yes"])
        self.assertTrue(parsed.all)
        self.assertTrue(parsed.yes)

    def test_missing_database_is_explained_without_creating_one(self) -> None:
        missing = self.base / "missing.db"
        with self.assertRaisesRegex(CliError, "Start InfoMancer once"):
            _database(Namespace(database=missing))
        self.assertFalse(missing.exists())

    def test_status_reports_catalog_counts_and_sources(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            result = command_status(self.database, Namespace())
        self.assertEqual(result, 0)
        rendered = output.getvalue()
        self.assertIn("Movies: 1", rendered)
        self.assertIn("Test Movies", rendered)
        self.assertIn("Media files: 1", rendered)

    def test_export_rows_include_shared_library_data_without_a_user(self) -> None:
        rows = _export_rows(self.database, None)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Example Movie")
        self.assertEqual(rows[0]["imdb_id"], "tt1234567")
        self.assertEqual(json.loads(rows[0]["custom_fields"])["favorite"], False)

    def test_json_export_writes_a_portable_library_file(self) -> None:
        destination = self.base / "library.json"
        result = command_export(
            self.database,
            Namespace(format="json", output=str(destination), user=None),
        )
        self.assertEqual(result, 0)
        payload = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(payload["items"][0]["filename"], "Example Movie (2024).mkv")

    def test_backup_uses_sqlite_backup_api_and_is_readable(self) -> None:
        destination = self.base / "backup.db"
        result = command_backup(
            self.database, Namespace(output=str(destination))
        )
        self.assertEqual(result, 0)
        conn = sqlite3.connect(destination)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM titles").fetchone()[0], 1)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()

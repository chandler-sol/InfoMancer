from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db import SCHEMA
from app.scanner import scan_root


class SourceGuardNetworkWarningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT INTO roots(path,kind,label) VALUES (?,?,?)",
            (str(self.root), "movie", "Mapped Movies"),
        )

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def root_row(self) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM roots").fetchone()

    @staticmethod
    def walk_with_warning(files: list[Path], warning: str):
        def _walk(_root: Path, errors: list[str]):
            errors.append(warning)
            yield from files

        return _walk

    def test_winerror_1272_is_warning_after_all_catalog_files_are_accounted_for(self):
        first = self.root / "Arrival (2016).mkv"
        second = self.root / "Alien (1979).mkv"
        first.write_bytes(b"arrival")
        second.write_bytes(b"alien")
        scan_root(self.conn, self.root_row())

        with patch(
            "app.scanner._walk_files",
            self.walk_with_warning(
                [first, second],
                "[WinError 1272] Security policy blocked final-path metadata lookup",
            ),
        ):
            result = scan_root(self.conn, self.root_row())

        self.assertEqual(result["source_status"], "healthy")
        self.assertEqual(result["read_errors"], 1)
        self.assertEqual(result["read_warnings"], 1)
        status = self.root_row()
        self.assertEqual(status["health_status"], "healthy")
        self.assertEqual(status["guard_preserved_count"], 0)
        self.assertEqual(status["last_error"], "")

    def test_winerror_1272_still_degrades_when_a_catalog_file_is_missing(self):
        first = self.root / "Arrival (2016).mkv"
        second = self.root / "Alien (1979).mkv"
        first.write_bytes(b"arrival")
        second.write_bytes(b"alien")
        scan_root(self.conn, self.root_row())
        second.unlink()

        with patch(
            "app.scanner._walk_files",
            self.walk_with_warning(
                [first],
                "[WinError 1272] Security policy blocked final-path metadata lookup",
            ),
        ):
            result = scan_root(self.conn, self.root_row())

        self.assertEqual(result["source_status"], "degraded")
        self.assertEqual(result["preserved"], 1)
        self.assertEqual(self.root_row()["health_status"], "degraded")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0], 2)

    def test_other_read_errors_remain_blocking_even_when_catalog_is_visible(self):
        media = self.root / "Arrival (2016).mkv"
        media.write_bytes(b"arrival")
        scan_root(self.conn, self.root_row())

        with patch(
            "app.scanner._walk_files",
            self.walk_with_warning([media], "Permission denied while reading a directory"),
        ):
            result = scan_root(self.conn, self.root_row())

        self.assertEqual(result["source_status"], "degraded")
        self.assertEqual(result["read_errors"], 1)
        self.assertEqual(result["read_warnings"], 0)
        self.assertEqual(self.root_row()["health_status"], "degraded")


if __name__ == "__main__":
    unittest.main()

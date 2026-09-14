from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from app.db import Database
from app.safe_file_rename import SafeFileRenameError, SafeFileRenameService


class SafeFileRenameTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "media"
        self.root.mkdir()
        self.show = self.root / "Example Show"
        self.show.mkdir()
        self.source = self.show / "old.mkv"
        self.source.write_bytes(b"media")
        self.database = Database(self.base / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            self.root_id = int(conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES (?,?,?)",
                (str(self.root), "tv", "TV"),
            ).lastrowid)
            self.title_id = int(conn.execute(
                "INSERT INTO titles(root_id,kind,title,folder_path) VALUES (?,?,?,?)",
                (self.root_id, "tv", "Example Show", str(self.show)),
            ).lastrowid)
            self.file_id = int(conn.execute(
                """INSERT INTO files(title_id,path,filename,extension,seen_scan)
                   VALUES (?,?,?,?,?)""",
                (self.title_id, str(self.source), self.source.name, ".mkv", "scan"),
            ).lastrowid)
        self.service = SafeFileRenameService(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def test_file_rename_updates_catalog(self):
        target = self.show / "new.mkv"
        self.service.rename_file(self.file_id, self.source, target)
        self.assertFalse(self.source.exists())
        self.assertEqual(target.read_bytes(), b"media")
        with self.database.connect() as conn:
            row = conn.execute("SELECT path,filename FROM files WHERE id=?", (self.file_id,)).fetchone()
        self.assertEqual(row["path"], str(target))
        self.assertEqual(row["filename"], target.name)

    def test_file_rename_revalidates_late_destination_symlink(self):
        target = self.show / "new.mkv"
        outside = self.base / "outside.mkv"
        outside.write_bytes(b"outside")
        original = self.service._require_inside
        calls = 0

        def guarded(path: Path, root: Path) -> None:
            nonlocal calls
            calls += 1
            original(path, root)
            if calls == 2:
                try:
                    target.symlink_to(outside)
                except (OSError, NotImplementedError):
                    self.skipTest("File symlinks are unavailable on this runner")

        self.service._require_inside = guarded
        with self.assertRaises(SafeFileRenameError):
            self.service.rename_file(self.file_id, self.source, target)
        self.assertEqual(self.source.read_bytes(), b"media")
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_file_catalog_failure_rolls_filesystem_back_with_domain_error(self):
        target = self.show / "new.mkv"
        real_connect = self.database.connect
        calls = 0

        @contextmanager
        def controlled_connect():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.assertTrue(target.is_file())
                self.assertFalse(self.source.exists())
                raise sqlite3.OperationalError("synthetic catalog failure")
            with real_connect() as conn:
                yield conn

        self.database.connect = controlled_connect
        with self.assertRaisesRegex(SafeFileRenameError, "restored the media file"):
            self.service.rename_file(self.file_id, self.source, target)
        self.assertEqual(self.source.read_bytes(), b"media")
        self.assertFalse(target.exists())

    def test_file_rollback_refuses_new_source_collision(self):
        target = self.show / "new.mkv"
        real_connect = self.database.connect
        calls = 0

        @contextmanager
        def controlled_connect():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.assertTrue(target.is_file())
                self.source.write_bytes(b"new collision")
                raise sqlite3.OperationalError("synthetic catalog failure")
            with real_connect() as conn:
                yield conn

        self.database.connect = controlled_connect
        with self.assertRaisesRegex(SafeFileRenameError, "refused to overwrite"):
            self.service.rename_file(self.file_id, self.source, target)
        self.assertEqual(self.source.read_bytes(), b"new collision")
        self.assertEqual(target.read_bytes(), b"media")

    def test_folder_rename_updates_title_and_file_paths(self):
        target = self.root / "Example Show (2020)"
        self.service.rename_folder(self.title_id, self.show, target)
        moved_file = target / self.source.name
        self.assertTrue(moved_file.is_file())
        with self.database.connect() as conn:
            title = conn.execute("SELECT folder_path FROM titles WHERE id=?", (self.title_id,)).fetchone()
            file_row = conn.execute("SELECT path FROM files WHERE id=?", (self.file_id,)).fetchone()
        self.assertEqual(title["folder_path"], str(target))
        self.assertEqual(file_row["path"], str(moved_file))

    def test_folder_catalog_failure_rolls_filesystem_back(self):
        target = self.root / "Example Show (2020)"
        real_connect = self.database.connect
        calls = 0

        @contextmanager
        def controlled_connect():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.assertTrue(target.is_dir())
                self.assertFalse(self.show.exists())
                raise sqlite3.OperationalError("synthetic catalog failure")
            with real_connect() as conn:
                yield conn

        self.database.connect = controlled_connect
        with self.assertRaisesRegex(SafeFileRenameError, "restored the show folder"):
            self.service.rename_folder(self.title_id, self.show, target)
        self.assertTrue(self.source.is_file())
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()

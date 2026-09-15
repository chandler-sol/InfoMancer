from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app.db import Database
from app.safe_file_rename import SafeFileRenameError, SafeFileRenameService


class R305SafeRenameRollbackIdentityTests(unittest.TestCase):
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

    def _catalog_path(self) -> str:
        with self.database.connect() as conn:
            row = conn.execute("SELECT path FROM files WHERE id=?", (self.file_id,)).fetchone()
        return str(row["path"])

    def test_file_rollback_refuses_substituted_destination_object(self):
        target = self.show / "new.mkv"
        parked = self.show / "moved-original.mkv"
        real_connect = self.database.connect
        calls = 0

        @contextmanager
        def controlled_connect():
            nonlocal calls
            calls += 1
            if calls == 3:
                target.rename(parked)
                target.write_bytes(b"intruder")
                raise sqlite3.OperationalError("synthetic catalog failure")
            with real_connect() as conn:
                yield conn

        with patch.object(self.database, "connect", controlled_connect):
            with self.assertRaisesRegex(SafeFileRenameError, "exact object moved"):
                self.service.rename_file(self.file_id, self.source, target)

        self.assertFalse(self.source.exists())
        self.assertEqual(target.read_bytes(), b"intruder")
        self.assertEqual(parked.read_bytes(), b"media")
        self.assertEqual(self._catalog_path(), str(self.source))

    def test_file_rollback_refuses_replaced_parent_even_when_moved_object_survives(self):
        target = self.show / "new.mkv"
        old_parent = self.root / "Example Show old"
        real_connect = self.database.connect
        calls = 0

        @contextmanager
        def controlled_connect():
            nonlocal calls
            calls += 1
            if calls == 3:
                self.show.rename(old_parent)
                self.show.mkdir()
                (old_parent / target.name).rename(target)
                raise sqlite3.OperationalError("synthetic catalog failure")
            with real_connect() as conn:
                yield conn

        with patch.object(self.database, "connect", controlled_connect):
            with self.assertRaisesRegex(SafeFileRenameError, "original parent identity changed"):
                self.service.rename_file(self.file_id, self.source, target)

        self.assertFalse(self.source.exists())
        self.assertEqual(target.read_bytes(), b"media")
        self.assertTrue(old_parent.is_dir())
        self.assertEqual(self._catalog_path(), str(self.source))

    def test_file_rollback_refuses_replaced_root_identity(self):
        target = self.show / "new.mkv"
        old_root = self.base / "media old"
        real_connect = self.database.connect
        calls = 0

        @contextmanager
        def controlled_connect():
            nonlocal calls
            calls += 1
            if calls == 3:
                self.root.rename(old_root)
                self.root.mkdir()
                replacement_show = self.root / self.show.name
                replacement_show.mkdir()
                (old_root / self.show.name / target.name).rename(replacement_show / target.name)
                raise sqlite3.OperationalError("synthetic catalog failure")
            with real_connect() as conn:
                yield conn

        with patch.object(self.database, "connect", controlled_connect):
            with self.assertRaisesRegex(SafeFileRenameError, "configured source identity changed"):
                self.service.rename_file(self.file_id, self.source, target)

        self.assertFalse(self.source.exists())
        self.assertEqual(target.read_bytes(), b"media")
        self.assertTrue(old_root.is_dir())
        self.assertEqual(self._catalog_path(), str(self.source))

    def test_file_rollback_treats_dangling_symlink_source_as_occupied(self):
        target = self.show / "new.mkv"
        missing = self.base / "does-not-exist"
        real_connect = self.database.connect
        calls = 0

        @contextmanager
        def controlled_connect():
            nonlocal calls
            calls += 1
            if calls == 3:
                try:
                    self.source.symlink_to(missing)
                except (OSError, NotImplementedError):
                    self.skipTest("File symlinks are unavailable on this runner")
                self.assertTrue(os.path.lexists(self.source))
                self.assertFalse(self.source.exists())
                raise sqlite3.OperationalError("synthetic catalog failure")
            with real_connect() as conn:
                yield conn

        with patch.object(self.database, "connect", controlled_connect):
            with self.assertRaisesRegex(SafeFileRenameError, "refused to overwrite"):
                self.service.rename_file(self.file_id, self.source, target)

        self.assertTrue(os.path.lexists(self.source))
        self.assertEqual(target.read_bytes(), b"media")
        self.assertEqual(self._catalog_path(), str(self.source))

    def test_folder_rollback_refuses_substituted_destination_object(self):
        target = self.root / "Example Show (2020)"
        parked = self.root / "moved-original-show"
        real_connect = self.database.connect
        calls = 0

        @contextmanager
        def controlled_connect():
            nonlocal calls
            calls += 1
            if calls == 3:
                target.rename(parked)
                target.mkdir()
                (target / "intruder.txt").write_text("intruder", encoding="utf-8")
                raise sqlite3.OperationalError("synthetic catalog failure")
            with real_connect() as conn:
                yield conn

        with patch.object(self.database, "connect", controlled_connect):
            with self.assertRaisesRegex(SafeFileRenameError, "exact object moved"):
                self.service.rename_folder(self.title_id, self.show, target)

        self.assertFalse(self.show.exists())
        self.assertEqual((target / "intruder.txt").read_text(encoding="utf-8"), "intruder")
        self.assertEqual((parked / "old.mkv").read_bytes(), b"media")
        self.assertEqual(self._catalog_path(), str(self.source))

    def test_folder_rollback_refuses_replaced_destination_parent_identity(self):
        destination_parent = self.root / "renamed"
        destination_parent.mkdir()
        target = destination_parent / "Example Show (2020)"
        old_parent = self.root / "renamed old"
        real_connect = self.database.connect
        calls = 0

        @contextmanager
        def controlled_connect():
            nonlocal calls
            calls += 1
            if calls == 3:
                destination_parent.rename(old_parent)
                destination_parent.mkdir()
                (old_parent / target.name).rename(target)
                raise sqlite3.OperationalError("synthetic catalog failure")
            with real_connect() as conn:
                yield conn

        with patch.object(self.database, "connect", controlled_connect):
            with self.assertRaisesRegex(SafeFileRenameError, "destination parent identity changed"):
                self.service.rename_folder(self.title_id, self.show, target)

        self.assertFalse(self.show.exists())
        self.assertEqual((target / "old.mkv").read_bytes(), b"media")
        self.assertTrue(old_parent.is_dir())
        self.assertEqual(self._catalog_path(), str(self.source))


if __name__ == "__main__":
    unittest.main()

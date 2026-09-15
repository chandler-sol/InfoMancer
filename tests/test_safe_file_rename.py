from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

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
            if calls == 3:
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
            if calls == 3:
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

    def test_folder_and_file_renames_serialize_across_service_instances(self):
        folder_target = self.root / "Renamed Show"
        file_target = self.show / "renamed-episode.mkv"
        second = SafeFileRenameService(Database(self.database.path))
        folder_moved = threading.Event()
        release_folder = threading.Event()
        folder_errors: list[BaseException] = []
        file_errors: list[BaseException] = []
        original_rename = Path.rename

        def controlled_rename(path: Path, destination: Path | str):
            result = original_rename(path, destination)
            if path == self.show and Path(destination) == folder_target:
                folder_moved.set()
                if not release_folder.wait(5):
                    raise RuntimeError("test timed out waiting to release folder rename")
            return result

        def rename_folder() -> None:
            try:
                self.service.rename_folder(self.title_id, self.show, folder_target)
            except BaseException as exc:
                folder_errors.append(exc)

        def rename_file() -> None:
            try:
                second.rename_file(self.file_id, self.source, file_target)
            except BaseException as exc:
                file_errors.append(exc)

        with patch.object(Path, "rename", controlled_rename):
            folder_thread = threading.Thread(target=rename_folder)
            file_thread = threading.Thread(target=rename_file)
            folder_thread.start()
            self.assertTrue(folder_moved.wait(5), "folder rename never reached the interleave point")
            file_thread.start()
            time.sleep(0.1)
            self.assertTrue(file_thread.is_alive(), "file rename did not wait for the title mutation lock")
            release_folder.set()
            folder_thread.join(5)
            file_thread.join(5)

        self.assertFalse(folder_thread.is_alive())
        self.assertFalse(file_thread.is_alive())
        self.assertEqual(folder_errors, [])
        self.assertEqual(len(file_errors), 1)
        self.assertIsInstance(file_errors[0], SafeFileRenameError)
        moved_file = folder_target / "old.mkv"
        self.assertEqual(moved_file.read_bytes(), b"media")
        self.assertFalse(file_target.exists())
        with self.database.connect() as conn:
            title = conn.execute("SELECT folder_path FROM titles WHERE id=?", (self.title_id,)).fetchone()
            file_row = conn.execute("SELECT path,filename FROM files WHERE id=?", (self.file_id,)).fetchone()
        self.assertEqual(title["folder_path"], str(folder_target))
        self.assertEqual(file_row["path"], str(moved_file))
        self.assertEqual(file_row["filename"], "old.mkv")


class SafeRenameRouteContractTests(unittest.TestCase):
    def test_live_title_rename_routes_use_safe_service(self):
        root = Path(__file__).resolve().parents[1]
        routes = (root / "app/routes/titles.py").read_text(encoding="utf-8")
        self.assertIn("safe_renames = SafeFileRenameService(db)", routes)
        self.assertEqual(routes.count("safe_renames.rename_file("), 4)
        self.assertEqual(routes.count("safe_renames.rename_folder("), 1)
        self.assertNotIn("source.rename(destination)", routes)
        self.assertNotIn('proposal["source"].rename(proposal["destination"])', routes)


if __name__ == "__main__":
    unittest.main()

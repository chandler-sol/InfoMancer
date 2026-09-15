from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db import Database
from app.safe_file_rename import SafeFileRenameError, SafeFileRenameService
from app.scanner import scan_root


class ScannerFolderRenameCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "media"
        self.root.mkdir()
        self.show = self.root / "Example Show"
        self.show.mkdir()
        self.first = self.show / "Example.Show.S01E01.mkv"
        self.first.write_bytes(b"episode one")
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
                """INSERT INTO files(title_id,path,filename,extension,size_bytes,seen_scan)
                   VALUES (?,?,?,?,?,?)""",
                (self.title_id, str(self.first), self.first.name, ".mkv", self.first.stat().st_size, "seed"),
            ).lastrowid)
        self.service = SafeFileRenameService(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def test_root_scan_waits_for_folder_rename_then_catalogs_new_file_at_new_path(self):
        second = self.show / "Example.Show.S01E02.mkv"
        second.write_bytes(b"episode two")
        target = self.root / "Renamed Show"
        folder_moved = threading.Event()
        release_folder = threading.Event()
        rename_errors: list[BaseException] = []
        scan_errors: list[BaseException] = []
        scan_result: list[dict[str, int | str]] = []
        original_rename = Path.rename

        def controlled_rename(path: Path, destination: Path | str):
            result = original_rename(path, destination)
            if path == self.show and Path(destination) == target:
                folder_moved.set()
                if not release_folder.wait(5):
                    raise RuntimeError("test timed out waiting to release folder rename")
            return result

        def rename_folder() -> None:
            try:
                self.service.rename_folder(self.title_id, self.show, target)
            except BaseException as exc:
                rename_errors.append(exc)

        def scan() -> None:
            try:
                scanner_database = Database(self.database.path)
                with scanner_database.connect() as conn:
                    root_row = conn.execute(
                        "SELECT * FROM roots WHERE id=?", (self.root_id,),
                    ).fetchone()
                    scan_result.append(scan_root(conn, root_row))
            except BaseException as exc:
                scan_errors.append(exc)

        with patch.object(Path, "rename", controlled_rename):
            rename_thread = threading.Thread(target=rename_folder)
            scan_thread = threading.Thread(target=scan)
            rename_thread.start()
            self.assertTrue(folder_moved.wait(5), "folder rename never reached the interleave point")
            scan_thread.start()
            time.sleep(0.1)
            self.assertTrue(scan_thread.is_alive(), "scanner did not wait for the root mutation lease")
            release_folder.set()
            rename_thread.join(5)
            scan_thread.join(5)

        self.assertFalse(rename_thread.is_alive())
        self.assertFalse(scan_thread.is_alive())
        self.assertEqual(rename_errors, [])
        self.assertEqual(scan_errors, [])
        self.assertEqual(scan_result[0]["files"], 2)
        with self.database.connect() as conn:
            title = conn.execute(
                "SELECT folder_path FROM titles WHERE id=?", (self.title_id,),
            ).fetchone()
            rows = conn.execute(
                "SELECT path FROM files WHERE title_id=? ORDER BY path", (self.title_id,),
            ).fetchall()
        self.assertEqual(title["folder_path"], str(target))
        self.assertEqual(
            {row["path"] for row in rows},
            {
                str(target / self.first.name),
                str(target / second.name),
            },
        )
        self.assertFalse(any(str(self.show) in row["path"] for row in rows))

    def test_uncaptured_insert_after_snapshot_forces_guarded_folder_rollback(self):
        second = self.show / "Example.Show.S01E02.mkv"
        second.write_bytes(b"episode two")
        target = self.root / "Renamed Show"
        inserted = False
        original_rename = Path.rename

        def insert_after_snapshot_then_rename(path: Path, destination: Path | str):
            nonlocal inserted
            if path == self.show and Path(destination) == target and not inserted:
                inserted = True
                bypass = sqlite3.connect(self.database.path)
                try:
                    with bypass:
                        bypass.execute(
                            """INSERT INTO files(title_id,path,filename,extension,size_bytes,seen_scan)
                               VALUES (?,?,?,?,?,?)""",
                            (self.title_id, str(second), second.name, ".mkv", second.stat().st_size, "interleave"),
                        )
                finally:
                    bypass.close()
            return original_rename(path, destination)

        with patch.object(Path, "rename", insert_after_snapshot_then_rename):
            with self.assertRaisesRegex(SafeFileRenameError, "restored the show folder"):
                self.service.rename_folder(self.title_id, self.show, target)

        self.assertTrue(inserted)
        self.assertTrue(self.show.is_dir())
        self.assertTrue(self.first.is_file())
        self.assertTrue(second.is_file())
        self.assertFalse(target.exists())
        with self.database.connect() as conn:
            title = conn.execute(
                "SELECT folder_path FROM titles WHERE id=?", (self.title_id,),
            ).fetchone()
            rows = conn.execute(
                "SELECT path FROM files WHERE title_id=? ORDER BY id", (self.title_id,),
            ).fetchall()
        self.assertEqual(title["folder_path"], str(self.show))
        self.assertEqual(
            {row["path"] for row in rows},
            {str(self.first), str(second)},
        )


if __name__ == "__main__":
    unittest.main()

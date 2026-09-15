from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db import Database
from app.duplicate_trash import DuplicateTrashError, DuplicateTrashService
from app.season_folders import SeasonFolderError, SeasonFolderService


class SeasonPostMoveIdentityTrackingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "tv"
        self.show = self.root / "Example Show"
        self.show.mkdir(parents=True)
        self.database = Database(self.base / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            root_id = int(conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES (?,?,?)",
                (str(self.root), "tv", "TV"),
            ).lastrowid)
            self.title_id = int(conn.execute(
                "INSERT INTO titles(root_id,kind,title,folder_path) VALUES (?,?,?,?)",
                (root_id, "tv", "Example Show", str(self.show)),
            ).lastrowid)
            self.source = self.show / "episode.mkv"
            self.source.write_bytes(b"video")
            self.file_id = int(conn.execute(
                """INSERT INTO files(title_id,path,filename,extension,season,episode_start,seen_scan)
                   VALUES (?,?,?,?,?,?,?)""",
                (self.title_id, str(self.source), self.source.name, ".mkv", 1, 1, "scan"),
            ).lastrowid)
        self.service = SeasonFolderService(self.database)
        self.destination = self.show / "Season 01" / self.source.name

    def tearDown(self):
        self.temporary.cleanup()

    def test_post_move_identity_failure_is_tracked_as_incomplete_rollback(self):
        real_identity = self.service._file_identity

        def fail_moved_identity(path: Path):
            candidate = Path(path)
            if candidate == self.destination:
                raise OSError("synthetic post-move identity failure")
            return real_identity(candidate)

        with patch.object(self.service, "_file_identity", side_effect=fail_moved_identity):
            with self.assertRaisesRegex(SeasonFolderError, "automatic rollback was incomplete"):
                self.service.apply(self.title_id, [self.file_id])

        self.assertFalse(self.source.exists())
        self.assertTrue(self.destination.is_file())
        with self.database.connect() as conn:
            catalog_path = conn.execute(
                "SELECT path FROM files WHERE id=?", (self.file_id,)
            ).fetchone()["path"]
        self.assertEqual(catalog_path, str(self.source))


class DuplicateTrashPostMoveIdentityTrackingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "media"
        self.root.mkdir()
        self.database = Database(self.base / "catalog.db")
        self.database.initialize()
        self.service = DuplicateTrashService(self.database)
        self.source = self.root / "first.mkv"
        self.source.write_bytes(b"first")
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO roots(id,path,kind,label) VALUES (1,?,'movie','Movies')",
                (str(self.root),),
            )
            conn.execute(
                """INSERT INTO titles(id,root_id,kind,title,folder_path)
                   VALUES (1,1,'movie','Example Movie',?)""",
                (str(self.root / "Example Movie"),),
            )
            conn.execute(
                """INSERT INTO files(
                     id,title_id,path,filename,extension,size_bytes,modified_at,seen_scan
                   ) VALUES (1,1,?,'first.mkv','.mkv',5,1.0,'scan')""",
                (str(self.source),),
            )

    def tearDown(self):
        self.temporary.cleanup()

    def test_trash_move_post_move_identity_failure_is_reported_incomplete(self):
        real_identity = self.service._file_identity

        def fail_trash_identity(path: Path):
            candidate = Path(path)
            if ".infomancer-trash" in candidate.parts:
                raise OSError("synthetic post-move identity failure")
            return real_identity(candidate)

        with patch.object(self.service, "_file_identity", side_effect=fail_trash_identity):
            with self.assertRaisesRegex(DuplicateTrashError, "Managed Trash move is incomplete"):
                self.service.move(1, 30, None)

        self.assertFalse(self.source.exists())
        trashed_files = [
            path for path in (self.root / ".infomancer-trash").rglob("*") if path.is_file()
        ]
        self.assertEqual(len(trashed_files), 1)
        with self.database.connect() as conn:
            catalog_path = conn.execute("SELECT path FROM files WHERE id=1").fetchone()["path"]
            trash_rows = conn.execute("SELECT COUNT(*) count FROM duplicate_trash").fetchone()["count"]
        self.assertEqual(catalog_path, str(self.source))
        self.assertEqual(trash_rows, 0)

    def test_trash_restore_post_move_identity_failure_is_reported_incomplete(self):
        trash_id = self.service.move(1, 30, None)
        with self.database.connect() as conn:
            trash_path = Path(conn.execute(
                "SELECT trash_path FROM duplicate_trash WHERE id=?", (trash_id,)
            ).fetchone()["trash_path"])

        real_identity = self.service._file_identity

        def fail_restored_identity(path: Path):
            candidate = Path(path)
            if candidate == self.source:
                raise OSError("synthetic post-restore identity failure")
            return real_identity(candidate)

        with patch.object(self.service, "_file_identity", side_effect=fail_restored_identity):
            with self.assertRaisesRegex(DuplicateTrashError, "Managed Trash restore is incomplete"):
                self.service.restore(trash_id)

        self.assertTrue(self.source.is_file())
        self.assertFalse(trash_path.exists())
        with self.database.connect() as conn:
            status = conn.execute(
                "SELECT status FROM duplicate_trash WHERE id=?", (trash_id,)
            ).fetchone()["status"]
            catalog_count = conn.execute("SELECT COUNT(*) count FROM files WHERE id=1").fetchone()["count"]
        self.assertEqual(status, "trashed")
        self.assertEqual(catalog_count, 0)


if __name__ == "__main__":
    unittest.main()

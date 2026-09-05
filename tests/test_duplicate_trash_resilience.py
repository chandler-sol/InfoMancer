from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.duplicate_trash import DuplicateTrashError, DuplicateTrashService


class DuplicateTrashRestoreResilienceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.database = Database(self.base / "catalog.db")
        self.database.initialize()
        self.service = DuplicateTrashService(self.database)
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO roots(id,path,kind,label) VALUES (1,?,'movie','Movies')",
                (str(self.base),),
            )
            conn.execute(
                """INSERT INTO titles(id,root_id,kind,title,folder_path,tmdb_id)
                   VALUES (1,1,'movie','Example Movie',?, '100')""",
                (str(self.base / "Example Movie"),),
            )
            conn.execute(
                """INSERT INTO files(
                     id,title_id,path,filename,extension,size_bytes,modified_at,
                     runtime_seconds,width,height,video_codec,bitrate,dynamic_range,
                     media_info_at,seen_scan
                   ) VALUES (1,1,?,'first.mkv','.mkv',5,1.0,7200,1920,1080,
                             'h264',4000000,'SDR','now','one')""",
                (str(self.base / "first.mkv"),),
            )

    def tearDown(self):
        self.temporary.cleanup()

    def test_non_object_snapshot_is_rejected_before_file_moves(self):
        original = self.base / "first.mkv"
        original.write_bytes(b"first")
        trash_id = self.service.move(1, 30, None)
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT trash_path FROM duplicate_trash WHERE id=?", (trash_id,)
            ).fetchone()
            trash_path = Path(row["trash_path"])
            conn.execute(
                "UPDATE duplicate_trash SET file_snapshot='[]' WHERE id=?",
                (trash_id,),
            )

        self.assertFalse(original.exists())
        self.assertTrue(trash_path.is_file())
        with self.assertRaises(DuplicateTrashError):
            self.service.restore(trash_id)

        self.assertFalse(original.exists())
        self.assertTrue(trash_path.is_file())
        with self.database.connect() as conn:
            status = conn.execute(
                "SELECT status FROM duplicate_trash WHERE id=?", (trash_id,)
            ).fetchone()["status"]
        self.assertEqual(status, "trashed")


if __name__ == "__main__":
    unittest.main()

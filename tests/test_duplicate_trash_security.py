from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.duplicate_trash import DuplicateTrashError, DuplicateTrashService


class DuplicateTrashSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "media"
        self.root.mkdir()
        self.database = Database(self.base / "catalog.db")
        self.database.initialize()
        self.trash = DuplicateTrashService(self.database)
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
                   ) VALUES (1,1,?,'first.mkv','.mkv',5,1.0,'one')""",
                (str(self.root / "first.mkv"),),
            )

    def tearDown(self):
        self.temporary.cleanup()

    def test_managed_trash_symlink_cannot_escape_media_root(self):
        original = self.root / "first.mkv"
        original.write_bytes(b"first")
        outside = self.base / "outside"
        outside.mkdir()
        managed = self.root / ".infomancer-trash"
        try:
            managed.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks are not available to this test runner")

        with self.assertRaises(DuplicateTrashError):
            self.trash.move(1, 30, None)
        self.assertTrue(original.exists())
        self.assertEqual(list(outside.iterdir()), [])

    def test_expired_purge_fails_closed_when_catalog_root_is_missing(self):
        source = (Path(__file__).resolve().parents[1] / "app/duplicate_trash.py").read_text(
            encoding="utf-8"
        )
        purge = source[source.index("    def purge_expired"):source.index("    def _catalog_file")]
        self.assertIn('if not row["root_path"]:', purge)
        self.assertIn("trash_root = self._managed_trash_root(root)", purge)
        self.assertIn("except DuplicateTrashError:", purge)


if __name__ == "__main__":
    unittest.main()

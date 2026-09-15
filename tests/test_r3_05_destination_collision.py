from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.safe_file_rename import SafeFileRenameError, SafeFileRenameService


class R305DanglingDestinationCollisionTests(unittest.TestCase):
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
            root_id = int(conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES (?,?,?)",
                (str(self.root), "tv", "TV"),
            ).lastrowid)
            self.title_id = int(conn.execute(
                "INSERT INTO titles(root_id,kind,title,folder_path) VALUES (?,?,?,?)",
                (root_id, "tv", "Example Show", str(self.show)),
            ).lastrowid)
            self.file_id = int(conn.execute(
                """INSERT INTO files(title_id,path,filename,extension,seen_scan)
                   VALUES (?,?,?,?,?)""",
                (self.title_id, str(self.source), self.source.name, ".mkv", "scan"),
            ).lastrowid)
        self.service = SafeFileRenameService(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def _make_dangling_symlink(path: Path, target: Path) -> None:
        try:
            path.symlink_to(target, target_is_directory=False)
        except (OSError, NotImplementedError) as exc:
            raise unittest.SkipTest("File symlinks are unavailable on this runner") from exc

    def test_file_rename_refuses_dangling_destination_entry(self):
        target = self.show / "new.mkv"
        missing = self.base / "missing-file"
        self._make_dangling_symlink(target, missing)
        self.assertTrue(os.path.lexists(target))
        self.assertFalse(target.exists())

        with self.assertRaisesRegex(SafeFileRenameError, "already exists at the rename destination"):
            self.service.rename_file(self.file_id, self.source, target)

        self.assertEqual(self.source.read_bytes(), b"media")
        self.assertTrue(os.path.lexists(target))

    def test_folder_rename_refuses_dangling_destination_entry(self):
        target = self.root / "Example Show (2020)"
        missing = self.base / "missing-folder"
        self._make_dangling_symlink(target, missing)
        self.assertTrue(os.path.lexists(target))
        self.assertFalse(target.exists())

        with self.assertRaisesRegex(SafeFileRenameError, "already exists at the rename destination"):
            self.service.rename_folder(self.title_id, self.show, target)

        self.assertEqual(self.source.read_bytes(), b"media")
        self.assertTrue(os.path.lexists(target))


if __name__ == "__main__":
    unittest.main()

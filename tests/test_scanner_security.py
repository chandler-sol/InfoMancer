from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.scanner import _walk_files


class ScannerSecurityTests(unittest.TestCase):
    def test_file_symlink_outside_source_is_not_catalog_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "media"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            normal = root / "inside.mkv"
            normal.write_bytes(b"inside")
            outside_file = outside / "outside.mkv"
            outside_file.write_bytes(b"outside")
            linked = root / "linked.mkv"
            try:
                linked.symlink_to(outside_file)
            except (OSError, NotImplementedError):
                self.skipTest("File symlinks are not available to this test runner")

            errors: list[str] = []
            files = list(_walk_files(root, errors))
            self.assertIn(normal, files)
            self.assertNotIn(linked, files)
            self.assertNotIn(outside_file, files)

    def test_directory_symlink_is_not_traversed(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "media"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            outside_file = outside / "episode.mkv"
            outside_file.write_bytes(b"outside")
            linked = root / "linked-season"
            try:
                linked.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Directory symlinks are not available to this test runner")

            files = list(_walk_files(root, []))
            self.assertNotIn(outside_file, files)
            self.assertEqual(files, [])


if __name__ == "__main__":
    unittest.main()

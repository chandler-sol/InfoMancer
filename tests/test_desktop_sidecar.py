from pathlib import Path
import unittest
from unittest import mock

from desktop import sidecar


class DesktopSidecarTests(unittest.TestCase):
    def test_inaccessible_media_root_is_skipped(self):
        blocked = OSError(1272, "Guest access is blocked")
        with mock.patch.object(sidecar.os, "scandir", side_effect=blocked):
            self.assertFalse(sidecar._root_is_accessible(Path("B:/")))

    def test_media_root_deduplication_does_not_resolve_filesystem_paths(self):
        root = Path("B:/")
        with mock.patch.object(Path, "resolve", side_effect=OSError(1272, "blocked")):
            self.assertEqual(sidecar._dedupe_media_roots([root]), [root])


if __name__ == "__main__":
    unittest.main()

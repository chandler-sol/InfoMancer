from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from desktop import sidecar


class DesktopSidecarTests(unittest.TestCase):
    def test_inaccessible_media_root_is_skipped(self):
        blocked = OSError(1272, "Guest access is blocked")
        with mock.patch.object(sidecar.os, "scandir", side_effect=blocked):
            self.assertFalse(sidecar._root_is_accessible(Path("B:/")))

    def test_windows_drive_mask_includes_mapped_drive_letters(self):
        mask = (1 << 2) | (1 << 13) | (1 << 25)  # C, N, Z
        self.assertEqual(
            sidecar._windows_drive_strings_from_mask(mask),
            ["C:\\", "N:\\", "Z:\\"],
        )

    def test_windows_logical_drives_use_win32_drive_table(self):
        mask = (1 << 2) | (1 << 13)  # C, N
        fake_ctypes = SimpleNamespace(
            windll=SimpleNamespace(
                kernel32=SimpleNamespace(GetLogicalDrives=lambda: mask)
            )
        )
        with mock.patch.dict(sys.modules, {"ctypes": fake_ctypes}):
            drives = sidecar._windows_logical_drives()
        self.assertEqual([str(path) for path in drives], ["C:\\", "N:\\"])

    def test_media_root_deduplication_does_not_resolve_filesystem_paths(self):
        root = Path("B:/")
        with mock.patch.object(Path, "resolve", side_effect=OSError(1272, "blocked")):
            self.assertEqual(sidecar._dedupe_media_roots([root]), [root])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SIDECAR_PATH = ROOT / "desktop" / "sidecar.py"


def _load_sidecar():
    spec = importlib.util.spec_from_file_location("infomancer_desktop_sidecar", SIDECAR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load desktop sidecar for tests")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DesktopSidecarDriveDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sidecar = _load_sidecar()

    def test_windows_drive_mask_includes_mapped_drive_letters(self):
        mask = (1 << 2) | (1 << 13) | (1 << 25)  # C, N, Z
        self.assertEqual(
            self.sidecar._windows_drive_strings_from_mask(mask),
            ["C:\\", "N:\\", "Z:\\"],
        )

    def test_windows_default_roots_use_drive_table_without_exists_probe(self):
        network_roots = [Path("N:/"), Path("Z:/")]
        with patch.object(self.sidecar.os, "name", "nt"), patch.object(
            self.sidecar, "_windows_logical_drives", return_value=network_roots
        ):
            roots = self.sidecar._default_media_roots()
        self.assertTrue(all(root in roots for root in network_roots))

    def test_root_deduplication_does_not_resolve_filesystem(self):
        missing = Path("definitely-not-mounted")
        with patch.object(Path, "resolve", side_effect=AssertionError("resolve should not run")):
            roots = self.sidecar._dedupe_media_roots([missing, missing])
        self.assertEqual(roots, [missing])


if __name__ == "__main__":
    unittest.main()

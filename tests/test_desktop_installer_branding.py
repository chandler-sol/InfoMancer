from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
TAURI_CONFIG = ROOT / "desktop" / "src-tauri" / "tauri.conf.json"


class DesktopInstallerBrandingTests(unittest.TestCase):
    def test_native_packages_use_infomancer_icon_set(self) -> None:
        config = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
        bundle = config["bundle"]
        icons = set(bundle["icon"])

        self.assertIn("icons/icon.ico", icons)
        self.assertIn("icons/icon.icns", icons)
        self.assertIn("icons/128x128.png", icons)

        nsis = bundle["windows"]["nsis"]
        self.assertEqual(nsis["installerIcon"], "icons/icon.ico")
        self.assertEqual(nsis["uninstallerIcon"], "icons/icon.ico")


if __name__ == "__main__":
    unittest.main()

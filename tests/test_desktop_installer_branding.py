from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
TAURI_CONFIG = ROOT / "desktop" / "src-tauri" / "tauri.conf.json"
INSTALLER_HOOKS = ROOT / "desktop" / "src-tauri" / "windows" / "hooks.nsh"
WINDOWS_PREVIEW = ROOT / ".github" / "workflows" / "windows-preview.yml"


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

    def test_nsis_installer_displays_build_commit(self) -> None:
        hooks = INSTALLER_HOOKS.read_text(encoding="utf-8")
        self.assertIn('!define INFOMANCER_BUILD_COMMIT "local"', hooks)
        self.assertIn('!define INFOMANCER_BUILD_COMMIT_FULL "local"', hooks)
        self.assertIn('BrandingText "InfoMancer build ${INFOMANCER_BUILD_COMMIT}"', hooks)
        self.assertIn(
            'DetailPrint "InfoMancer build commit: ${INFOMANCER_BUILD_COMMIT_FULL}"',
            hooks,
        )

    def test_windows_preview_stamps_and_names_commit_build(self) -> None:
        workflow = WINDOWS_PREVIEW.read_text(encoding="utf-8")
        self.assertIn("name: Stamp installer build identity", workflow)
        self.assertIn("PREVIEW_SHORT_SHA=$shortSha", workflow)
        self.assertIn("INFOMANCER_BUILD_COMMIT", workflow)
        self.assertIn("INFOMANCER_BUILD_COMMIT_FULL", workflow)
        self.assertIn("-commit-$($env:PREVIEW_SHORT_SHA)-setup.exe", workflow)
        self.assertIn("gh release delete-asset", workflow)


if __name__ == "__main__":
    unittest.main()

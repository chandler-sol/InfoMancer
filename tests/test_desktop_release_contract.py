import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DesktopReleaseContractTests(unittest.TestCase):
    def test_windows_launcher_uses_gui_subsystem(self):
        source = (ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            '#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]',
            source,
        )

    def test_windows_launcher_surfaces_and_logs_startup_failures(self):
        source = (ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text(
            encoding="utf-8"
        )
        for expected in (
            "desktop-launcher.log",
            "install_panic_logger",
            "InfoMancer startup error",
            "Tauri startup failed",
            "Tauri application built successfully; entering the desktop event loop.",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, source)

    def test_preview_updater_plugin_configuration_deserializes(self):
        config = json.loads(
            (ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text(
                encoding="utf-8"
            )
        )
        updater = config.get("plugins", {}).get("updater")
        self.assertIsInstance(updater, dict)
        self.assertIn("pubkey", updater)
        self.assertIsInstance(updater["pubkey"], str)
        self.assertEqual(updater["pubkey"], "")
        self.assertEqual(updater.get("endpoints"), [])

    def test_draft_windows_sidecar_is_built_without_console(self):
        workflow = (ROOT / ".github" / "workflows" / "draft-08-release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("PyInstaller --noconfirm --clean --onefile --noconsole", workflow)
        self.assertIn("Verify Windows launcher uses GUI subsystem", workflow)

    def test_draft_release_launches_installed_windows_app(self):
        workflow = (ROOT / ".github" / "workflows" / "draft-08-release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("Smoke-test installed Windows desktop launch", workflow)
        self.assertIn("Start-Process -FilePath $launcher.FullName -PassThru", workflow)
        self.assertIn("desktop-launcher.log", workflow)
        self.assertIn(
            "Tauri application built successfully; entering the desktop event loop.",
            workflow,
        )

    def test_installation_guide_documents_native_packages(self):
        guide = (ROOT / "docs" / "INSTALLATION.md").read_text(encoding="utf-8")
        for expected in (
            "InfoMancer_0.8.0-alpha.1_x64-setup.exe",
            "InfoMancer_0.8.0-alpha.1_aarch64.dmg",
            "InfoMancer_0.8.0-alpha.1_amd64.deb",
            "InfoMancer_0.8.0-alpha.1_amd64.AppImage",
            "Run on this computer",
            "Connect to a server",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, guide)


if __name__ == "__main__":
    unittest.main()

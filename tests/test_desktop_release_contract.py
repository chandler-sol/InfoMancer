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

    def test_active_windows_sidecar_is_built_without_console(self):
        workflow = (ROOT / ".github" / "workflows" / "windows-desktop.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("PyInstaller --noconfirm --clean --onefile --noconsole", workflow)
        self.assertIn("Verify Windows launcher uses GUI subsystem", workflow)
        self.assertIn("--check-ffprobe", workflow)

    def test_active_windows_build_launches_installed_app(self):
        workflow = (ROOT / ".github" / "workflows" / "windows-desktop.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("Smoke-test installed launch and zero-residue silent uninstall", workflow)
        self.assertIn("Start-Process -FilePath $launcher.FullName -PassThru", workflow)
        self.assertIn("desktop-launcher.log", workflow)
        self.assertIn(
            "Tauri application built successfully; entering the desktop event loop.",
            workflow,
        )

    def test_tagged_release_stays_draft_until_ffprobe_source_is_uploaded(self):
        workflow = (ROOT / ".github" / "workflows" / "windows-desktop-release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("permissions:\n  contents: write", workflow)
        self.assertIn("releaseDraft: true", workflow)
        self.assertIn("Upload FFprobe compliance assets to draft release", workflow)
        self.assertIn("ffmpeg-source-$ffmpegCommit.tar.gz", workflow)
        self.assertIn("Publish verified Windows release", workflow)
        self.assertIn("gh release edit $env:GITHUB_REF_NAME --draft=false --prerelease", workflow)
        self.assertLess(
            workflow.index("Upload FFprobe compliance assets to draft release"),
            workflow.index("Publish verified Windows release"),
        )

    def test_installation_guide_documents_current_native_packages(self):
        guide = (ROOT / "docs" / "INSTALLATION.md").read_text(encoding="utf-8")
        for expected in (
            "InfoMancer-0.8.1-beta.2-Windows-x64-Setup.exe",
            "InfoMancer-0.8.1-beta.2-macOS-Apple-Silicon.dmg",
            "InfoMancer-0.8.1-beta.2-macOS-Intel.dmg",
            "InfoMancer-0.8.1-beta.2-Linux-x86_64.deb",
            "InfoMancer-0.8.1-beta.2-Linux-x86_64.AppImage",
            "InfoMancer-Server-0.8.1-beta.2.zip",
            "Run on this computer",
            "Connect to a server",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, guide)
        self.assertNotIn("0.8.1-beta.1", guide)


if __name__ == "__main__":
    unittest.main()
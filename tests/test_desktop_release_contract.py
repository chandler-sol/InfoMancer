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

    def test_draft_windows_sidecar_is_built_without_console(self):
        workflow = (ROOT / ".github" / "workflows" / "draft-08-release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("PyInstaller --noconfirm --clean --onefile --noconsole", workflow)
        self.assertIn("Verify Windows launcher uses GUI subsystem", workflow)

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

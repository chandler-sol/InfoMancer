import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReleaseAssetNameContracts(unittest.TestCase):
    def test_native_builds_flow_through_human_readable_filename_wrapper(self):
        package = json.loads((ROOT / "desktop/package.json").read_text(encoding="utf-8"))
        wrapper = (ROOT / "desktop/scripts/tauri-wrapper.mjs").read_text(encoding="utf-8")
        release_workflow = (ROOT / ".github/workflows/draft-08-release.yml").read_text(encoding="utf-8")

        self.assertEqual(package["scripts"]["tauri"], "node scripts/tauri-wrapper.mjs")
        self.assertIn("npm run tauri -- build --bundles ${{ matrix.bundles }}", release_workflow)

        for label in (
            "Windows-x64-Setup",
            "macOS-Apple-Silicon",
            "macOS-Intel",
            "Linux-x86_64",
        ):
            self.assertIn(label, wrapper)

        self.assertIn("InfoMancer-${version}-${label}${extension}", wrapper)

    def test_wrapper_preserves_normal_tauri_commands_and_renames_only_completed_builds(self):
        wrapper = (ROOT / "desktop/scripts/tauri-wrapper.mjs").read_text(encoding="utf-8")

        self.assertIn("if (args[0] !== 'build')", wrapper)
        self.assertIn("renameSingleBundle('nsis', '.exe')", wrapper)
        self.assertIn("renameSingleBundle('dmg', '.dmg')", wrapper)
        self.assertIn("renameSingleBundle('deb', '.deb')", wrapper)
        self.assertIn("renameSingleBundle('appimage', '.AppImage')", wrapper)


if __name__ == "__main__":
    unittest.main()

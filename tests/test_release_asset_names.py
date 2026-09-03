from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReleaseAssetNameContracts(unittest.TestCase):
    def test_native_release_assets_use_human_readable_platform_names(self):
        workflow = (ROOT / ".github/workflows/draft-08-release.yml").read_text(encoding="utf-8")

        for label in (
            "Windows-x64-Setup",
            "macOS-Apple-Silicon",
            "macOS-Intel",
            "Linux-x86_64",
        ):
            self.assertIn(f"asset_label: {label}", workflow)

        self.assertIn("ASSET_LABEL: ${{ matrix.asset_label }}", workflow)
        self.assertIn("InfoMancer-{version}-{asset_label}{extension}", workflow)
        self.assertIn("-Server-Source.zip", workflow)

    def test_draft_refresh_removes_legacy_machine_oriented_asset_names(self):
        workflow = (ROOT / ".github/workflows/draft-08-release.yml").read_text(encoding="utf-8")

        for legacy_pattern in (
            "InfoMancer_*_aarch64.dmg",
            "InfoMancer_*_x64.dmg",
            "InfoMancer_*_amd64.deb",
            "InfoMancer_*_amd64.AppImage",
            "InfoMancer_*_x64-setup.exe",
            "InfoMancer_*_x64-commit-*-setup.exe",
        ):
            self.assertIn(legacy_pattern, workflow)


if __name__ == "__main__":
    unittest.main()

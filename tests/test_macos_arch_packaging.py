from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MacOsArchitecturePackagingContracts(unittest.TestCase):
    def test_release_builds_native_apple_silicon_and_intel_dmgs(self):
        workflow = (ROOT / ".github/workflows/draft-08-release.yml").read_text(encoding="utf-8")

        self.assertIn("- os: macos-26\n            label: macOS Apple Silicon", workflow)
        self.assertIn("slug: macos-arm64", workflow)
        self.assertIn("- os: macos-26-intel\n            label: macOS Intel", workflow)
        self.assertIn("slug: macos-intel", workflow)
        self.assertGreaterEqual(workflow.count("asset_os: macos"), 2)
        self.assertIn("RELEASE_OS: ${{ matrix.asset_os }}", workflow)
        self.assertIn("name: infomancer-${{ matrix.slug }}", workflow)

    def test_ffprobe_is_pinned_for_both_mac_architectures(self):
        stage = (ROOT / "scripts/stage_ffprobe.py").read_text(encoding="utf-8")

        self.assertIn('(\"darwin\", \"x86_64\")', stage)
        self.assertIn('"slug": "darwin-x64"', stage)
        self.assertIn('(\"darwin\", \"arm64\")', stage)
        self.assertIn('"slug": "darwin-arm64"', stage)


if __name__ == "__main__":
    unittest.main()

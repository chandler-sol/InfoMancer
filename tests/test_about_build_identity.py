from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
BUILD_INFO = ROOT / "app" / "static" / "build-info.json"
ABOUT = ROOT / "app" / "templates" / "about.html"
STAGE_FFPROBE = ROOT / "scripts" / "stage_ffprobe.py"


class AboutBuildIdentityTests(unittest.TestCase):
    def test_repository_has_safe_local_build_identity(self) -> None:
        build = json.loads(BUILD_INFO.read_text(encoding="utf-8"))
        self.assertEqual(build["commit"], "local")
        self.assertEqual(build["short_commit"], "local")

    def test_packaging_stamps_runtime_manifest_before_sidecar_bundle(self) -> None:
        script = STAGE_FFPROBE.read_text(encoding="utf-8")
        self.assertIn('os.environ.get("PREVIEW_SHA")', script)
        self.assertIn('os.environ.get("GITHUB_SHA")', script)
        self.assertIn('Path("app/static/build-info.json")', script)
        self.assertIn("_write_build_identity()", script)

    def test_about_page_displays_stamped_commit(self) -> None:
        template = ABOUT.read_text(encoding="utf-8")
        self.assertIn('id="about-build"', template)
        self.assertIn("/static/build-info.json", template)
        self.assertIn("Build ${shortCommit}", template)
        self.assertIn("Commit ${fullCommit}", template)


if __name__ == "__main__":
    unittest.main()

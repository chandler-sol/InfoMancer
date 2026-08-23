from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ShowcaseScreenshotToolingTests(unittest.TestCase):
    def test_playwright_dependency_is_pinned(self) -> None:
        package = json.loads((ROOT / "tools" / "showcase" / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["dependencies"]["playwright"], "1.62.1")
        self.assertEqual(package["scripts"]["capture"], "node capture.mjs")

    def test_capture_script_has_valid_javascript_syntax_when_node_is_available(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is not installed on this test host")
        subprocess.run(
            [node, "--check", str(ROOT / "tools" / "showcase" / "capture.mjs")],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_capture_script_has_core_showcase_states_and_sizes(self) -> None:
        script = (ROOT / "tools" / "showcase" / "capture.mjs").read_text(encoding="utf-8")
        for state in ("dashboard", "library", "library-inspector", "title-detail", "review"):
            self.assertIn(f'slug: "{state}"', script)
        for dimensions in ("width: 1440, height: 900", "width: 1200, height: 675", "width: 390, height: 844"):
            self.assertIn(dimensions, script)
        self.assertIn('name: "infomancer_library_view"', script)
        self.assertIn('value: "covers"', script)

    def test_capture_script_keeps_credentials_out_of_manifest(self) -> None:
        script = (ROOT / "tools" / "showcase" / "capture.mjs").read_text(encoding="utf-8")
        manifest_block = script.split("const manifest =", 1)[1].split("};", 1)[0]
        self.assertNotIn("USERNAME", manifest_block)
        self.assertNotIn("PASSWORD", manifest_block)
        self.assertIn("INFOMANCER_SHOWCASE_PASSWORD", script)

    def test_windows_wrapper_uses_secure_password_prompt(self) -> None:
        wrapper = (ROOT / "scripts" / "capture-showcase.ps1").read_text(encoding="utf-8")
        self.assertIn("Read-Host", wrapper)
        self.assertIn("-AsSecureString", wrapper)
        self.assertIn("ZeroFreeBSTR", wrapper)

    def test_generated_screenshot_directory_is_ignored(self) -> None:
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("showcase/screenshots/", ignored)


if __name__ == "__main__":
    unittest.main()

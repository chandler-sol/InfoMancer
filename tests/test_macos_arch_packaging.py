from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_macos_auditor():
    path = ROOT / "scripts/verify_macos_minos.py"
    spec = importlib.util.spec_from_file_location("verify_macos_minos", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MacOsArchitecturePackagingContracts(unittest.TestCase):
    def test_legacy_multiplatform_release_path_is_not_active_on_09(self):
        self.assertFalse((ROOT / ".github/workflows/draft-08-release.yml").exists())
        self.assertFalse((ROOT / ".github/workflows/windows-preview.yml").exists())

    def test_macos_ffprobe_bundling_requires_separate_review(self):
        stage = (ROOT / "scripts/stage_ffprobe.py").read_text(encoding="utf-8")
        distribution = (ROOT / "docs/FFPROBE_DISTRIBUTION.md").read_text(encoding="utf-8")

        self.assertIn("Windows FFprobe redistribution check must execute on Windows", stage)
        self.assertNotIn('(\"darwin\", \"x86_64\")', stage)
        self.assertNotIn('(\"darwin\", \"arm64\")', stage)
        self.assertIn("Linux and macOS native FFprobe bundling are not approved", distribution)
        self.assertIn("adding native Linux, macOS, or Windows ARM64 FFprobe bundling", distribution)

    def test_macos_auditor_ignores_linker_tool_version(self):
        auditor = _load_macos_auditor()
        output = """
Load command 10
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform MACOS
    minos 13.0
      sdk 26.0
   ntools 1
     tool LD
  version 1267.0
"""
        self.assertEqual(auditor._parse_minimum_versions(output), ["13.0"])

    def test_macos_auditor_accepts_legacy_version_min_command(self):
        auditor = _load_macos_auditor()
        output = """
Load command 9
      cmd LC_VERSION_MIN_MACOSX
  cmdsize 16
  version 12.0
      sdk 13.3
"""
        self.assertEqual(auditor._parse_minimum_versions(output), ["12.0"])

    def test_macos_launcher_log_uses_persistent_application_support(self):
        launcher = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")

        self.assertIn('home.push("Library")', launcher)
        self.assertIn('home.push("Application Support")', launcher)
        self.assertIn('home.push("cloud.arsenik.infomancer")', launcher)
        self.assertIn('launcher_log_path().display()', launcher)


if __name__ == "__main__":
    unittest.main()

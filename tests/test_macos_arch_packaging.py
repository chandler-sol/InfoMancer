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

    def test_intel_macos_cryptography_is_built_with_static_openssl(self):
        workflow = (ROOT / ".github/workflows/draft-08-release.yml").read_text(encoding="utf-8")

        self.assertIn("Build Intel macOS cryptography with static OpenSSL", workflow)
        self.assertIn("if: matrix.slug == 'macos-intel'", workflow)
        self.assertIn("OPENSSL_STATIC=1", workflow)
        self.assertIn("OPENSSL_DIR=\"$(brew --prefix openssl@3)\"", workflow)
        self.assertIn("--no-binary cryptography", workflow)
        self.assertIn("Verify macOS cryptography OpenSSL linkage", workflow)
        self.assertIn("otool -L", workflow)

    def test_packaged_macos_core_is_started_during_release_smoke_test(self):
        workflow = (ROOT / ".github/workflows/draft-08-release.yml").read_text(encoding="utf-8")

        self.assertIn("Smoke-test packaged macOS core startup", workflow)
        self.assertIn("./dist/infomancer-core --port", workflow)
        self.assertIn("--data-dir \"$smoke_dir\"", workflow)
        self.assertIn("socket.create_connection(('127.0.0.1', port)", workflow)

    def test_intel_priority_build_targets_ventura_and_audits_embedded_binaries(self):
        workflow = (ROOT / ".github/workflows/macos-intel-priority.yml").read_text(encoding="utf-8")
        auditor = (ROOT / "scripts/verify_macos_minos.py").read_text(encoding="utf-8")

        self.assertIn("MACOSX_DEPLOYMENT_TARGET: '13.0'", workflow)
        self.assertIn("CMAKE_OSX_DEPLOYMENT_TARGET: '13.0'", workflow)
        self.assertIn("macos13", workflow)
        self.assertIn('TMPDIR="$pyi_tmp" ./dist/infomancer-core', workflow)
        self.assertIn("verify_macos_minos.py", workflow)
        self.assertIn("Verify finished Intel DMG supports macOS 13", workflow)
        self.assertIn('"xcrun", "vtool", "-show-build"', auditor)
        self.assertIn("newer than supported", auditor)

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

    def test_normal_release_preserves_intel_ventura_contract(self):
        workflow = (ROOT / ".github/workflows/draft-08-release.yml").read_text(encoding="utf-8")

        self.assertIn("Configure Intel macOS 13 deployment target", workflow)
        self.assertIn("MACOSX_DEPLOYMENT_TARGET=13.0", workflow)
        self.assertIn("CMAKE_OSX_DEPLOYMENT_TARGET=13.0", workflow)
        self.assertIn("compat_key: macos13", workflow)
        self.assertIn('TMPDIR="$pyi_tmp" ./dist/infomancer-core', workflow)
        self.assertIn("Verify finished Intel app supports macOS 13", workflow)
        self.assertIn("scripts/verify_macos_minos.py", workflow)
        self.assertIn("testing/0.8-beta", workflow)
        self.assertNotIn("testing/0.8-alpha", workflow)

    def test_macos_launcher_log_uses_persistent_application_support(self):
        launcher = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")

        self.assertIn('home.push("Library")', launcher)
        self.assertIn('home.push("Application Support")', launcher)
        self.assertIn('home.push("cloud.arsenik.infomancer")', launcher)
        self.assertIn('launcher_log_path().display()', launcher)


if __name__ == "__main__":
    unittest.main()

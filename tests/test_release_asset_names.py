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

    def test_signed_updater_bundle_keeps_signature_on_matching_friendly_basename(self):
        wrapper = (ROOT / "desktop/scripts/tauri-wrapper.mjs").read_text(encoding="utf-8")
        updater_config = (ROOT / "desktop/src-tauri/tauri.release.conf.json").read_text(encoding="utf-8")

        self.assertIn('"createUpdaterArtifacts": true', updater_config)
        self.assertIn("const sourceSignature = `${source}.sig`", wrapper)
        self.assertIn("const destinationSignature = `${destination}.sig`", wrapper)
        self.assertIn("renameSync(sourceSignature, destinationSignature)", wrapper)

    def test_shared_package_is_named_infomancer_server(self):
        builder = (ROOT / "scripts/build_release.py").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/draft-08-release.yml").read_text(encoding="utf-8")
        installation = (ROOT / "docs/INSTALLATION.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn('package_name = f"InfoMancer-Server-{version}"', builder)
        # The existing broad staging glob intentionally picks up the newly named
        # Server archive without coupling the release workflow to one beta label.
        self.assertIn("cp dist/InfoMancer-*.zip release-assets/", workflow)
        self.assertIn("InfoMancer-Server-0.8.1-beta.2.zip", installation)
        self.assertIn("InfoMancer-Server-0.8.1-beta.2.zip", readme)
        self.assertNotIn("InfoMancer-0.8.1-beta.1", installation)
        self.assertNotIn("InfoMancer-0.8.1-beta.1", readme)

    def test_server_handoff_explains_lan_bootstrap_and_media_mapping(self):
        installation = (ROOT / "docs/INSTALLATION.md").read_text(encoding="utf-8")
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

        self.assertIn("Only change `source:`", installation)
        self.assertIn("InfoMancer first-run bootstrap token:", installation)
        self.assertIn("http://SERVER-IP:8787", installation)
        self.assertIn("Do not port-forward", installation)
        self.assertIn("${INFOMANCER_BIND_ADDRESS:-127.0.0.1}:8787:8787", compose)
        self.assertIn("INFOMANCER_BIND_ADDRESS=0.0.0.0", env_example)


if __name__ == "__main__":
    unittest.main()
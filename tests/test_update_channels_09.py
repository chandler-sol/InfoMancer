from pathlib import Path
import tempfile
import unittest

from app.update_channels import (
    channel_transition,
    read_update_channel,
    release_channel,
    select_release,
    update_state,
    validate_channel_manifest,
    version_key,
    write_update_channel,
)


ROOT = Path(__file__).resolve().parents[1]


def qualified_manifest(channel: str = "dev") -> dict:
    return {
        "schema_version": 1,
        "channel": channel,
        "version": "0.9.0-dev.2384" if channel == "dev" else "0.9.0-beta.1",
        "build_id": "qualified-build-2384",
        "commit_sha": "0123456789abcdef0123456789abcdef01234567",
        "qualified_at": "2026-09-12T17:45:00+00:00",
        "qualification": {
            "status": "passed",
            "workflow": "Tests",
            "run_id": 2384,
            "run_url": "https://github.com/chandler-sol/InfoMancer/actions/runs/2384",
            "gates": [
                "python-windows",
                "python-macos",
                "python-linux",
                "security-audit",
                "browser-acceptance",
            ],
        },
        "database_schema": {
            "current": 17,
            "minimum_reader_schema": 1,
            "minimum_writer_schema": 1,
            "downgrade_policy": "compatible",
        },
        "artifacts": {
            "windows": {
                "kind": "tauri-updater",
                "url": "https://example.invalid/InfoMancer.exe",
                "sha256": "a" * 64,
            }
        },
    }


class UpdateChannel09Tests(unittest.TestCase):
    def test_release_channel_classification(self):
        self.assertEqual(release_channel("v1.2.3"), "standard")
        self.assertEqual(release_channel("v1.2.3-beta.4", True), "beta")
        self.assertEqual(release_channel("v1.2.3-rc.1", True), "beta")
        self.assertEqual(release_channel("v1.2.3-dev.91", True), "dev")
        self.assertEqual(release_channel("v1.2.3-alpha.2", True), "dev")
        self.assertEqual(release_channel("v1.2.3-preview.8", True), "dev")
        self.assertEqual(release_channel("v1.2.3-experimental.1", True), "beta")

    def test_semver_stage_ordering(self):
        self.assertLess(version_key("0.9.0-dev.12"), version_key("0.9.0-beta.1"))
        self.assertLess(version_key("0.9.0-beta.9"), version_key("0.9.0"))
        self.assertLess(version_key("0.9.0-dev.9"), version_key("0.9.0-dev.10"))

    def test_channel_selection_never_leaks_less_stable_release(self):
        releases = [
            {"tag_name": "v0.8.2", "draft": False, "prerelease": False},
            {"tag_name": "v0.9.0-beta.2", "draft": False, "prerelease": True},
            {"tag_name": "v0.10.0-dev.18", "draft": False, "prerelease": True},
            {"tag_name": "v9.9.9-dev.999", "draft": True, "prerelease": True},
        ]
        self.assertEqual(select_release(releases, "standard")["tag_name"], "v0.8.2")
        self.assertEqual(select_release(releases, "beta")["tag_name"], "v0.9.0-beta.2")
        self.assertEqual(select_release(releases, "dev")["tag_name"], "v0.10.0-dev.18")

    def test_switching_to_more_stable_channel_never_requests_downgrade(self):
        self.assertEqual(channel_transition("dev", "standard"), "more_stable")
        self.assertEqual(update_state("0.10.0-dev.18", "0.9.0"), "waiting_for_channel")
        self.assertEqual(update_state("0.9.0-beta.1", "0.9.0-beta.2"), "available")

    def test_channel_preference_defaults_to_standard_and_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "infomancer.db"
            self.assertEqual(read_update_channel(database), "standard")
            write_update_channel(database, "dev")
            self.assertEqual(read_update_channel(database), "dev")
            (Path(directory) / "update-channel.json").write_text("not-json", encoding="utf-8")
            self.assertEqual(read_update_channel(database), "standard")

    def test_valid_qualified_manifest_is_normalized(self):
        manifest = validate_channel_manifest(qualified_manifest(), "dev")
        self.assertEqual(manifest["channel"], "dev")
        self.assertEqual(manifest["version"], "0.9.0-dev.2384")
        self.assertEqual(manifest["qualification"]["status"], "passed")
        self.assertEqual(manifest["database_schema"]["current"], 17)
        self.assertEqual(manifest["database_schema"]["downgrade_policy"], "compatible")

    def test_manifest_for_wrong_channel_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "selected channel"):
            validate_channel_manifest(qualified_manifest("beta"), "dev")

    def test_manifest_without_passed_qualification_is_rejected(self):
        manifest = qualified_manifest()
        manifest["qualification"]["status"] = "failed"
        with self.assertRaisesRegex(ValueError, "passed qualification"):
            validate_channel_manifest(manifest, "dev")

    def test_manifest_with_invalid_artifact_digest_is_rejected(self):
        manifest = qualified_manifest()
        manifest["artifacts"]["windows"]["sha256"] = "not-a-digest"
        with self.assertRaisesRegex(ValueError, "artifact metadata"):
            validate_channel_manifest(manifest, "dev")

    def test_manifest_with_plain_http_artifact_is_rejected(self):
        manifest = qualified_manifest()
        manifest["artifacts"]["windows"]["url"] = "http://example.invalid/InfoMancer.exe"
        with self.assertRaisesRegex(ValueError, "artifact metadata"):
            validate_channel_manifest(manifest, "dev")

    def test_manifest_without_schema_contract_is_rejected(self):
        manifest = qualified_manifest()
        del manifest["database_schema"]
        with self.assertRaisesRegex(ValueError, "database schema contract"):
            validate_channel_manifest(manifest, "dev")

    def test_manifest_with_impossible_schema_contract_is_rejected(self):
        manifest = qualified_manifest()
        manifest["database_schema"]["minimum_writer_schema"] = 18
        with self.assertRaisesRegex(ValueError, "minimum writer schema"):
            validate_channel_manifest(manifest, "dev")

    def test_updates_page_and_routes_are_registered(self):
        routes = (ROOT / "app" / "routes" / "__init__.py").read_text(encoding="utf-8")
        handler = (ROOT / "app" / "routes" / "update_channel_settings.py").read_text(encoding="utf-8")
        nav = (ROOT / "app" / "templates" / "_settings_nav.html").read_text(encoding="utf-8")
        template = (ROOT / "app" / "templates" / "settings_updates.html").read_text(encoding="utf-8")
        self.assertIn("build_update_channel_settings_router", routes)
        self.assertIn('"/settings/updates"', handler)
        self.assertIn('"/settings/updates/channel"', handler)
        self.assertIn('"/settings/updates/check"', handler)
        self.assertIn('"/settings/updates/apply"', handler)
        self.assertIn("INFOMANCER_UPDATE_MANIFEST_BASE_URL", handler)
        self.assertIn("validate_channel_manifest", handler)
        self.assertIn("assess_schema_downgrade", handler)
        self.assertIn("schema_compatibility_history", handler)
        self.assertIn('href="/settings/updates"', nav)
        self.assertIn("update_channel_labels", template)
        self.assertIn("Dev only advances after qualification succeeds", template)
        self.assertIn("never silently downgrades", template)
        self.assertIn("Source commit", template)
        self.assertIn("Qualification gates", template)
        self.assertIn("update_status.installable", template)
        self.assertIn("Schema compatibility ledger", template)
        self.assertIn("Migration compatibility history", template)
        self.assertIn("Oldest compatible writer", template)


if __name__ == "__main__":
    unittest.main()

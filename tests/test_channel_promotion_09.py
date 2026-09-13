from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from app.update_channels import validate_channel_manifest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "infomancer_promote_update_channel", ROOT / "scripts" / "promote_update_channel_manifest.py"
)
assert SPEC and SPEC.loader
promotion = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(promotion)


class ChannelPromotion09Tests(unittest.TestCase):
    def source_manifest(self, channel: str = "dev", version: str = "0.9.0-dev.2431") -> dict:
        return {
            "schema_version": 1,
            "channel": channel,
            "version": version,
            "build_id": "dev-2431-aaaaaaaa",
            "commit_sha": "a" * 40,
            "qualified_at": "2026-09-13T00:14:00+00:00",
            "qualification": {
                "status": "passed",
                "workflow": "Tests",
                "run_id": 34727256423,
                "run_url": "https://github.com/chandler-sol/InfoMancer/actions/runs/34727256423",
                "gates": ["python-windows", "python-macos", "python-linux", "security", "browser"],
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
                    "url": "https://example.invalid/dev.exe",
                    "sha256": "1" * 64,
                }
            },
            "release_notes_url": "https://example.invalid/dev-notes",
        }

    def replacement_artifact(self) -> dict:
        return {
            "windows": {
                "kind": "tauri-updater",
                "url": "https://example.invalid/promoted.exe",
                "sha256": "2" * 64,
            }
        }

    def test_dev_to_beta_preserves_immutable_build_and_qualification(self):
        source = self.source_manifest()
        promoted = promotion.promote_manifest(
            source,
            target_channel="beta",
            target_version="0.9.0-beta.1",
            artifacts=self.replacement_artifact(),
            promoted_at="2026-09-13T01:00:00+00:00",
        )
        self.assertEqual(promoted["channel"], "beta")
        self.assertEqual(promoted["version"], "0.9.0-beta.1")
        self.assertEqual(promoted["build_id"], source["build_id"])
        self.assertEqual(promoted["commit_sha"], source["commit_sha"])
        self.assertEqual(promoted["qualified_at"], source["qualified_at"])
        self.assertEqual(promoted["qualification"], source["qualification"])
        self.assertEqual(promoted["database_schema"], source["database_schema"])
        self.assertEqual(promoted["artifacts"], self.replacement_artifact())
        self.assertEqual(promoted["promotion"]["source_channel"], "dev")
        self.assertEqual(promoted["promotion"]["source_build_id"], source["build_id"])
        self.assertEqual(promoted["promotion"]["source_commit_sha"], source["commit_sha"])
        validate_channel_manifest(promoted, "beta")

    def test_beta_to_standard_is_supported(self):
        source = self.source_manifest("beta", "0.9.0-beta.4")
        promoted = promotion.promote_manifest(
            source,
            target_channel="standard",
            target_version="0.9.0",
            artifacts=self.replacement_artifact(),
            promoted_at="2026-09-13T02:00:00+00:00",
        )
        self.assertEqual(promoted["channel"], "standard")
        self.assertEqual(promoted["promotion"]["source_channel"], "beta")
        validate_channel_manifest(promoted, "standard")

    def test_promotion_never_silently_reuses_versioned_artifacts(self):
        with self.assertRaisesRegex(ValueError, "artifact metadata"):
            promotion.promote_manifest(
                self.source_manifest(),
                target_channel="beta",
                target_version="0.9.0-beta.1",
                artifacts={},
                promoted_at="2026-09-13T01:00:00+00:00",
            )

    def test_target_version_must_match_target_channel(self):
        with self.assertRaisesRegex(ValueError, "belongs to standard"):
            promotion.promote_manifest(
                self.source_manifest(),
                target_channel="beta",
                target_version="0.9.0",
                artifacts=self.replacement_artifact(),
                promoted_at="2026-09-13T01:00:00+00:00",
            )

    def test_manifest_validation_rejects_rewritten_immutable_identity(self):
        promoted = promotion.promote_manifest(
            self.source_manifest(),
            target_channel="beta",
            target_version="0.9.0-beta.1",
            artifacts=self.replacement_artifact(),
            promoted_at="2026-09-13T01:00:00+00:00",
        )
        promoted["promotion"]["source_build_id"] = "different-build"
        with self.assertRaisesRegex(ValueError, "immutable source build id"):
            validate_channel_manifest(promoted, "beta")

    def test_promotion_cannot_move_toward_a_less_stable_channel(self):
        source = self.source_manifest("beta", "0.9.0-beta.4")
        with self.assertRaisesRegex(ValueError, "Beta or Standard|less stable"):
            promotion.promote_manifest(
                source,
                target_channel="beta",
                target_version="0.9.0-beta.5",
                artifacts=self.replacement_artifact(),
                promoted_at="2026-09-13T01:00:00+00:00",
            )

    def test_artifact_spec_accepts_unsigned_platform_url_and_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact_path = Path(tmp) / "InfoMancer.exe"
            artifact_path.write_bytes(b"signed-package-bytes")
            platform, artifact = promotion.parse_artifact_spec(
                f"windows=tauri-updater,https://example.invalid/InfoMancer.exe,{artifact_path}"
            )
        self.assertEqual(platform, "windows")
        self.assertEqual(artifact["kind"], "tauri-updater")
        self.assertEqual(artifact["url"], "https://example.invalid/InfoMancer.exe")
        self.assertEqual(len(artifact["sha256"]), 64)
        self.assertNotIn("signature", artifact)

    def test_artifact_spec_accepts_optional_signature_as_fourth_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact_path = Path(tmp) / "InfoMancer.exe"
            artifact_path.write_bytes(b"signed-package-bytes")
            platform, artifact = promotion.parse_artifact_spec(
                f"windows=tauri-updater,https://example.invalid/InfoMancer.exe,{artifact_path},minisign-value"
            )
        self.assertEqual(platform, "windows")
        self.assertEqual(artifact["signature"], "minisign-value")

    def test_artifact_spec_rejects_wrong_field_count(self):
        with self.assertRaisesRegex(ValueError, "platform=kind,url,path"):
            promotion.parse_artifact_spec("windows=tauri-updater,https://example.invalid/InfoMancer.exe")

    def test_workflow_checks_out_and_publishes_exact_qualified_commit(self):
        workflow = (ROOT / ".github/workflows/promote-update-channel.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("ref: ${{ steps.source.outputs.commit_sha }}", workflow)
        self.assertIn("releaseCommitish: ${{ steps.source.outputs.commit_sha }}", workflow)
        self.assertIn("Require unused immutable promotion tag", workflow)
        self.assertIn("never overwritten; choose a new target version", workflow)
        self.assertIn("tag does not resolve to the exact qualified source commit", workflow)
        self.assertIn("TAURI_UPDATER_PUBLIC_KEY", workflow)
        self.assertIn("TAURI_SIGNING_PRIVATE_KEY", workflow)
        self.assertIn("scripts/promote_update_channel_manifest.py", workflow)
        self.assertIn("signature_path", workflow)
        self.assertIn("$artifactSpec =", workflow)
        self.assertIn("source_build_id", (ROOT / "docs/update-channel-manifest.schema.json").read_text(encoding="utf-8"))
        self.assertNotIn("git checkout testing/0.9-alpha", workflow)


if __name__ == "__main__":
    unittest.main()

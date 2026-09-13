from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "infomancer_host_updater_09", ROOT / "scripts" / "host_updater.py"
)
assert SPEC and SPEC.loader
host_updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(host_updater)


class HostUpdater09Tests(unittest.TestCase):
    def test_release_metadata_is_bounded_whitelisted_and_validates_commit(self):
        release = host_updater.release_metadata({
            "release": {
                "channel": "dev",
                "build_id": "qualified-2410",
                "commit_sha": "a" * 40,
                "qualification_status": "passed",
                "not_trusted": "drop me",
            }
        })
        self.assertEqual(release["channel"], "dev")
        self.assertEqual(release["commit_sha"], "a" * 40)
        self.assertNotIn("not_trusted", release)
        with self.assertRaisesRegex(host_updater.UpdateError, "commit SHA"):
            host_updater.release_metadata({"release": {"commit_sha": "not-a-sha"}})

    def test_update_history_is_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            for index in range(105):
                host_updater.append_history(data, {"index": index})
            history = json.loads((data / "update-history.json").read_text())
        self.assertEqual(len(history), host_updater.MAX_HISTORY_ENTRIES)
        self.assertEqual(history[0]["index"], 5)
        self.assertEqual(history[-1]["index"], 104)

    def test_manifest_commit_must_match_signed_tag_before_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repo"
            data = root / "data"
            (repository / ".git").mkdir(parents=True)
            (repository / "compose.yaml").write_text("services: {}\n")
            data.mkdir()
            tag = "v0.9.0-dev.2410"
            requested_commit = "a" * 40
            current_commit = "b" * 40
            signed_tag_commit = "c" * 40
            (data / "update-request.json").write_text(json.dumps({
                "tag": tag,
                "requested_by": "Librarian",
                "release": {
                    "channel": "dev",
                    "build_id": "qualified-2410",
                    "commit_sha": requested_commit,
                    "qualification_status": "passed",
                },
            }))

            def fake_run(command, cwd):
                if command[:3] == ["git", "status", "--porcelain"]:
                    return ""
                if command == ["git", "rev-parse", "HEAD"]:
                    return current_commit
                if command[:2] == ["git", "fetch"]:
                    return ""
                if command[:3] == ["git", "rev-parse", "--verify"]:
                    return signed_tag_commit
                self.fail(f"Unexpected command after trust mismatch: {command}")

            with mock.patch.object(host_updater, "run", side_effect=fake_run), mock.patch.object(
                host_updater, "verify_release_tag"
            ) as verify:
                handled = host_updater.process_request(
                    repository,
                    data,
                    ["compose.yaml"],
                    "http://127.0.0.1:8787/health",
                    30,
                    {"D" * 40},
                )

            self.assertTrue(handled)
            verify.assert_called_once()
            status = json.loads((data / "update-status.json").read_text())
            self.assertEqual(status["status"], "error")
            self.assertEqual(status["release"]["build_id"], "qualified-2410")
            self.assertIn("does not point to the commit", status["message"])
            history = json.loads((data / "update-history.json").read_text())
            self.assertEqual(history[-1]["release"]["commit_sha"], requested_commit)

    def test_schema_safe_version_downgrade_can_complete_through_trusted_host_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repo"
            data = root / "data"
            (repository / ".git").mkdir(parents=True)
            (repository / "compose.yaml").write_text("services: {}\n")
            data.mkdir()
            previous_commit = "b" * 40
            target_commit = "a" * 40
            (data / "update-request.json").write_text(json.dumps({
                "tag": "v0.8.1-beta.2",
                "requested_by": "Librarian",
                "release": {
                    "channel": "standard",
                    "build_id": "qualified-beta2",
                    "commit_sha": target_commit,
                    "qualification_status": "passed",
                    "schema_assessment": {
                        "status": "safe_downgrade",
                        "current_schema": 17,
                        "target_schema": 17,
                    },
                },
            }))
            commands: list[list[str]] = []

            def fake_run(command, cwd):
                commands.append(command)
                if command[:3] == ["git", "status", "--porcelain"]:
                    return ""
                if command == ["git", "rev-parse", "HEAD"]:
                    return previous_commit
                if command[:2] == ["git", "fetch"]:
                    return ""
                if command[:3] == ["git", "rev-parse", "--verify"]:
                    return target_commit
                return ""

            with mock.patch.object(host_updater, "run", side_effect=fake_run), mock.patch.object(
                host_updater, "verify_release_tag"
            ) as verify, mock.patch.object(host_updater, "wait_for_health") as health:
                handled = host_updater.process_request(
                    repository,
                    data,
                    ["compose.yaml"],
                    "http://127.0.0.1:8787/health",
                    30,
                    {"D" * 40},
                )

            self.assertTrue(handled)
            verify.assert_called_once()
            health.assert_called_once()
            self.assertIn(["git", "checkout", "--detach", target_commit], commands)
            status = json.loads((data / "update-status.json").read_text())
            self.assertEqual(status["status"], "success")
            self.assertEqual(status["previous_commit"], previous_commit)
            self.assertEqual(status["target_commit"], target_commit)
            self.assertEqual(status["release"]["schema_assessment"]["status"], "safe_downgrade")
            self.assertFalse((data / "update-request.json").exists())

    def test_failed_schema_safe_downgrade_rolls_back_to_previous_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repo"
            data = root / "data"
            (repository / ".git").mkdir(parents=True)
            (repository / "compose.yaml").write_text("services: {}\n")
            data.mkdir()
            previous_commit = "b" * 40
            target_commit = "a" * 40
            (data / "update-request.json").write_text(json.dumps({
                "tag": "v0.8.1-beta.2",
                "requested_by": "Librarian",
                "release": {
                    "channel": "standard",
                    "build_id": "qualified-beta2",
                    "commit_sha": target_commit,
                    "qualification_status": "passed",
                    "schema_assessment": {"status": "safe_downgrade"},
                },
            }))
            commands: list[list[str]] = []

            def fake_run(command, cwd):
                commands.append(command)
                if command[:3] == ["git", "status", "--porcelain"]:
                    return ""
                if command == ["git", "rev-parse", "HEAD"]:
                    return previous_commit
                if command[:2] == ["git", "fetch"]:
                    return ""
                if command[:3] == ["git", "rev-parse", "--verify"]:
                    return target_commit
                return ""

            health_results = [host_updater.UpdateError("target unhealthy"), None]
            with mock.patch.object(host_updater, "run", side_effect=fake_run), mock.patch.object(
                host_updater, "verify_release_tag"
            ), mock.patch.object(host_updater, "wait_for_health", side_effect=health_results):
                handled = host_updater.process_request(
                    repository,
                    data,
                    ["compose.yaml"],
                    "http://127.0.0.1:8787/health",
                    30,
                    {"D" * 40},
                )

            self.assertTrue(handled)
            checkout_commands = [command for command in commands if command[:3] == ["git", "checkout", "--detach"]]
            self.assertEqual(
                checkout_commands,
                [
                    ["git", "checkout", "--detach", target_commit],
                    ["git", "checkout", "--detach", previous_commit],
                ],
            )
            status = json.loads((data / "update-status.json").read_text())
            self.assertEqual(status["status"], "rolled_back")
            self.assertEqual(status["previous_commit"], previous_commit)
            self.assertEqual(status["target_commit"], target_commit)
            self.assertEqual(status["release"]["schema_assessment"]["status"], "safe_downgrade")
            history = json.loads((data / "update-history.json").read_text())
            self.assertEqual(history[-1]["status"], "rolled_back")


if __name__ == "__main__":
    unittest.main()

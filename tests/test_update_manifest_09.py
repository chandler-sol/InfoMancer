import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from app.migrations import CURRENT_SCHEMA_VERSION


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_update_channel_manifest.py"


class UpdateManifest09Tests(unittest.TestCase):
    def test_builder_records_qualification_artifact_digest_and_schema_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "candidate.bin"
            artifact.write_bytes(b"qualified infomancer candidate\n")
            output = root / "dev.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--channel", "dev",
                    "--version", "0.9.0-dev.184",
                    "--build-id", "dev-184-01234567",
                    "--commit-sha", "0123456789abcdef0123456789abcdef01234567",
                    "--run-id", "184",
                    "--run-url", "https://github.com/chandler-sol/InfoMancer/actions/runs/184",
                    "--artifact", f"windows=tauri-updater,https://example.invalid/InfoMancer.exe,{artifact}",
                    "--output", str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            manifest = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["channel"], "dev")
            self.assertEqual(manifest["qualification"]["status"], "passed")
            self.assertEqual(manifest["qualification"]["run_id"], 184)
            self.assertEqual(len(manifest["artifacts"]["windows"]["sha256"]), 64)
            self.assertGreaterEqual(manifest["database_schema"]["current"], 17)
            self.assertEqual(manifest["database_schema"]["downgrade_policy"], "compatible")
            self.assertLessEqual(
                manifest["database_schema"]["minimum_reader_schema"],
                manifest["database_schema"]["current"],
            )

    def test_builder_runs_without_site_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "candidate.bin"
            artifact.write_bytes(b"dependency-free manifest candidate\n")
            output = root / "isolated.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    str(SCRIPT),
                    "--channel", "dev",
                    "--version", "0.9.0-dev.184",
                    "--build-id", "dev-184-01234567",
                    "--commit-sha", "0123456789abcdef0123456789abcdef01234567",
                    "--run-id", "184",
                    "--artifact", f"windows=tauri-updater,https://example.invalid/InfoMancer.exe,{artifact}",
                    "--output", str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            manifest = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                manifest["database_schema"]["current"],
                CURRENT_SCHEMA_VERSION,
            )
            self.assertEqual(manifest["database_schema"]["downgrade_policy"], "compatible")

    def test_builder_refuses_failed_qualification(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bad.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--channel", "dev",
                    "--version", "0.9.0-dev.185",
                    "--build-id", "dev-185-deadbeef",
                    "--commit-sha", "0123456789abcdef0123456789abcdef01234567",
                    "--run-id", "185",
                    "--qualification-status", "failed",
                    "--output", str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(output.exists())

    def test_builder_refuses_plain_http_artifact_url(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "candidate.bin"
            artifact.write_bytes(b"candidate\n")
            output = root / "bad-http.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--channel", "dev",
                    "--version", "0.9.0-dev.186",
                    "--build-id", "dev-186-feedbeef",
                    "--commit-sha", "0123456789abcdef0123456789abcdef01234567",
                    "--run-id", "186",
                    "--artifact", f"windows=tauri-updater,http://example.invalid/InfoMancer.exe,{artifact}",
                    "--output", str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(output.exists())
            self.assertIn("HTTPS", completed.stdout + completed.stderr)

    def test_builder_refuses_plain_http_qualification_and_release_notes_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for option in ("--run-url", "--release-notes-url"):
                output = root / f"bad-{option.removeprefix('--')}.json"
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT),
                        "--channel", "dev",
                        "--version", "0.9.0-dev.187",
                        "--build-id", "dev-187-cafebabe",
                        "--commit-sha", "0123456789abcdef0123456789abcdef01234567",
                        "--run-id", "187",
                        option, "http://example.invalid/metadata",
                        "--output", str(output),
                    ],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertFalse(output.exists())
                self.assertIn("HTTPS", completed.stdout + completed.stderr)

    def test_manifest_schema_requires_passed_qualification_database_contract_and_https_urls(self):
        schema = json.loads(
            (ROOT / "docs" / "update-channel-manifest.schema.json").read_text(encoding="utf-8")
        )
        qualification = schema["properties"]["qualification"]
        self.assertEqual(qualification["properties"]["status"]["const"], "passed")
        self.assertEqual(qualification["properties"]["run_url"]["pattern"], "^https://")
        self.assertEqual(schema["properties"]["release_notes_url"]["pattern"], "^https://")
        artifact_url = schema["properties"]["artifacts"]["additionalProperties"]["properties"]["url"]
        self.assertEqual(artifact_url["pattern"], "^https://")
        self.assertIn("artifacts", schema["required"])
        self.assertIn("database_schema", schema["required"])
        database_schema = schema["properties"]["database_schema"]
        self.assertIn("current", database_schema["required"])
        self.assertIn("minimum_reader_schema", database_schema["required"])
        self.assertIn("minimum_writer_schema", database_schema["required"])
        self.assertIn("downgrade_policy", database_schema["required"])

    def test_dev_candidate_waits_for_every_qualification_gate(self):
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        self.assertIn("qualified-dev-candidate:", workflow)
        self.assertIn("- audit", workflow)
        self.assertIn("- test", workflow)
        self.assertIn("- acceptance", workflow)
        self.assertIn("github.ref == 'refs/heads/testing/0.9-alpha'", workflow)
        self.assertIn("dev-channel-candidate-${{ github.run_number }}-${{ github.sha }}", workflow)
        self.assertNotIn("workflow_run:", workflow)


if __name__ == "__main__":
    unittest.main()

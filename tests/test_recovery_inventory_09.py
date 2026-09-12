import os
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.recovery_inventory import (
    inspect_recovery_package,
    recommend_recovery_build,
    recovery_search_directories,
    recovery_target_compatibility,
    scan_recovery_packages,
)
from app.recovery_package import RecoveryPackageService


class RecoveryInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name)
        self.database = Database(self.data / "infomancer.db")
        self.database.initialize()
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO users(username,display_name,role,password_hash)
                   VALUES ('librarian','Librarian','librarian','test')"""
            )
        self.service = RecoveryPackageService(self.database.path, "0.9.0-dev.2402")

    def tearDown(self):
        self.temporary.cleanup()

    def test_inventory_derives_schema_contract_from_packaged_database(self):
        package = self.service.create()
        inspected = inspect_recovery_package(self.service, package)

        self.assertEqual(inspected["integrity_status"], "verified")
        self.assertEqual(inspected["app_version"], "0.9.0-dev.2402")
        self.assertGreaterEqual(inspected["database_schema"]["current"], 1)
        self.assertEqual(inspected["database_schema"]["ledger_status"], "complete")

    def test_scan_includes_default_and_explicit_directories_without_recursing(self):
        package = self.service.create()
        extra = self.data / "external-backups"
        extra.mkdir()
        copied = extra / "copied.infomancer-backup"
        copied.write_bytes(package.read_bytes())
        nested = extra / "nested"
        nested.mkdir()
        (nested / "ignored.infomancer-backup").write_bytes(package.read_bytes())

        configured = os.pathsep.join((str(extra), str(extra)))
        directories = recovery_search_directories(self.database.path, configured=configured)
        self.assertEqual(len(directories), 2)

        results = scan_recovery_packages(self.service, directories)
        names = {item["name"] for item in results}
        self.assertIn(package.name, names)
        self.assertIn("copied.infomancer-backup", names)
        self.assertNotIn("ignored.infomancer-backup", names)
        self.assertTrue(all(item["valid"] for item in results))

    def test_scan_reports_invalid_package_without_hiding_valid_backups(self):
        valid = self.service.create()
        invalid = self.service.output_dir / "broken.infomancer-backup"
        invalid.write_bytes(b"not a recovery package")

        results = scan_recovery_packages(
            self.service,
            recovery_search_directories(self.database.path, configured=""),
        )
        by_name = {item["name"]: item for item in results}
        self.assertTrue(by_name[valid.name]["valid"])
        self.assertFalse(by_name[invalid.name]["valid"])
        self.assertEqual(by_name[invalid.name]["integrity_status"], "failed")

    def test_recovery_compatibility_uses_backup_ledger_for_safe_downgrade(self):
        backup = {
            "current": 24,
            "minimum_reader_schema": 17,
            "minimum_writer_schema": 17,
            "downgrade_policy": "compatible",
        }
        target = {
            "current": 21,
            "minimum_reader_schema": 1,
            "minimum_writer_schema": 1,
            "downgrade_policy": "compatible",
        }
        result = recovery_target_compatibility(backup, target)
        self.assertEqual(result["status"], "compatible")
        self.assertEqual(result["reason"], "ledger_safe_downgrade")

    def test_recovery_compatibility_rejects_writer_incompatible_target(self):
        backup = {
            "current": 24,
            "minimum_reader_schema": 17,
            "minimum_writer_schema": None,
            "downgrade_policy": "read_only",
        }
        target = {
            "current": 21,
            "minimum_reader_schema": 1,
            "minimum_writer_schema": 1,
            "downgrade_policy": "compatible",
        }
        result = recovery_target_compatibility(backup, target)
        self.assertEqual(result["status"], "read_only")

    def test_recommendation_prefers_exact_creator_build_inside_selected_channel(self):
        backup = {
            "valid": True,
            "app_version": "0.9.0-dev.2402",
            "database_schema": {
                "current": 17,
                "minimum_reader_schema": 1,
                "minimum_writer_schema": 1,
                "downgrade_policy": "compatible",
            },
        }
        manifests = [
            self._manifest("standard", "0.8.1", 17),
            self._manifest("dev", "0.9.0-dev.2402", 17),
        ]
        result = recommend_recovery_build(backup, manifests, "dev")
        self.assertIsNotNone(result)
        self.assertEqual(result["version"], "0.9.0-dev.2402")
        self.assertEqual(result["reason"], "exact_creator")
        self.assertFalse(result["requires_channel_change"])

    def test_recommendation_respects_standard_preference(self):
        backup = {
            "valid": True,
            "app_version": "0.9.0-dev.2300",
            "database_schema": {
                "current": 17,
                "minimum_reader_schema": 1,
                "minimum_writer_schema": 1,
                "downgrade_policy": "compatible",
            },
        }
        manifests = [
            self._manifest("standard", "0.8.1", 17),
            self._manifest("dev", "0.9.0-dev.2402", 17),
        ]
        result = recommend_recovery_build(backup, manifests, "standard")
        self.assertIsNotNone(result)
        self.assertEqual(result["channel"], "standard")
        self.assertEqual(result["version"], "0.8.1")
        self.assertEqual(result["reason"], "newest_compatible")

    def test_recommendation_marks_cross_channel_fallback_instead_of_silently_switching(self):
        backup = {
            "valid": True,
            "app_version": "0.9.0-dev.2402",
            "database_schema": {
                "current": 18,
                "minimum_reader_schema": 18,
                "minimum_writer_schema": 18,
                "downgrade_policy": "restore_required",
            },
        }
        manifests = [
            self._manifest("standard", "0.8.1", 17),
            self._manifest("dev", "0.9.0-dev.2402", 18),
        ]
        result = recommend_recovery_build(backup, manifests, "standard")
        self.assertIsNotNone(result)
        self.assertEqual(result["channel"], "dev")
        self.assertTrue(result["requires_channel_change"])
        self.assertEqual(result["reason"], "cross_channel_fallback")

    @staticmethod
    def _manifest(channel: str, version: str, schema: int) -> dict:
        return {
            "channel": channel,
            "version": version,
            "build_id": f"test-{channel}-{version}",
            "commit_sha": "a" * 40,
            "qualified_at": "2026-09-12T20:00:00+00:00",
            "database_schema": {
                "current": schema,
                "minimum_reader_schema": 1,
                "minimum_writer_schema": 1,
                "downgrade_policy": "compatible",
            },
            "artifacts": {
                "windows": {
                    "kind": "installer",
                    "url": f"https://example.invalid/{version}.exe",
                    "sha256": "b" * 64,
                }
            },
        }


class RecoveryInventoryUiContractTests(unittest.TestCase):
    def test_recovery_page_exposes_inventory_and_cross_platform_copy(self):
        root = Path(__file__).resolve().parents[1]
        page = (root / "app/templates/recovery_restore.html").read_text(encoding="utf-8")
        routes = (root / "app/routes/recovery.py").read_text(encoding="utf-8")

        self.assertIn("Scan for backups", page)
        self.assertIn("RECOMMENDED BUILD", page)
        self.assertIn("Portable backups are cross-platform", page)
        self.assertIn("scan_recovery_packages", routes)
        self.assertIn("recommend_recovery_build", routes)
        self.assertIn("validate_channel_manifest", routes)


if __name__ == "__main__":
    unittest.main()

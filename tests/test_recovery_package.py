import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.db import Database
from app.recovery_package import RecoveryPackageError, RecoveryPackageService


class RecoveryPackageTests(unittest.TestCase):
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
        artwork = self.data / "collection-art"
        artwork.mkdir()
        (artwork / "collection-1.webp").write_bytes(b"fake artwork")
        self.service = RecoveryPackageService(self.database.path, "0.8-test")

    def tearDown(self):
        self.temporary.cleanup()

    def test_create_is_self_verified_and_contains_database_artwork_and_manifest(self):
        package = self.service.create()
        self.assertEqual(package.suffix, ".infomancer-backup")
        result = self.service.verify(package)
        self.assertEqual(result["app_version"], "0.8-test")
        self.assertEqual(result["artwork_files"], 1)
        with zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
            self.assertEqual(
                names,
                {"manifest.json", "database/infomancer.db", "collection-art/collection-1.webp"},
            )
            manifest = json.loads(archive.read("manifest.json"))
        self.assertFalse(manifest["contains_media"])
        self.assertTrue(any("provider credentials" in item for item in manifest["excluded"]))

    def test_verify_rejects_traversal_even_when_manifest_names_it(self):
        package = self.data / "evil.infomancer-backup"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("../escape.txt", b"bad")
            archive.writestr("manifest.json", json.dumps({
                "format": "infomancer-recovery", "format_version": 1,
                "files": [{"path": "../escape.txt", "role": "collection-artwork", "size": 3, "sha256": "0" * 64}],
            }))
        with self.assertRaisesRegex(RecoveryPackageError, "unsafe archive path"):
            self.service.verify(package)

    def test_verify_rejects_checksum_tampering(self):
        package = self.service.create()
        rebuilt = self.data / "tampered.infomancer-backup"
        with zipfile.ZipFile(package, "r") as source, zipfile.ZipFile(rebuilt, "w") as target:
            for item in source.infolist():
                payload = source.read(item)
                if item.filename == "collection-art/collection-1.webp":
                    payload = b"evil artwork"
                target.writestr(item.filename, payload)
        with self.assertRaisesRegex(RecoveryPackageError, "checksum failed"):
            self.service.verify(rebuilt)


class RecoveryPackageUiContractTests(unittest.TestCase):
    def test_system_settings_explain_portable_recovery_scope(self):
        root = Path(__file__).resolve().parents[1]
        settings = (root / "app/templates/settings.html").read_text(encoding="utf-8")
        routes = (root / "app/routes/settings.py").read_text(encoding="utf-8")
        self.assertIn("Create &amp; download recovery package", settings)
        self.assertIn("provider credentials", settings)
        self.assertIn('action="/maintenance/recovery-package/verify"', settings)
        self.assertIn('recovery_packages.verify(candidate_path)', routes)


if __name__ == "__main__":
    unittest.main()

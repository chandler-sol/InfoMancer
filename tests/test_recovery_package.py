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
        self.assertFalse(result["contains_media"])
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

    def test_restore_replaces_database_and_artwork_but_never_provider_secrets(self):
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO users(username,display_name,role,password_hash)
                   VALUES ('from-package','From Package','member','test')"""
            )
        (self.data / "collection-art" / "collection-1.webp").write_bytes(b"package artwork")
        package = self.service.create()

        with self.database.connect() as conn:
            conn.execute("DELETE FROM users WHERE username='from-package'")
            conn.execute(
                """INSERT INTO users(username,display_name,role,password_hash)
                   VALUES ('current-only','Current Only','member','test')"""
            )
        (self.data / "collection-art" / "collection-1.webp").write_bytes(b"current artwork")
        secret_store = self.data / "provider-secrets.json.enc"
        secret_store.write_bytes(b"do not touch")

        result = self.service.restore(package, (self.data,))

        with self.database.connect() as conn:
            package_user = conn.execute(
                "SELECT 1 FROM users WHERE username='from-package'"
            ).fetchone()
            current_user = conn.execute(
                "SELECT 1 FROM users WHERE username='current-only'"
            ).fetchone()
        self.assertIsNotNone(package_user)
        self.assertIsNone(current_user)
        self.assertEqual(
            (self.data / "collection-art" / "collection-1.webp").read_bytes(),
            b"package artwork",
        )
        self.assertEqual(secret_store.read_bytes(), b"do not touch")
        self.assertTrue((self.data / "recovery-packages" / result["safety_package"]).is_file())

    def test_restore_rolls_back_database_and_artwork_when_commit_fails(self):
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO users(username,display_name,role,password_hash)
                   VALUES ('from-package','From Package','member','test')"""
            )
        (self.data / "collection-art" / "collection-1.webp").write_bytes(b"package artwork")
        package = self.service.create()
        with self.database.connect() as conn:
            conn.execute("DELETE FROM users WHERE username='from-package'")
            conn.execute(
                """INSERT INTO users(username,display_name,role,password_hash)
                   VALUES ('current-only','Current Only','member','test')"""
            )
        (self.data / "collection-art" / "collection-1.webp").write_bytes(b"current artwork")

        original_replace = self.service._replace

        def fail_database_commit(source: Path, destination: Path) -> None:
            if Path(destination) == self.database.path and "database/infomancer.db" in str(source).replace("\\", "/"):
                raise OSError("simulated database commit failure")
            original_replace(source, destination)

        self.service._replace = fail_database_commit
        with self.assertRaisesRegex(RecoveryPackageError, "rolled back safely"):
            self.service.restore(package, (self.data,))

        with self.database.connect() as conn:
            self.assertIsNotNone(conn.execute(
                "SELECT 1 FROM users WHERE username='current-only'"
            ).fetchone())
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM users WHERE username='from-package'"
            ).fetchone())
        self.assertEqual(
            (self.data / "collection-art" / "collection-1.webp").read_bytes(),
            b"current artwork",
        )

    def test_invalid_restore_is_rejected_before_safety_package_is_created(self):
        bad = self.data / "invalid.infomancer-backup"
        bad.write_bytes(b"not a zip")
        output = self.data / "recovery-packages"
        before = set(output.glob("*")) if output.exists() else set()
        with self.assertRaises(RecoveryPackageError):
            self.service.restore(bad, (self.data,))
        after = set(output.glob("*")) if output.exists() else set()
        self.assertEqual(before, after)


class RecoveryPackageUiContractTests(unittest.TestCase):
    def test_recovery_ui_has_preview_confirmation_and_secret_boundary(self):
        root = Path(__file__).resolve().parents[1]
        settings = (root / "app/templates/settings.html").read_text(encoding="utf-8")
        routes = (root / "app/routes/settings.py").read_text(encoding="utf-8")
        recovery_routes = (root / "app/routes/recovery.py").read_text(encoding="utf-8")
        page = (root / "app/templates/recovery_restore.html").read_text(encoding="utf-8")
        preview = (root / "app/templates/recovery_restore_preview.html").read_text(encoding="utf-8")
        self.assertIn("Create &amp; download recovery package", settings)
        self.assertIn("provider credentials", settings)
        self.assertIn('action="/maintenance/recovery-package/verify"', settings)
        self.assertIn('recovery_packages.verify(candidate_path)', routes)
        self.assertIn('/settings/recovery/preview', page)
        self.assertIn('/settings/recovery/apply', preview)
        self.assertIn('name="confirm"', preview)
        self.assertIn('recovery_service().restore(candidate, settings.media_browse_roots)', recovery_routes)
        self.assertIn("Provider credentials were not restored", (root / "app/templates/recovery_restore_pending.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

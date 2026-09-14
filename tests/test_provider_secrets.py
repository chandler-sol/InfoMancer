import base64
import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from app.provider_secrets import ProviderSecretError, ProviderSecretStore


class ProviderSecretStoreTests(unittest.TestCase):
    @staticmethod
    def _legacy_application_token(secret: str, values: dict[str, str]) -> bytes:
        digest = hashlib.sha256(secret.encode("utf-8")).digest()
        cipher = Fernet(base64.urlsafe_b64encode(digest))
        return cipher.encrypt(json.dumps(values, sort_keys=True).encode("utf-8"))

    def test_credentials_are_encrypted_and_round_trip_with_versioned_scrypt_envelope(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "providers.enc"
            store = ProviderSecretStore(path, "test-application-secret")
            store.update({"tvdb_api_key": "secret-key", "tvdb_pin": "1234"})

            raw = path.read_bytes()
            self.assertNotIn(b"secret-key", raw)
            envelope = json.loads(raw.decode("utf-8"))
            self.assertEqual(envelope["format"], "infomancer-provider-secrets")
            self.assertEqual(envelope["version"], 2)
            self.assertEqual(envelope["key_source"], "application_secret")
            self.assertEqual(envelope["kdf"], "scrypt")
            self.assertTrue(envelope["salt"])
            self.assertTrue(envelope["token"])
            self.assertEqual(store.load()["tvdb_api_key"], "secret-key")
            self.assertEqual(store.load()["tvdb_pin"], "1234")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(list(Path(temporary).glob(f".{path.name}.*.tmp")), [])

    def test_failed_encryption_does_not_leave_provider_secret_temp_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "providers.enc"
            store = ProviderSecretStore(path, "test-application-secret")

            def fail_encrypt(_payload: bytes) -> bytes:
                raise ProviderSecretError("synthetic encryption failure")

            store._encrypt = fail_encrypt
            with self.assertRaisesRegex(ProviderSecretError, "synthetic encryption failure"):
                store.update({"tvdb_api_key": "secret-key"})

            self.assertFalse(path.exists())
            self.assertEqual(list(Path(temporary).glob(f".{path.name}.*.tmp")), [])

    def test_wrong_application_secret_fails_closed_for_versioned_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "providers.enc"
            ProviderSecretStore(path, "correct-secret").update(
                {"tvdb_api_key": "secret-key"}
            )

            with self.assertRaises(ProviderSecretError):
                ProviderSecretStore(path, "wrong-secret").load()

    def test_legacy_application_secret_ciphertext_is_read_and_migrated_on_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "providers.enc"
            secret = "legacy-application-secret"
            path.write_bytes(
                self._legacy_application_token(secret, {"tvdb_api_key": "legacy-key"})
            )

            store = ProviderSecretStore(path, secret)
            self.assertEqual(store.load()["tvdb_api_key"], "legacy-key")
            store.update({"tvdb_pin": "1234"})

            envelope = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(envelope["version"], 2)
            self.assertEqual(envelope["key_source"], "application_secret")
            self.assertEqual(envelope["kdf"], "scrypt")
            self.assertEqual(store.load()["tvdb_pin"], "1234")

    def test_legacy_local_key_ciphertext_survives_adding_application_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "providers.enc"
            key_path = Path(temporary) / "provider-secrets.key"
            key = Fernet.generate_key()
            key_path.write_bytes(key)
            path.write_bytes(
                Fernet(key).encrypt(
                    json.dumps({"tvdb_api_key": "legacy-local-key"}).encode("utf-8")
                )
            )

            store = ProviderSecretStore(path, "new-application-secret")
            self.assertEqual(store.load()["tvdb_api_key"], "legacy-local-key")
            original_key = key_path.read_bytes()
            store.update({"tvdb_pin": "5678"})

            envelope = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(envelope["key_source"], "application_secret")
            self.assertEqual(envelope["kdf"], "scrypt")
            self.assertEqual(key_path.read_bytes(), original_key)
            self.assertEqual(store.load()["tvdb_pin"], "5678")

    def test_missing_application_secret_creates_restrictive_local_encryption_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "providers.enc"
            key_path = Path(temporary) / "provider-secrets.key"
            store = ProviderSecretStore(path, "")
            store.update({"tvdb_api_key": "secret-key"})
            self.assertTrue(key_path.exists())
            envelope = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(envelope["version"], 2)
            self.assertEqual(envelope["key_source"], "local_key")
            self.assertEqual(store.load()["tvdb_api_key"], "secret-key")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(key_path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

            original_key = key_path.read_bytes()
            ProviderSecretStore(path, "").update({"tvdb_pin": "1234"})
            self.assertEqual(key_path.read_bytes(), original_key)
            self.assertEqual(ProviderSecretStore(path, "").load()["tvdb_pin"], "1234")

    def test_versioned_local_key_file_can_migrate_after_secret_is_added(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "providers.enc"
            local_store = ProviderSecretStore(path, "")
            local_store.update({"tvdb_api_key": "local-key"})
            local_envelope = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(local_envelope["key_source"], "local_key")

            configured_store = ProviderSecretStore(path, "later-application-secret")
            self.assertEqual(configured_store.load()["tvdb_api_key"], "local-key")
            configured_store.update({"tvdb_pin": "9999"})

            migrated = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(migrated["key_source"], "application_secret")
            self.assertEqual(migrated["kdf"], "scrypt")
            self.assertEqual(configured_store.load()["tvdb_pin"], "9999")

    def test_unknown_versioned_format_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "providers.enc"
            store = ProviderSecretStore(path, "test-secret")
            store.update({"tvdb_api_key": "secret-key"})
            envelope = json.loads(path.read_text(encoding="utf-8"))
            envelope["version"] = 999
            path.write_text(json.dumps(envelope), encoding="utf-8")

            with self.assertRaises(ProviderSecretError):
                store.load()


if __name__ == "__main__":
    unittest.main()

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
    def _qualified_application_cipher(secret: str) -> Fernet:
        digest = hashlib.sha256(secret.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(digest))

    def test_credentials_are_encrypted_and_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "providers.enc"
            store = ProviderSecretStore(path, "test-application-secret")
            store.update({"tvdb_api_key": "secret-key", "tvdb_pin": "1234"})

            raw = path.read_bytes()
            self.assertNotIn(b"secret-key", raw)
            self.assertEqual(store.load()["tvdb_api_key"], "secret-key")
            self.assertEqual(store.load()["tvdb_pin"], "1234")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(list(Path(temporary).glob(f".{path.name}.*.tmp")), [])

    def test_new_application_secret_write_is_readable_by_qualified_09_cipher(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "providers.enc"
            secret = "downgrade-compatible-secret"
            ProviderSecretStore(path, secret).update({"tvdb_api_key": "survives-downgrade"})

            payload = self._qualified_application_cipher(secret).decrypt(path.read_bytes())
            self.assertEqual(
                json.loads(payload.decode("utf-8"))["tvdb_api_key"],
                "survives-downgrade",
            )

    def test_existing_qualified_application_secret_ciphertext_remains_readable(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "providers.enc"
            secret = "qualified-application-secret"
            path.write_bytes(
                self._qualified_application_cipher(secret).encrypt(
                    json.dumps({"tvdb_api_key": "qualified-key"}, sort_keys=True).encode("utf-8")
                )
            )
            self.assertEqual(
                ProviderSecretStore(path, secret).load()["tvdb_api_key"],
                "qualified-key",
            )

    def test_failed_encryption_does_not_leave_provider_secret_temp_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "providers.enc"
            store = ProviderSecretStore(path, "test-application-secret")

            class FailingCipher:
                def encrypt(self, _payload: bytes) -> bytes:
                    raise ProviderSecretError("synthetic encryption failure")

            store._cipher = lambda **_kwargs: FailingCipher()
            with self.assertRaisesRegex(ProviderSecretError, "synthetic encryption failure"):
                store.update({"tvdb_api_key": "secret-key"})

            self.assertFalse(path.exists())
            self.assertEqual(list(Path(temporary).glob(f".{path.name}.*.tmp")), [])

    def test_wrong_application_secret_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "providers.enc"
            ProviderSecretStore(path, "correct-secret").update({"tvdb_api_key": "secret-key"})
            with self.assertRaises(ProviderSecretError):
                ProviderSecretStore(path, "wrong-secret").load()

    def test_missing_application_secret_creates_restrictive_local_encryption_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "providers.enc"
            key_path = Path(temporary) / "provider-secrets.key"
            store = ProviderSecretStore(path, "")
            store.update({"tvdb_api_key": "secret-key"})
            self.assertTrue(key_path.exists())
            self.assertEqual(store.load()["tvdb_api_key"], "secret-key")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(key_path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

            original_key = key_path.read_bytes()
            ProviderSecretStore(path, "").update({"tvdb_pin": "1234"})
            self.assertEqual(key_path.read_bytes(), original_key)
            self.assertEqual(ProviderSecretStore(path, "").load()["tvdb_pin"], "1234")

    def test_new_local_key_write_is_readable_by_qualified_09_cipher(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "providers.enc"
            key_path = Path(temporary) / "provider-secrets.key"
            ProviderSecretStore(path, "").update({"tvdb_api_key": "local-downgrade"})
            payload = Fernet(key_path.read_bytes().strip()).decrypt(path.read_bytes())
            self.assertEqual(
                json.loads(payload.decode("utf-8"))["tvdb_api_key"],
                "local-downgrade",
            )

    def test_missing_local_key_fails_closed_without_creating_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "providers.enc"
            key_path = Path(temporary) / "provider-secrets.key"
            path.write_bytes(b"not-a-valid-token")
            with self.assertRaises(ProviderSecretError):
                ProviderSecretStore(path, "").load()
            self.assertFalse(key_path.exists())


if __name__ == "__main__":
    unittest.main()

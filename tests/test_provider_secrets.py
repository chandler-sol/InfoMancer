import os
import stat
import tempfile
import unittest
from pathlib import Path

from app.provider_secrets import ProviderSecretStore


class ProviderSecretStoreTests(unittest.TestCase):
    def test_credentials_are_encrypted_and_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "providers.enc"
            store = ProviderSecretStore(path, "test-application-secret")
            store.update({"tvdb_api_key": "secret-key", "tvdb_pin": "1234"})

            self.assertNotIn(b"secret-key", path.read_bytes())
            self.assertEqual(store.load()["tvdb_api_key"], "secret-key")
            self.assertEqual(store.load()["tvdb_pin"], "1234")

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

            original_key = key_path.read_bytes()
            ProviderSecretStore(path, "").update({"tvdb_pin": "1234"})
            self.assertEqual(key_path.read_bytes(), original_key)
            self.assertEqual(ProviderSecretStore(path, "").load()["tvdb_pin"], "1234")


if __name__ == "__main__":
    unittest.main()

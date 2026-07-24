import tempfile
import unittest
from pathlib import Path

from app.provider_secrets import ProviderSecretError, ProviderSecretStore


class ProviderSecretStoreTests(unittest.TestCase):
    def test_credentials_are_encrypted_and_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "providers.enc"
            store = ProviderSecretStore(path, "test-application-secret")
            store.update({"tvdb_api_key": "secret-key", "tvdb_pin": "1234"})

            self.assertNotIn(b"secret-key", path.read_bytes())
            self.assertEqual(store.load()["tvdb_api_key"], "secret-key")
            self.assertEqual(store.load()["tvdb_pin"], "1234")

    def test_missing_application_secret_creates_local_encryption_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "providers.enc"
            store = ProviderSecretStore(path, "")
            store.update({"tvdb_api_key": "secret-key"})
            self.assertTrue((Path(temporary) / "provider-secrets.key").exists())
            self.assertEqual(store.load()["tvdb_api_key"], "secret-key")


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from app.db import Database
from app.tvdb import TVDBClient


class FakeProviderSecrets:
    def __init__(self):
        self.values = {}

    def update(self, values):
        self.values.update(values)

    def load(self):
        return dict(self.values)


class SettingsQuickActionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        self.original_db = main.db
        self.original_tvdb = main.tvdb
        self.original_provider_secrets = main.provider_secrets
        self.original_stored_provider_secrets = main.stored_provider_secrets
        self.original_provider_secret_error = main.provider_secret_error
        main.db = self.database
        main.tvdb = TVDBClient("old-project-key", "old-pin")
        main.provider_secrets = FakeProviderSecrets()
        main.stored_provider_secrets = {}
        main.provider_secret_error = ""
        self.auth_patch = patch.object(
            main, "settings", replace(main.settings, auth_mode="disabled")
        )
        self.auth_patch.start()
        self.client = TestClient(main.app)
        self.client.get("/")
        csrf = self.client.cookies.get("infomancer_local_csrf")
        self.assertTrue(csrf)
        self.client.headers.update({"X-CSRF-Token": csrf})

    def tearDown(self):
        self.client.close()
        self.auth_patch.stop()
        main.db = self.original_db
        main.tvdb = self.original_tvdb
        main.provider_secrets = self.original_provider_secrets
        main.stored_provider_secrets = self.original_stored_provider_secrets
        main.provider_secret_error = self.original_provider_secret_error
        self.temporary.cleanup()

    def test_tvdb_credentials_save_in_settings_without_echoing_secrets(self):
        with patch(
            "app.routes.settings_quick_actions.TVDBClient.test_connection",
            return_value=None,
        ):
            response = self.client.post(
                "/settings/metadata/tvdb-credentials",
                data={"api_key": "new-secret-project-key", "subscriber_pin": "new-pin"},
                headers={"Accept": "application/json", "X-InfoMancer-Async": "1"},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["key_hint"], "Configured · ends in -key")
        self.assertTrue(payload["pin_configured"])
        self.assertNotIn("new-secret-project-key", response.text)
        self.assertNotIn("new-pin", response.text)
        self.assertEqual(main.tvdb.api_key, "new-secret-project-key")
        self.assertEqual(main.tvdb.pin, "new-pin")
        self.assertEqual(
            main.provider_secrets.values,
            {"tvdb_api_key": "new-secret-project-key", "tvdb_pin": "new-pin"},
        )

    def test_bulk_source_connection_check_visits_every_root(self):
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES ('/movies/a','movie','A')"
            )
            conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES ('/tv/b','tv','B')"
            )
        results = [
            {"status": "healthy", "last_seen_at": "now"},
            {"status": "offline", "last_seen_at": "earlier"},
        ]
        with patch.object(main, "check_source_health", side_effect=results) as checker, \
             patch.object(main.mie, "analyze", return_value={}) as analyze:
            response = self.client.post("/roots/check-all", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(checker.call_count, 2)
        analyze.assert_called_once()
        self.assertIn("/sources", response.headers["location"])


if __name__ == "__main__":
    unittest.main()

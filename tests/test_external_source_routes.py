from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.db import Database
from app.media_identity.external_config import ExternalSourceConfigService
from app.provider_secrets import ProviderSecretStore
from app.request_security import LOCAL_CSRF_COOKIE


class ExternalSourceRouteSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        data = Path(self.temporary.name)
        settings = replace(
            main.settings,
            database=data / "catalog.db",
            auth_mode="disabled",
            cookie_secure="false",
            sandbox=True,
            media_browse_roots=(data,),
        )
        database = Database(settings.database)
        database.initialize()

        self.original = (
            main.db,
            main.settings,
            main.external_source_config,
            main.provider_secrets,
        )
        main.db = database
        main.settings = settings
        main.external_source_config = ExternalSourceConfigService(database)
        main.provider_secrets = ProviderSecretStore(
            data / "provider-secrets.enc",
            "external-route-security-test-secret",
        )

        main.external_source_config.save_source(
            "plex",
            enabled=True,
            server_url="http://trusted-plex.local:32400",
        )
        main.provider_secrets.update({"plex_token": "trusted-token"})

        self.client = TestClient(main.app, follow_redirects=False)
        self.client.get("/")
        csrf = self.client.cookies.get(LOCAL_CSRF_COOKIE)
        self.assertTrue(csrf)
        self.client.headers.update({"X-CSRF-Token": csrf})

    def tearDown(self):
        self.client.close()
        (
            main.db,
            main.settings,
            main.external_source_config,
            main.provider_secrets,
        ) = self.original
        self.temporary.cleanup()

    def test_changed_server_url_cannot_reuse_hidden_saved_token(self):
        response = self.client.post(
            "/settings/integrations/plex",
            data={
                "enabled": "1",
                "server_url": "http://attacker.example:32400",
                "metadata_root": "",
                "token": "",
                "clear_token": "",
            },
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("replacement+token", response.headers["location"])

        source = main.external_source_config.source("plex")
        secrets = main.provider_secrets.load()
        self.assertEqual(source.server_url, "http://trusted-plex.local:32400")
        self.assertTrue(source.enabled)
        self.assertEqual(secrets["plex_token"], "trusted-token")

    def test_changed_server_url_accepts_explicit_replacement_token(self):
        response = self.client.post(
            "/settings/integrations/plex",
            data={
                "enabled": "1",
                "server_url": "http://replacement-plex.local:32400",
                "metadata_root": "",
                "token": "replacement-token",
                "clear_token": "",
            },
        )
        self.assertEqual(response.status_code, 303)

        source = main.external_source_config.source("plex")
        secrets = main.provider_secrets.load()
        self.assertEqual(source.server_url, "http://replacement-plex.local:32400")
        self.assertTrue(source.enabled)
        self.assertEqual(secrets["plex_token"], "replacement-token")


if __name__ == "__main__":
    unittest.main()

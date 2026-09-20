from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
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
            server_url="https://trusted-plex.local:32400",
            credential_generation="trusted-generation",
        )
        main.provider_secrets.update({
            "plex_token": "trusted-token",
            "plex_token_endpoint": "https://trusted-plex.local:32400",
            "plex_token_generation": "trusted-generation",
        })

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

    def test_integrations_page_reports_plex_adapter_without_claiming_analysis_ready(self):
        local_root = Path(self.temporary.name) / "media"
        main.external_source_config.add_mapping(
            "plex",
            "/srv/tv",
            str(local_root),
        )

        response = self.client.get("/settings/integrations")

        self.assertEqual(response.status_code, 200)
        self.assertIn("BIF adapter", response.text)
        self.assertIn("Installed", response.text)
        self.assertIn(
            "Plex BIF adapter is configured and validated.",
            response.text,
        )
        self.assertIn(
            "Preview-frame analysis becomes available when a consuming analyzer is installed.",
            response.text,
        )
        self.assertNotIn(">Ready</span><span>BIF adapter", response.text)
        self.assertNotIn(">Pending</strong><span>Preview adapter", response.text)
        self.assertIn("Allow this Plex token over plain HTTP", response.text)
        self.assertIn('placeholder="https://plex.local:32400"', response.text)

    def test_integrations_page_reports_jellyfin_adapter_without_claiming_analysis_ready(self):
        local_root = Path(self.temporary.name) / "media"
        source = main.external_source_config.save_source(
            "jellyfin",
            enabled=True,
            server_url="https://jellyfin.local:8096",
            credential_generation="jellyfin-generation",
        )
        main.external_source_config.add_mapping(
            "jellyfin",
            "/srv/tv",
            str(local_root),
        )
        main.provider_secrets.update({
            "jellyfin_token": "jellyfin-token",
            "jellyfin_token_endpoint": source.server_url,
            "jellyfin_token_generation": "jellyfin-generation",
        })

        response = self.client.get("/settings/integrations")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Trickplay adapter", response.text)
        self.assertIn("Installed", response.text)
        self.assertIn(
            "Preview-frame analysis becomes available when a consuming analyzer is installed.",
            response.text,
        )
        self.assertNotIn(">Ready</span><span>Trickplay adapter", response.text)

    def test_plex_plain_http_requires_explicit_opt_in(self):
        response = self.client.post(
            "/settings/integrations/plex",
            data={
                "enabled": "1",
                "server_url": "http://plex.local:32400",
                "metadata_root": "",
                "token": "plex-secret",
                "clear_token": "",
            },
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("plain+HTTP", response.headers["location"])
        source = main.external_source_config.source("plex")
        self.assertEqual(
            source.server_url,
            "https://trusted-plex.local:32400",
        )
        self.assertFalse(source.config.get("allow_insecure_http", False))

    def test_plex_plain_http_can_be_explicitly_allowed(self):
        response = self.client.post(
            "/settings/integrations/plex",
            data={
                "enabled": "1",
                "server_url": "http://plex.local:32400",
                "metadata_root": "",
                "token": "plex-secret",
                "clear_token": "",
                "allow_insecure_http": "1",
            },
        )
        self.assertEqual(response.status_code, 303)
        source = main.external_source_config.source("plex")
        self.assertTrue(source.enabled)
        self.assertEqual(source.server_url, "http://plex.local:32400")
        self.assertTrue(source.config.get("allow_insecure_http"))
        secrets = main.provider_secrets.load()
        self.assertEqual(
            secrets["plex_token_endpoint"],
            "http://plex.local:32400",
        )

    def test_jellyfin_plain_http_requires_explicit_opt_in(self):
        response = self.client.post(
            "/settings/integrations/jellyfin",
            data={
                "enabled": "1",
                "server_url": "http://jellyfin.local:8096",
                "metadata_root": "",
                "token": "jf-secret",
                "clear_token": "",
            },
        )
        self.assertEqual(response.status_code, 303)
        source = main.external_source_config.source("jellyfin")
        self.assertFalse(source.enabled)
        self.assertEqual(source.server_url, "")

    def test_jellyfin_plain_http_can_be_explicitly_allowed(self):
        response = self.client.post(
            "/settings/integrations/jellyfin",
            data={
                "enabled": "1",
                "server_url": "http://jellyfin.local:8096",
                "metadata_root": "",
                "token": "jf-secret",
                "clear_token": "",
                "allow_insecure_http": "1",
            },
        )
        self.assertEqual(response.status_code, 303)
        source = main.external_source_config.source("jellyfin")
        self.assertTrue(source.enabled)
        self.assertEqual(source.server_url, "http://jellyfin.local:8096")
        self.assertTrue(source.config.get("allow_insecure_http"))

    def test_whitespace_in_server_url_is_rejected_without_500(self):
        response = self.client.post(
            "/settings/integrations/plex",
            data={
                "enabled": "1",
                "server_url": "https://plex local:32400",
                "metadata_root": "",
                "token": "",
                "clear_token": "",
            },
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("whitespace", response.headers["location"])
        self.assertEqual(
            main.external_source_config.source("plex").server_url,
            "https://trusted-plex.local:32400",
        )

    def test_non_ascii_server_url_is_rejected_without_500(self):
        response = self.client.post(
            "/settings/integrations/plex",
            data={
                "enabled": "1",
                "server_url": "https://plex.local:32400/médias",
                "metadata_root": "",
                "token": "",
                "clear_token": "",
            },
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("non-ASCII", response.headers["location"])
        self.assertEqual(
            main.external_source_config.source("plex").server_url,
            "https://trusted-plex.local:32400",
        )

    def test_inflight_connection_result_is_discarded_after_token_change(self):
        from app.media_identity.external_config import ExternalConnectionResult

        def change_token_during_test(*_args, **_kwargs):
            main.provider_secrets.update({
                "plex_token": "new-token",
                "plex_token_endpoint": "https://trusted-plex.local:32400",
                "plex_token_generation": "new-generation",
            })
            return ExternalConnectionResult(
                "plex",
                True,
                server_name="Trusted Plex",
                version="1.2.3",
                detail="Authenticated Plex connection succeeded.",
            )

        with patch(
            "app.routes.external_sources.test_external_connection",
            side_effect=change_token_during_test,
        ):
            response = self.client.post("/settings/integrations/plex/test")

        self.assertEqual(response.status_code, 303)
        self.assertIn("stale+result+was+discarded", response.headers["location"])
        source = main.external_source_config.source("plex")
        self.assertEqual(source.last_test_status, "")
        self.assertIsNone(source.last_test_at)

    def test_connection_test_cannot_start_between_secret_write_and_generation_adoption(self):
        from app.media_identity.external_config import ExternalConnectionResult

        main.external_source_config.clear_connection_result("plex")
        main.provider_secrets.update({
            "plex_token": "replacement-token",
            "plex_token_endpoint": "https://trusted-plex.local:32400",
            "plex_token_generation": "replacement-generation",
        })

        with patch(
            "app.routes.external_sources.test_external_connection",
            return_value=ExternalConnectionResult(
                "plex", True, server_name="Plex", version="1", detail="ok"
            ),
        ) as probe:
            response = self.client.post("/settings/integrations/plex/test")

        self.assertEqual(response.status_code, 303)
        self.assertIn("not+bound", response.headers["location"])
        probe.assert_not_called()
        source = main.external_source_config.source("plex")
        self.assertEqual(source.credential_generation, "trusted-generation")
        self.assertEqual(source.last_test_status, "")

    def test_revision_closes_token_change_gap_before_result_write(self):
        from app.media_identity.external_config import ExternalConnectionResult

        original_record = main.external_source_config.record_connection_result

        def race_before_record(
            result,
            *,
            tested_server_url,
            tested_revision,
        ):
            main.provider_secrets.update({
                "plex_token": "new-token",
                "plex_token_endpoint": "https://trusted-plex.local:32400",
                "plex_token_generation": "new-generation",
            })
            main.external_source_config.save_source(
                "plex",
                enabled=True,
                server_url="https://trusted-plex.local:32400",
                credential_generation="new-generation",
            )
            return original_record(
                result,
                tested_server_url=tested_server_url,
                tested_revision=tested_revision,
            )

        with (
            patch(
                "app.routes.external_sources.test_external_connection",
                return_value=ExternalConnectionResult(
                    "plex",
                    True,
                    server_name="Trusted Plex",
                    version="1.2.3",
                    detail="Authenticated Plex connection succeeded.",
                ),
            ),
            patch.object(
                main.external_source_config,
                "record_connection_result",
                side_effect=race_before_record,
            ),
        ):
            response = self.client.post("/settings/integrations/plex/test")

        self.assertEqual(response.status_code, 303)
        self.assertIn("stale+result+was+discarded", response.headers["location"])
        source = main.external_source_config.source("plex")
        self.assertEqual(source.last_test_status, "")
        self.assertIsNone(source.last_test_at)

    def test_changed_server_url_cannot_reuse_hidden_saved_token(self):
        response = self.client.post(
            "/settings/integrations/plex",
            data={
                "enabled": "1",
                "server_url": "https://attacker.example:32400",
                "metadata_root": "",
                "token": "",
                "clear_token": "",
            },
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("replacement+token", response.headers["location"])

        source = main.external_source_config.source("plex")
        secrets = main.provider_secrets.load()
        self.assertEqual(source.server_url, "https://trusted-plex.local:32400")
        self.assertTrue(source.enabled)
        self.assertEqual(secrets["plex_token"], "trusted-token")

    def test_unbound_token_is_never_sent_after_partial_endpoint_save(self):
        main.external_source_config.save_source(
            "plex",
            enabled=True,
            server_url="https://replacement-plex.local:32400",
        )
        secrets = main.provider_secrets.load()
        self.assertEqual(secrets["plex_token"], "trusted-token")
        self.assertEqual(
            secrets.get("plex_token_endpoint"),
            "https://trusted-plex.local:32400",
        )

        response = self.client.post("/settings/integrations/plex/test")
        self.assertEqual(response.status_code, 303)
        self.assertIn("not+bound", response.headers["location"])

    def test_replacement_token_is_bound_to_new_endpoint(self):
        response = self.client.post(
            "/settings/integrations/plex",
            data={
                "enabled": "1",
                "server_url": "https://replacement-plex.local:32400",
                "metadata_root": "",
                "token": "replacement-token",
                "clear_token": "",
            },
        )
        self.assertEqual(response.status_code, 303)
        secrets = main.provider_secrets.load()
        self.assertEqual(secrets["plex_token"], "replacement-token")
        self.assertEqual(
            secrets["plex_token_endpoint"],
            "https://replacement-plex.local:32400",
        )
        source = main.external_source_config.source("plex")
        self.assertTrue(secrets["plex_token_generation"])
        self.assertEqual(
            secrets["plex_token_generation"],
            source.credential_generation,
        )

    def test_changed_server_url_accepts_explicit_replacement_token(self):
        response = self.client.post(
            "/settings/integrations/plex",
            data={
                "enabled": "1",
                "server_url": "https://replacement-plex.local:32400",
                "metadata_root": "",
                "token": "replacement-token",
                "clear_token": "",
            },
        )
        self.assertEqual(response.status_code, 303)

        source = main.external_source_config.source("plex")
        secrets = main.provider_secrets.load()
        self.assertEqual(source.server_url, "https://replacement-plex.local:32400")
        self.assertTrue(source.enabled)
        self.assertEqual(secrets["plex_token"], "replacement-token")


if __name__ == "__main__":
    unittest.main()

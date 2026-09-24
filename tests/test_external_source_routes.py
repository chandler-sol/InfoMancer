from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.db import Database
from app.managed_ffmpeg import (
    ManagedFfmpegComponent,
    ManagedFfmpegError,
    ManagedFfmpegStatus,
)
from app.managed_speech import (
    ManagedSpeechComponentError,
    ManagedSpeechRuntimeStatus,
    ManagedWhisperCppRuntime,
    ManagedWhisperModel,
)
from app.media_identity.speech import SpeechBinaryIdentity
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
            main.ffmpeg_components,
            main.speech_runtime_component,
            main.speech_model_components,
        )
        main.db = database
        main.settings = settings
        main.external_source_config = ExternalSourceConfigService(database)
        main.provider_secrets = ProviderSecretStore(
            data / "provider-secrets.enc",
            "external-route-security-test-secret",
        )
        main.ffmpeg_components = ManagedFfmpegComponent(data)
        main.speech_runtime_component = ManagedWhisperCppRuntime(data)
        main.speech_model_components = {
            "base-q5_1": ManagedWhisperModel(data, "base-q5_1"),
            "base.en-q5_1": ManagedWhisperModel(data, "base.en-q5_1"),
        }

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
            main.ffmpeg_components,
            main.speech_runtime_component,
            main.speech_model_components,
        ) = self.original
        self.temporary.cleanup()

    def test_integrations_page_offers_managed_ffmpeg_when_unavailable(self):
        status = ManagedFfmpegStatus(
            state="unavailable",
            available=False,
            version="6.1.1",
            path="",
            detail="FFmpeg is not available.",
            can_install=True,
            can_remove=False,
        )
        with patch.object(main.ffmpeg_components, "status", return_value=status):
            response = self.client.get("/settings/integrations")

        self.assertEqual(response.status_code, 200)
        self.assertIn("FFmpeg frame extraction", response.text)
        self.assertIn("Install FFmpeg for InfoMancer", response.text)
        self.assertIn("/settings/integrations/ffmpeg/install", response.text)
        self.assertNotIn("Remove managed FFmpeg", response.text)

    def test_managed_ffmpeg_install_route_redirects_after_verified_install(self):
        installed = Path(self.temporary.name) / "components" / "ffmpeg" / "6.1.1" / "ffmpeg"
        with patch.object(
            main.ffmpeg_components,
            "install",
            return_value=installed,
        ) as install:
            response = self.client.post("/settings/integrations/ffmpeg/install")

        self.assertEqual(response.status_code, 303)
        self.assertIn("/settings/integrations", response.headers["location"])
        self.assertIn("downloaded", response.headers["location"])
        install.assert_called_once_with()

    def test_managed_ffmpeg_install_failure_returns_to_settings_without_500(self):
        with patch.object(
            main.ffmpeg_components,
            "install",
            side_effect=ManagedFfmpegError("fixture integrity failure"),
        ):
            response = self.client.post("/settings/integrations/ffmpeg/install")

        self.assertEqual(response.status_code, 303)
        self.assertIn("fixture+integrity+failure", response.headers["location"])

    def test_managed_ffmpeg_remove_route_only_calls_component_manager(self):
        with patch.object(main.ffmpeg_components, "remove") as remove:
            response = self.client.post("/settings/integrations/ffmpeg/remove")

        self.assertEqual(response.status_code, 303)
        self.assertIn("/settings/integrations", response.headers["location"])
        self.assertIn("removed", response.headers["location"])
        remove.assert_called_once_with()

    def test_integrations_page_exposes_separate_local_speech_components(self):
        runtime_status = ManagedSpeechRuntimeStatus(
            state="unavailable",
            available=False,
            version="1.9.4",
            path="",
            detail="whisper.cpp is not available.",
            can_install=True,
            can_remove=False,
            identity=None,
        )
        with patch.object(
            main.speech_runtime_component,
            "status",
            return_value=runtime_status,
        ):
            response = self.client.get("/settings/integrations")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Local speech analysis", response.text)
        self.assertIn("Install whisper.cpp for InfoMancer", response.text)
        self.assertIn(
            "/settings/integrations/speech/runtime/install",
            response.text,
        )
        self.assertIn(
            "/settings/integrations/speech/models/base-q5_1/install",
            response.text,
        )
        self.assertIn(
            "/settings/integrations/speech/models/base.en-q5_1/install",
            response.text,
        )
        self.assertIn("CPU only", response.text)

    def test_managed_speech_runtime_install_route_uses_verified_manager(self):
        installed = Path(self.temporary.name) / "whisper-cli"
        identity = SpeechBinaryIdentity(
            key="whisper.cpp",
            version="1.9.4",
            sha256="d" * 64,
            size_bytes=123,
            source="fixture",
            license_id="MIT",
        )
        with patch.object(
            main.speech_runtime_component,
            "install",
            return_value=(installed, identity),
        ) as install:
            response = self.client.post(
                "/settings/integrations/speech/runtime/install"
            )

        self.assertEqual(response.status_code, 303)
        self.assertIn("/settings/integrations", response.headers["location"])
        self.assertIn("downloaded", response.headers["location"])
        install.assert_called_once_with()

    def test_managed_speech_runtime_failure_returns_without_500(self):
        with patch.object(
            main.speech_runtime_component,
            "install",
            side_effect=ManagedSpeechComponentError(
                "fixture speech integrity failure"
            ),
        ):
            response = self.client.post(
                "/settings/integrations/speech/runtime/install"
            )

        self.assertEqual(response.status_code, 303)
        self.assertIn(
            "fixture+speech+integrity+failure",
            response.headers["location"],
        )

    def test_managed_speech_model_routes_are_allowlisted(self):
        model = main.speech_model_components["base-q5_1"]
        installed = Path(self.temporary.name) / "ggml-base-q5_1.bin"
        with patch.object(
            model,
            "install",
            return_value=(installed, model.identity),
        ) as install:
            response = self.client.post(
                "/settings/integrations/speech/models/base-q5_1/install"
            )
        self.assertEqual(response.status_code, 303)
        install.assert_called_once_with()

        with patch.object(model, "remove") as remove:
            response = self.client.post(
                "/settings/integrations/speech/models/base-q5_1/remove"
            )
        self.assertEqual(response.status_code, 303)
        remove.assert_called_once_with()

        response = self.client.post(
            "/settings/integrations/speech/models/not-a-model/install"
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("Unknown+managed+Whisper+model", response.headers["location"])

    def test_integrations_page_reports_plex_adapter_ready_for_normal(self):
        local_root = Path(self.temporary.name) / "media"
        main.external_source_config.add_mapping(
            "plex",
            "/srv/tv",
            str(local_root),
        )

        response = self.client.get("/settings/integrations")

        self.assertEqual(response.status_code, 200)
        self.assertIn("BIF adapter", response.text)
        self.assertIn(
            "<strong>Ready</strong><span>BIF adapter</span>",
            response.text,
        )
        self.assertIn(
            "Plex BIF preview frames are ready for Episode Identity.",
            response.text,
        )
        self.assertIn(
            "existing preview frames are available to Normal verification",
            response.text,
        )
        self.assertNotIn(
            "Preview-frame analysis becomes available when a consuming analyzer is installed.",
            response.text,
        )
        self.assertIn("Allow this Plex token over plain HTTP", response.text)
        self.assertIn('placeholder="https://plex.local:32400"', response.text)
        self.assertIn("Auto-detect, or enter a custom Plex data root", response.text)
        self.assertIn("Leave blank for Auto detection", response.text)

    def test_integrations_page_reports_jellyfin_adapter_ready_for_normal(self):
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
        self.assertIn(
            "<strong>Ready</strong><span>Trickplay adapter</span>",
            response.text,
        )
        self.assertIn(
            "Jellyfin Trickplay preview frames are ready for Episode Identity.",
            response.text,
        )
        self.assertIn(
            "existing preview frames are available to Normal verification",
            response.text,
        )

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

    def test_plex_plain_http_rejects_non_checkbox_truthy_strings(self):
        for submitted in ("false", "0", "yes", "on"):
            with self.subTest(submitted=submitted):
                response = self.client.post(
                    "/settings/integrations/plex",
                    data={
                        "enabled": "1",
                        "server_url": "http://plex.local:32400",
                        "metadata_root": "",
                        "token": "replacement-token",
                        "clear_token": "",
                        "allow_insecure_http": submitted,
                    },
                )
                self.assertEqual(response.status_code, 303)
                self.assertIn("plain+HTTP", response.headers["location"])
                source = main.external_source_config.source("plex")
                secrets = main.provider_secrets.load()
                self.assertEqual(
                    source.server_url,
                    "https://trusted-plex.local:32400",
                )
                self.assertEqual(secrets["plex_token"], "trusted-token")

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

    def test_invalid_plex_metadata_root_cannot_replace_saved_token(self):
        response = self.client.post(
            "/settings/integrations/plex",
            data={
                "enabled": "1",
                "server_url": "https://trusted-plex.local:32400",
                "metadata_root": "relative/plex-data",
                "token": "replacement-token",
                "clear_token": "",
            },
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("absolute", response.headers["location"])

        source = main.external_source_config.source("plex")
        secrets = main.provider_secrets.load()
        self.assertEqual(source.server_url, "https://trusted-plex.local:32400")
        self.assertEqual(source.metadata_root, "")
        self.assertEqual(source.credential_generation, "trusted-generation")
        self.assertEqual(secrets["plex_token"], "trusted-token")
        self.assertEqual(
            secrets["plex_token_endpoint"],
            "https://trusted-plex.local:32400",
        )
        self.assertEqual(
            secrets["plex_token_generation"],
            "trusted-generation",
        )

    def test_empty_enabled_server_url_cannot_replace_saved_token(self):
        response = self.client.post(
            "/settings/integrations/plex",
            data={
                "enabled": "1",
                "server_url": "",
                "metadata_root": "",
                "token": "replacement-token",
                "clear_token": "",
            },
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("server+URL", response.headers["location"])

        source = main.external_source_config.source("plex")
        secrets = main.provider_secrets.load()
        self.assertEqual(source.server_url, "https://trusted-plex.local:32400")
        self.assertEqual(source.credential_generation, "trusted-generation")
        self.assertEqual(secrets["plex_token"], "trusted-token")
        self.assertEqual(
            secrets["plex_token_endpoint"],
            "https://trusted-plex.local:32400",
        )
        self.assertEqual(
            secrets["plex_token_generation"],
            "trusted-generation",
        )

    def test_empty_enabled_server_url_cannot_delete_saved_token(self):
        response = self.client.post(
            "/settings/integrations/plex",
            data={
                "enabled": "1",
                "server_url": "",
                "metadata_root": "",
                "token": "",
                "clear_token": "1",
            },
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("server+URL", response.headers["location"])

        source = main.external_source_config.source("plex")
        secrets = main.provider_secrets.load()
        self.assertEqual(source.server_url, "https://trusted-plex.local:32400")
        self.assertEqual(source.credential_generation, "trusted-generation")
        self.assertEqual(secrets["plex_token"], "trusted-token")
        self.assertEqual(
            secrets["plex_token_endpoint"],
            "https://trusted-plex.local:32400",
        )
        self.assertEqual(
            secrets["plex_token_generation"],
            "trusted-generation",
        )

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

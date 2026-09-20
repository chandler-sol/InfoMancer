from __future__ import annotations

import json
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from app.db import Database
from app.migrations import CURRENT_SCHEMA_VERSION
from app.media_identity.external import ExternalCapability
from app.media_identity.models import (
    AnalyzerContext,
    IdentityProfile,
    IdentityReference,
    MediaIdentityFile,
)
from app.media_identity.external_config import (
    ExternalSourceConfigError,
    ExternalConnectionResult,
    ExternalSourceConfigService,
    build_configured_source_registry,
    normalize_server_url,
    test_external_connection,
)


class DummyResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.status = status
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self, _limit: int = -1) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class DummyOpener:
    def __init__(self, response: DummyResponse) -> None:
        self.response = response
        self.request = None
        self.timeout = None

    def open(self, request, timeout=None):
        self.request = request
        self.timeout = timeout
        return self.response


class ExternalSourceConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name)
        self.database = Database(self.data / "infomancer.db")
        self.database.initialize()
        self.service = ExternalSourceConfigService(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def test_migration_seeds_disabled_plex_and_jellyfin_shells(self):
        sources = {source.source_key: source for source in self.service.sources()}
        self.assertEqual(set(sources), {"plex", "jellyfin"})
        self.assertFalse(sources["plex"].enabled)
        self.assertFalse(sources["jellyfin"].enabled)
        with self.database.connect() as conn:
            version = conn.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
        self.assertEqual(version, CURRENT_SCHEMA_VERSION)

    def test_connection_result_is_persisted_for_status_ui(self):
        self.service.save_source(
            "plex", enabled=False, server_url="https://plex.local:32400"
        )
        self.service.record_connection_result(
            ExternalConnectionResult(
                "plex",
                True,
                server_name="Living Room Plex",
                version="1.2.3",
                detail="Authenticated Plex connection succeeded.",
            ),
            tested_server_url="https://plex.local:32400",
            tested_revision=self.service.source("plex").config_revision,
        )
        source = self.service.source("plex")
        self.assertEqual(source.last_test_status, "ok")
        self.assertEqual(source.last_test_server_name, "Living Room Plex")
        self.assertEqual(source.last_test_version, "1.2.3")
        self.assertIsNotNone(source.last_test_at)

    def test_plex_registry_keeps_preview_capability_hidden_until_consumer_exists(self):
        local_root = self.data / "media"
        self.service.add_mapping(
            "plex",
            "/srv/tv",
            str(local_root),
        )
        source = self.service.save_source(
            "plex",
            enabled=True,
            server_url="https://plex.local:32400",
            metadata_root=str(self.data / "plex-data"),
            credential_generation="generation-1",
        )
        secrets = {
            "plex_token": "secret",
            "plex_token_endpoint": source.server_url,
            "plex_token_generation": "generation-1",
        }

        registry = build_configured_source_registry(self.service, secrets)
        self.assertEqual(registry.keys(), ("jellyfin", "plex"))
        plex = registry.require("plex")
        status = plex.status()
        self.assertTrue(status.available)
        self.assertEqual(status.capabilities, frozenset())
        self.assertIn("BIF adapter", status.detail)
        self.assertIn("consuming analyzer", status.detail)
        self.assertEqual(
            registry.available_for(ExternalCapability.PREVIEW_FRAMES),
            (),
        )
        self.assertEqual(plex.metadata_root, str(self.data / "plex-data"))
        self.assertFalse(registry.require("jellyfin").status().available)

        stale_registry = build_configured_source_registry(
            self.service,
            {
                **secrets,
                "plex_token_generation": "stale-generation",
            },
        )
        stale_status = stale_registry.require("plex").status()
        self.assertFalse(stale_status.available)
        self.assertEqual(stale_status.capabilities, frozenset())

    def test_plex_registry_requires_explicit_plain_http_opt_in(self):
        local_root = self.data / "media"
        self.service.add_mapping("plex", "/srv/tv", str(local_root))
        source = self.service.save_source(
            "plex",
            enabled=True,
            server_url="http://plex.local:32400",
            config={"allow_insecure_http": False},
            credential_generation="generation-1",
        )
        secrets = {
            "plex_token": "secret",
            "plex_token_endpoint": source.server_url,
            "plex_token_generation": "generation-1",
        }

        blocked = build_configured_source_registry(self.service, secrets)
        blocked_status = blocked.require("plex").status()
        self.assertFalse(blocked_status.available)
        self.assertIn("plain HTTP", blocked_status.detail)

        allowed_source = self.service.save_source(
            "plex",
            enabled=True,
            server_url=source.server_url,
            config={"allow_insecure_http": True},
        )
        allowed = build_configured_source_registry(self.service, secrets)
        allowed_status = allowed.require("plex").status()
        self.assertTrue(allowed_status.available)
        self.assertTrue(allowed.require("plex").allow_insecure_http)
        self.assertEqual(
            allowed_source.config_revision,
            source.config_revision + 1,
        )

    def test_configured_plex_registry_resolves_media_through_real_adapter(self):
        local_root = self.data / "media"
        local_path = local_root / "Show" / "Season 01" / "Episode.mkv"
        external_path = "/srv/tv/Show/Season 01/Episode.mkv"
        self.service.add_mapping(
            "plex",
            "/srv/tv",
            str(local_root),
        )
        source = self.service.save_source(
            "plex",
            enabled=True,
            server_url="https://plex.local:32400",
            credential_generation="generation-1",
        )
        registry = build_configured_source_registry(
            self.service,
            {
                "plex_token": "secret",
                "plex_token_endpoint": source.server_url,
                "plex_token_generation": "generation-1",
            },
        )
        candidate = {
            "ratingKey": "101",
            "updatedAt": 123456,
            "Guid": [{"id": "tvdb://12345"}],
            "Media": [
                {
                    "id": "301",
                    "Part": [
                        {
                            "id": "501",
                            "file": external_path,
                            "key": "/library/parts/501/123/file.mkv",
                            "indexes": "sd",
                        }
                    ],
                }
            ],
        }
        context = AnalyzerContext(
            media=MediaIdentityFile(
                file_id=1,
                title_id=1,
                path=str(local_path),
                size_bytes=1,
                modified_at=1.0,
            ),
            claimed_identity=IdentityReference(
                identity_kind="episode",
                season=1,
                episode=2,
                display_name="Episode",
            ),
            profile=IdentityProfile.DEEP,
        )

        with (
            patch(
                "app.media_identity.sources.plex.fetch_plex_episode_candidates",
                return_value=(candidate,),
            ) as candidates,
            patch(
                "app.media_identity.sources.plex.fetch_plex_item",
                return_value=candidate,
            ) as item_fetch,
        ):
            resolved = registry.require("plex").resolve_media(context)

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.item_id, "101")
        self.assertEqual(resolved.media_source_id, "501")
        self.assertEqual(resolved.path, external_path)
        candidates.assert_called_once_with(
            "https://plex.local:32400",
            "secret",
            season=1,
            episode=2,
            allow_insecure_http=False,
        )
        item_fetch.assert_called_once_with(
            "https://plex.local:32400",
            "secret",
            "101",
            allow_insecure_http=False,
        )

    def test_plex_preview_capability_requires_a_path_mapping(self):
        source = self.service.save_source(
            "plex",
            enabled=True,
            server_url="https://plex.local:32400",
            credential_generation="generation-1",
        )
        registry = build_configured_source_registry(
            self.service,
            {
                "plex_token": "secret",
                "plex_token_endpoint": source.server_url,
                "plex_token_generation": "generation-1",
            },
        )
        status = registry.require("plex").status()
        self.assertFalse(status.available)
        self.assertEqual(status.capabilities, frozenset())
        self.assertIn("path mapping", status.detail.casefold())

    def test_jellyfin_registry_keeps_preview_capability_hidden_until_consumer_exists(self):
        local_root = self.data / "media"
        self.service.add_mapping(
            "jellyfin",
            "/srv/tv",
            str(local_root),
        )
        source = self.service.save_source(
            "jellyfin",
            enabled=True,
            server_url="https://jellyfin.local:8096",
            credential_generation="generation-1",
        )
        secrets = {
            "jellyfin_token": "jf-secret",
            "jellyfin_token_endpoint": source.server_url,
            "jellyfin_token_generation": "generation-1",
        }

        registry = build_configured_source_registry(self.service, secrets)
        jellyfin = registry.require("jellyfin")
        status = jellyfin.status()
        self.assertTrue(status.available)
        self.assertEqual(status.capabilities, frozenset())
        self.assertIn("consuming analyzer", status.detail)
        self.assertEqual(
            registry.available_for(ExternalCapability.PREVIEW_FRAMES),
            (),
        )

        stale_registry = build_configured_source_registry(
            self.service,
            {
                **secrets,
                "jellyfin_token_generation": "stale-generation",
            },
        )
        stale_status = stale_registry.require("jellyfin").status()
        self.assertFalse(stale_status.available)
        self.assertNotIn(
            ExternalCapability.PREVIEW_FRAMES,
            stale_status.capabilities,
        )
        self.assertEqual(
            stale_registry.available_for(ExternalCapability.PREVIEW_FRAMES),
            (),
        )

    def test_jellyfin_preview_capability_requires_a_path_mapping(self):
        source = self.service.save_source(
            "jellyfin",
            enabled=True,
            server_url="https://jellyfin.local:8096",
            credential_generation="generation-1",
        )
        registry = build_configured_source_registry(
            self.service,
            {
                "jellyfin_token": "jf-secret",
                "jellyfin_token_endpoint": source.server_url,
                "jellyfin_token_generation": "generation-1",
            },
        )
        status = registry.require("jellyfin").status()
        self.assertFalse(status.available)
        self.assertEqual(status.capabilities, frozenset())
        self.assertIn("path mapping", status.detail.casefold())

    def test_clearing_connection_result_removes_stale_success(self):
        self.service.save_source(
            "plex", enabled=False, server_url="https://plex.local:32400"
        )
        self.service.record_connection_result(
            ExternalConnectionResult(
                "plex", True, server_name="Plex", version="1.2.3", detail="ok"
            ),
            tested_server_url="https://plex.local:32400",
            tested_revision=self.service.source("plex").config_revision,
        )
        self.service.clear_connection_result("plex")
        source = self.service.source("plex")
        self.assertEqual(source.last_test_status, "")
        self.assertEqual(source.last_test_server_name, "")
        self.assertEqual(source.last_test_version, "")
        self.assertIsNone(source.last_test_at)

    def test_failed_connection_result_degrades_configured_source_availability(self):
        self.service.save_source(
            "plex",
            enabled=True,
            server_url="https://plex.local:32400",
            credential_generation="generation-1",
        )
        self.service.record_connection_result(
            ExternalConnectionResult(
                "plex", False, detail="The server rejected the access token."
            ),
            tested_server_url="https://plex.local:32400",
            tested_revision=self.service.source("plex").config_revision,
        )
        registry = build_configured_source_registry(
            self.service, {
                "plex_token": "secret",
                "plex_token_endpoint": "https://plex.local:32400",
                "plex_token_generation": "generation-1",
            }
        )
        status = registry.require("plex").status()
        self.assertFalse(status.available)
        self.assertIn("failed", status.detail)

    def test_unadopted_secret_generation_is_not_bound(self):
        source = self.service.save_source(
            "plex",
            enabled=True,
            server_url="https://plex.local:32400",
            credential_generation="generation-old",
        )
        registry = build_configured_source_registry(
            self.service,
            {
                "plex_token": "new-token",
                "plex_token_endpoint": source.server_url,
                "plex_token_generation": "generation-new",
            },
        )
        status = registry.require("plex").status()
        self.assertFalse(status.available)
        self.assertIn("token", status.detail.casefold())

    def test_credential_generation_change_bumps_revision_only_when_adopted(self):
        source = self.service.save_source(
            "plex",
            enabled=True,
            server_url="https://plex.local:32400",
            credential_generation="generation-old",
        )
        same = self.service.save_source(
            "plex",
            enabled=True,
            server_url=source.server_url,
        )
        self.assertEqual(same.config_revision, source.config_revision)
        self.assertEqual(same.credential_generation, "generation-old")

        adopted = self.service.save_source(
            "plex",
            enabled=True,
            server_url=source.server_url,
            credential_generation="generation-new",
        )
        self.assertEqual(adopted.config_revision, source.config_revision + 1)
        self.assertEqual(adopted.credential_generation, "generation-new")

    def test_plex_metadata_root_change_bumps_revision(self):
        first_root = self.data / "plex-a"
        second_root = self.data / "plex-b"
        source = self.service.save_source(
            "plex",
            enabled=True,
            server_url="https://plex.local:32400",
            metadata_root=str(first_root),
            credential_generation="generation-1",
        )
        changed = self.service.save_source(
            "plex",
            enabled=True,
            server_url=source.server_url,
            metadata_root=str(second_root),
        )
        self.assertEqual(changed.config_revision, source.config_revision + 1)
        self.assertEqual(changed.metadata_root, str(second_root))

    def test_plex_metadata_root_must_be_absolute(self):
        with self.assertRaisesRegex(ExternalSourceConfigError, "absolute"):
            self.service.save_source(
                "plex",
                enabled=False,
                server_url="https://plex.local:32400",
                metadata_root="relative/plex-data",
            )

    def test_connection_revision_increments_when_transport_policy_changes(self):
        source = self.service.save_source(
            "jellyfin",
            enabled=True,
            server_url="https://jellyfin.local:8096",
            config={"allow_insecure_http": False},
            credential_generation="generation-1",
        )
        changed = self.service.save_source(
            "jellyfin",
            enabled=True,
            server_url=source.server_url,
            config={"allow_insecure_http": True},
        )
        self.assertEqual(changed.config_revision, source.config_revision + 1)

    def test_connection_revision_increments_for_endpoint_or_token_identity_change(self):
        initial = self.service.source("plex")
        saved = self.service.save_source(
            "plex", enabled=True, server_url="https://plex.local:32400"
        )
        self.assertGreater(saved.config_revision, initial.config_revision)

        same_endpoint = self.service.save_source(
            "plex", enabled=True, server_url="https://plex.local:32400"
        )
        self.assertEqual(same_endpoint.config_revision, saved.config_revision)

        token_change = self.service.save_source(
            "plex",
            enabled=True,
            server_url="https://plex.local:32400",
            credential_generation="generation-2",
        )
        self.assertEqual(token_change.config_revision, saved.config_revision + 1)
        self.assertEqual(token_change.credential_generation, "generation-2")

    def test_stale_connection_test_revision_is_not_surfaced(self):
        source = self.service.save_source(
            "plex", enabled=True, server_url="https://plex.local:32400"
        )
        recorded = self.service.record_connection_result(
            ExternalConnectionResult(
                "plex", True, server_name="Plex", version="1", detail="ok"
            ),
            tested_server_url=source.server_url,
            tested_revision=source.config_revision,
        )
        self.assertTrue(recorded)
        changed = self.service.save_source(
            "plex",
            enabled=True,
            server_url=source.server_url,
            credential_generation="generation-2",
        )
        self.assertEqual(changed.last_test_status, "")
        self.assertIsNone(changed.last_test_at)

    def test_connection_result_is_discarded_if_endpoint_changed_during_test(self):
        self.service.save_source(
            "plex", enabled=True, server_url="https://plex-a.local:32400"
        )
        self.service.save_source(
            "plex", enabled=True, server_url="https://plex-b.local:32400"
        )
        recorded = self.service.record_connection_result(
            ExternalConnectionResult(
                "plex", True, server_name="Old Plex", version="1.0", detail="ok"
            ),
            tested_server_url="https://plex-a.local:32400",
            tested_revision=0,
        )
        self.assertFalse(recorded)
        source = self.service.source("plex")
        self.assertEqual(source.server_url, "https://plex-b.local:32400")
        self.assertEqual(source.last_test_status, "")
        self.assertIsNone(source.last_test_at)

    def test_source_url_normalization_rejects_non_ascii_characters(self):
        with self.assertRaisesRegex(ExternalSourceConfigError, "non-ASCII"):
            normalize_server_url("http://jellyfin.local/jellyfín")

    def test_source_url_normalization_rejects_embedded_credentials_and_queries(self):
        self.assertEqual(
            normalize_server_url("HTTP://plex.local:32400/"),
            "http://plex.local:32400",
        )
        self.assertEqual(
            normalize_server_url("https://example.test/jellyfin/"),
            "https://example.test/jellyfin",
        )
        with self.assertRaisesRegex(ExternalSourceConfigError, "credentials"):
            normalize_server_url("http://user:secret@plex.local:32400")
        with self.assertRaisesRegex(ExternalSourceConfigError, "query"):
            normalize_server_url("https://plex.local:32400/?token=secret")
        with self.assertRaisesRegex(ExternalSourceConfigError, "complete"):
            normalize_server_url("plex.local:32400")
        with self.assertRaisesRegex(ExternalSourceConfigError, "not valid"):
            normalize_server_url("http://[::1")
        with self.assertRaisesRegex(ExternalSourceConfigError, "not valid"):
            normalize_server_url("http://plex.local:99999")
        with self.assertRaisesRegex(ExternalSourceConfigError, "whitespace"):
            normalize_server_url("https://plex.local:32400/path with space")
        with self.assertRaisesRegex(ExternalSourceConfigError, "whitespace"):
            normalize_server_url("http://plex local:32400")
        with self.assertRaisesRegex(ExternalSourceConfigError, "whitespace"):
            normalize_server_url(" https://plex.local:32400")
        with self.assertRaisesRegex(ExternalSourceConfigError, "whitespace"):
            normalize_server_url("https://plex.local:32400 ")
        with self.assertRaisesRegex(ExternalSourceConfigError, "whitespace"):
            normalize_server_url("https://plex.local:32400\t")
        with self.assertRaisesRegex(ExternalSourceConfigError, "whitespace"):
            normalize_server_url("https://plex.local:32400\n")

    def test_enabled_source_requires_url_but_disabled_shell_can_be_saved(self):
        with self.assertRaisesRegex(ExternalSourceConfigError, "URL"):
            self.service.save_source("plex", enabled=True, server_url="")
        saved = self.service.save_source(
            "plex",
            enabled=False,
            server_url="https://plex.local:32400",
            metadata_root="/var/lib/plex",
        )
        self.assertFalse(saved.enabled)
        self.assertEqual(saved.server_url, "https://plex.local:32400")
        self.assertEqual(saved.metadata_root, "/var/lib/plex")

    def test_mapping_translates_windows_external_path_and_finds_catalog_file(self):
        local_root = self.data / "media" / "tv"
        episode = local_root / "Show" / "Season 01" / "Episode.mkv"
        episode.parent.mkdir(parents=True)
        episode.write_bytes(b"episode")
        with self.database.connect() as conn:
            root_id = conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES (?,?,?)",
                (str(local_root), "tv", "TV"),
            ).lastrowid
            title_id = conn.execute(
                """INSERT INTO titles(root_id,kind,title,folder_path)
                   VALUES (?,?,?,?)""",
                (root_id, "tv", "Show", str(local_root / "Show")),
            ).lastrowid
            file_id = conn.execute(
                """INSERT INTO files(
                     title_id,path,filename,extension,size_bytes,modified_at,seen_scan
                   ) VALUES (?,?,?,?,?,?,?)""",
                (
                    title_id,
                    str(episode),
                    episode.name,
                    ".mkv",
                    episode.stat().st_size,
                    episode.stat().st_mtime,
                    "test",
                ),
            ).lastrowid

        self.service.add_mapping("plex", r"D:\TV", str(local_root), priority=10)
        result = self.service.test_mapping(
            "plex",
            r"d:\tv\Show\Season 01\Episode.mkv",
        )
        self.assertTrue(result["matched"])
        self.assertTrue(result["exists"])
        self.assertEqual(result["local_path"], str(episode))
        self.assertEqual(result["catalog_match"]["id"], file_id)
        self.assertEqual(result["catalog_match"]["title_name"], "Show")

    def test_mapping_miss_is_safe_and_does_not_guess(self):
        local_root = self.data / "media"
        self.service.add_mapping("jellyfin", "/srv/tv", str(local_root))
        result = self.service.test_mapping(
            "jellyfin", "/srv/movies/Alien/Alien.mkv"
        )
        self.assertFalse(result["matched"])
        self.assertEqual(result["local_path"], "")
        self.assertIsNone(result["catalog_match"])

    def test_duplicate_mapping_is_rejected(self):
        local_root = self.data / "media"
        self.service.add_mapping("plex", "/srv/tv", str(local_root))
        with self.assertRaisesRegex(ExternalSourceConfigError, "already exists"):
            self.service.add_mapping("plex", "/srv/tv", str(local_root))

    def test_mapping_delete_is_source_scoped(self):
        local_root = self.data / "media"
        mapping_id = self.service.add_mapping("plex", "/srv/tv", str(local_root))
        self.assertFalse(self.service.delete_mapping("jellyfin", mapping_id))
        self.assertTrue(self.service.delete_mapping("plex", mapping_id))
        self.assertEqual(self.service.mappings("plex"), ())

    def test_plex_connection_test_uses_header_token_not_url(self):
        opener = DummyOpener(
            DummyResponse({"MediaContainer": {"friendlyName": "Plex", "version": "1.2.3"}})
        )
        with patch(
            "app.media_identity.external_config.urllib.request.build_opener",
            return_value=opener,
        ) as builder:
            result = test_external_connection(
                "plex", "https://plex.local:32400", "top-secret", timeout=3
            )
        handlers = builder.call_args.args
        proxy_handlers = [
            handler
            for handler in handlers
            if isinstance(handler, urllib.request.ProxyHandler)
        ]
        self.assertEqual(len(proxy_handlers), 1)
        self.assertEqual(proxy_handlers[0].proxies, {})
        self.assertTrue(result.ok)
        self.assertEqual(result.server_name, "Plex")
        self.assertNotIn("top-secret", opener.request.full_url)
        self.assertEqual(opener.request.get_header("X-plex-token"), "top-secret")
        self.assertLessEqual(opener.timeout, 15.0)

    def test_plex_connection_test_rejects_plain_http_without_opt_in(self):
        with patch(
            "app.media_identity.external_config.urllib.request.build_opener"
        ) as builder:
            with self.assertRaisesRegex(ExternalSourceConfigError, "plain HTTP"):
                test_external_connection(
                    "plex",
                    "http://plex.local:32400",
                    "top-secret",
                )
        builder.assert_not_called()

    def test_plex_connection_test_allows_explicit_insecure_http_opt_in(self):
        opener = DummyOpener(
            DummyResponse(
                {"MediaContainer": {"friendlyName": "Plex", "version": "1.2.3"}}
            )
        )
        with patch(
            "app.media_identity.external_config.urllib.request.build_opener",
            return_value=opener,
        ):
            result = test_external_connection(
                "plex",
                "http://plex.local:32400",
                "top-secret",
                allow_insecure_http=True,
            )
        self.assertTrue(result.ok)
        self.assertEqual(opener.request.full_url, "http://plex.local:32400/")

    def test_jellyfin_connection_test_rejects_plain_http_without_opt_in(self):
        with patch(
            "app.media_identity.external_config.urllib.request.build_opener"
        ) as builder:
            with self.assertRaisesRegex(ExternalSourceConfigError, "plain HTTP"):
                test_external_connection(
                    "jellyfin",
                    "http://jellyfin.local:8096",
                    "jf-secret",
                )
        builder.assert_not_called()

    def test_jellyfin_connection_test_allows_explicit_insecure_http_opt_in(self):
        opener = DummyOpener(
            DummyResponse({"ServerName": "Jellyfin", "Version": "10.11.0", "Id": "server"})
        )
        with patch(
            "app.media_identity.external_config.urllib.request.build_opener",
            return_value=opener,
        ):
            result = test_external_connection(
                "jellyfin",
                "http://jellyfin.local:8096",
                "jf-secret",
                allow_insecure_http=True,
            )
        self.assertTrue(result.ok)
        self.assertEqual(
            opener.request.full_url,
            "http://jellyfin.local:8096/System/Info",
        )

    def test_jellyfin_connection_test_uses_authenticated_system_info(self):
        opener = DummyOpener(
            DummyResponse({"ServerName": "Jellyfin", "Version": "10.11.0", "Id": "server"})
        )
        with patch("app.media_identity.external_config.urllib.request.build_opener", return_value=opener):
            result = test_external_connection(
                "jellyfin", "https://jellyfin.local:8096", "jf-secret"
            )
        self.assertTrue(result.ok)
        self.assertEqual(
            opener.request.full_url,
            "https://jellyfin.local:8096/System/Info",
        )
        self.assertEqual(opener.request.get_header("X-emby-token"), "jf-secret")


if __name__ == "__main__":
    unittest.main()

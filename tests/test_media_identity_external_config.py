from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db import Database
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
        self.assertEqual(version, 21)

    def test_connection_result_is_persisted_for_status_ui(self):
        self.service.save_source(
            "plex", enabled=False, server_url="http://plex.local:32400"
        )
        self.service.record_connection_result(
            ExternalConnectionResult(
                "plex",
                True,
                server_name="Living Room Plex",
                version="1.2.3",
                detail="Authenticated Plex connection succeeded.",
            )
        )
        source = self.service.source("plex")
        self.assertEqual(source.last_test_status, "ok")
        self.assertEqual(source.last_test_server_name, "Living Room Plex")
        self.assertEqual(source.last_test_version, "1.2.3")
        self.assertIsNotNone(source.last_test_at)

    def test_configured_source_registry_has_no_evidence_capabilities_yet(self):
        self.service.save_source(
            "plex", enabled=True, server_url="http://plex.local:32400"
        )
        registry = build_configured_source_registry(
            self.service, {"plex_token": "secret"}
        )
        self.assertEqual(registry.keys(), ("jellyfin", "plex"))
        plex = registry.require("plex")
        status = plex.status()
        self.assertTrue(status.available)
        self.assertEqual(status.capabilities, frozenset())
        self.assertIn("source-specific analysis adapter", status.detail)
        self.assertFalse(registry.require("jellyfin").status().available)

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
            normalize_server_url("http://plex.local:32400/?token=secret")
        with self.assertRaisesRegex(ExternalSourceConfigError, "complete"):
            normalize_server_url("plex.local:32400")

    def test_enabled_source_requires_url_but_disabled_shell_can_be_saved(self):
        with self.assertRaisesRegex(ExternalSourceConfigError, "URL"):
            self.service.save_source("plex", enabled=True, server_url="")
        saved = self.service.save_source(
            "plex",
            enabled=False,
            server_url="http://plex.local:32400",
            metadata_root="/var/lib/plex",
        )
        self.assertFalse(saved.enabled)
        self.assertEqual(saved.server_url, "http://plex.local:32400")
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
        with patch("app.media_identity.external_config.urllib.request.build_opener", return_value=opener):
            result = test_external_connection(
                "plex", "http://plex.local:32400", "top-secret", timeout=3
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.server_name, "Plex")
        self.assertNotIn("top-secret", opener.request.full_url)
        self.assertEqual(opener.request.get_header("X-plex-token"), "top-secret")
        self.assertLessEqual(opener.timeout, 15.0)

    def test_jellyfin_connection_test_uses_authenticated_system_info(self):
        opener = DummyOpener(
            DummyResponse({"ServerName": "Jellyfin", "Version": "10.11.0", "Id": "server"})
        )
        with patch("app.media_identity.external_config.urllib.request.build_opener", return_value=opener):
            result = test_external_connection(
                "jellyfin", "http://jellyfin.local:8096", "jf-secret"
            )
        self.assertTrue(result.ok)
        self.assertEqual(
            opener.request.full_url,
            "http://jellyfin.local:8096/System/Info",
        )
        self.assertEqual(opener.request.get_header("X-emby-token"), "jf-secret")


if __name__ == "__main__":
    unittest.main()

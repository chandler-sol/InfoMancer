from __future__ import annotations

import tempfile
import unittest
from unittest import mock
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app import main
from app.db import Database
from app.media_identity.provider_cache import ProviderEpisodeCache
from app.request_security import LOCAL_CSRF_COOKIE
from app.tvdb import TVDBError


def _episode_payload(*, overview: str = "A provider synopsis for the pilot.") -> dict:
    return {
        "data": {
            "episodes": [{
                "id": 101,
                "seasonNumber": 1,
                "number": 1,
                "absoluteNumber": 1,
                "name": "Pilot",
                "overview": overview,
                "aired": "2026-01-01",
                "runtime": 24,
            }]
        },
        "links": {"next": None},
    }


class WorkflowTVDB:
    api_key = "testing-key"
    pin = ""

    def __init__(self, *, fail: bool = False, updated: str = "2026-09-20T12:00:00Z"):
        self.fail = fail
        self.updated = updated
        self.calls: list[tuple[str, int | None]] = []

    def _get(
        self, path: str, params: dict | None = None, *,
        allow_not_found: bool = False, _retry_auth: bool = True,
    ) -> dict:
        page = None if params is None else params.get("page")
        self.calls.append((path, page))
        if self.fail:
            raise TVDBError("simulated TVDB outage")
        if path == "/series/9001/extended":
            return {
                "data": {
                    "lastUpdated": self.updated,
                    "seasonTypes": [],
                }
            }
        if path == "/series/9001/episodes/default/eng" and int(page or 0) == 0:
            return deepcopy(_episode_payload())
        if allow_not_found:
            return {}
        raise AssertionError(f"Unexpected TVDB request: {path} page={page}")


class EpisodeIdentityProviderWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        media_root = self.root / "media"
        show_root = media_root / "Example Show"
        show_root.mkdir(parents=True)
        media_path = show_root / "Example Show - S01E01.mkv"
        media_bytes = b"episode-identity-workflow-fixture" * 8
        media_path.write_bytes(media_bytes)
        stat = media_path.stat()

        self.database = Database(self.root / "workflow.db")
        self.database.initialize()
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO roots(id,path,kind,label) VALUES (1,?,'tv','TV')",
                (str(media_root),),
            )
            conn.execute(
                """INSERT INTO titles(
                     id,root_id,kind,title,metadata_title,metadata_title_language,
                     folder_path,tvdb_id,poster_url,imdb_id,overview
                   ) VALUES (
                     1,1,'tv','Example Show','Example Show','eng',?,9001,
                     'https://example.invalid/poster.jpg','tt0000001','Series overview'
                   )""",
                (str(show_root),),
            )
            conn.execute(
                """INSERT INTO expected_episodes(
                     id,title_id,tvdb_episode_id,season,episode,name,aired
                   ) VALUES (1,1,101,1,1,'Pilot','2026-01-01')"""
            )
            conn.execute(
                """INSERT INTO files(
                     id,title_id,path,filename,extension,size_bytes,modified_at,
                     season,episode_start,episode_end,parsed_title,seen_scan
                   ) VALUES (1,1,?,?,?,?,?,1,1,1,'Example Show','fixture-scan')""",
                (
                    str(media_path),
                    media_path.name,
                    "mkv",
                    len(media_bytes),
                    stat.st_mtime,
                ),
            )

        self.original_db = main.db
        self.original_tvdb = main.tvdb
        main.db = self.database
        main.tvdb = WorkflowTVDB()
        self.auth_patch = mock.patch.object(
            main, "settings", replace(main.settings, auth_mode="disabled")
        )
        self.auth_patch.start()
        self.client = TestClient(main.app, follow_redirects=False)
        self.client.get("/")
        csrf_token = self.client.cookies.get(LOCAL_CSRF_COOKIE)
        self.assertTrue(csrf_token)
        self.client.headers.update({"X-CSRF-Token": csrf_token})

    def tearDown(self) -> None:
        self.client.close()
        self.auth_patch.stop()
        main.tvdb = self.original_tvdb
        main.db = self.original_db
        self.temporary.cleanup()

    @staticmethod
    def _message(response) -> str:
        return parse_qs(urlparse(response.headers.get("location", "")).query).get(
            "message", [""]
        )[0]

    def test_production_refresh_populates_cache_then_fast_verification_uses_it(self) -> None:
        cache = ProviderEpisodeCache(self.database)
        self.assertIsNone(cache.cache_status("tvdb", "9001"))

        refresh = self.client.post("/titles/1/metadata/enrich")
        self.assertEqual(refresh.status_code, 303)
        self.assertIn("Episode Identity provider data refreshed", self._message(refresh))

        status = cache.cache_status("tvdb", "9001")
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status["episode_count"], 1)
        self.assertEqual(status["mapping_count"], 1)

        verify = self.client.post("/files/1/episode-identity/fast")
        self.assertEqual(verify.status_code, 303)
        self.assertIn("/episode-identity/scans/", verify.headers["location"])
        self.assertNotIn("reduced evidence", self._message(verify).casefold())

        with self.database.connect() as conn:
            scan = conn.execute(
                """SELECT id,status FROM media_identity_scans
                   WHERE file_id=1 ORDER BY id DESC LIMIT 1"""
            ).fetchone()
            candidates = conn.execute(
                """SELECT provider_item_id,details_json
                   FROM media_identity_candidates
                   WHERE scan_id=? ORDER BY rank""",
                (int(scan["id"]),),
            ).fetchall()
        self.assertEqual(scan["status"], "complete")
        self.assertEqual([str(row["provider_item_id"]) for row in candidates], ["101"])
        self.assertIn("A provider synopsis for the pilot.", candidates[0]["details_json"])

    def test_failed_explicit_refresh_preserves_previous_cache_for_offline_reuse(self) -> None:
        cache = ProviderEpisodeCache(self.database)
        first = self.client.post("/titles/1/metadata/enrich")
        self.assertEqual(first.status_code, 303)
        before = cache.cache_status("tvdb", "9001")
        self.assertIsNotNone(before)
        assert before is not None

        main.tvdb = WorkflowTVDB(fail=True)
        failed = self.client.post("/titles/1/metadata/enrich")
        self.assertEqual(failed.status_code, 303)
        self.assertIn("preserved for offline reuse", self._message(failed))

        after = cache.cache_status("tvdb", "9001")
        self.assertIsNotNone(after)
        assert after is not None
        self.assertEqual(after["source_signature"], before["source_signature"])
        self.assertEqual(after["episode_count"], before["episode_count"])
        self.assertEqual(after["mapping_count"], before["mapping_count"])


if __name__ == "__main__":
    unittest.main()

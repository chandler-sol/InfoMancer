import os
import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("INFOMANCER_AUTH_MODE", "disabled")

import app.main as main
from app.app_settings import AppSettings
from app.auth import AuthService
from app.db import Database
from app.engagement import EngagementService


class TitleBackgroundActionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        settings = replace(
            main.settings,
            database=Path(self.temporary.name) / "title-actions.db",
            auth_mode="local",
            cookie_secure="false",
            sandbox=True,
            media_browse_roots=(Path(self.temporary.name),),
        )
        database = Database(settings.database)
        database.initialize()
        self.original = (
            main.db,
            main.settings,
            main.auth_service,
            main.app_settings,
            main.engagement,
            main.media_info_job,
            main.imdb_genre_job,
            main.queue_metadata_refresh,
        )
        main.db, main.settings = database, settings
        main.auth_service = AuthService(database, settings)
        main.app_settings = AppSettings(database, settings.search_url_template)
        main.engagement = EngagementService(database)
        main.engagement.seed_official()
        main.media_info_job = {}
        main.imdb_genre_job = {}
        self.database = database

        self.user = main.auth_service.create_user(
            "title-action-tester",
            "title-actions@example.com",
            "Title Action Tester",
            "x",
            role="librarian",
        )
        with database.connect() as conn:
            root_id = conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES ('/movies','movie','Movies')"
            ).lastrowid
            self.title_id = conn.execute(
                """INSERT INTO titles(
                     root_id,kind,title,year,folder_path,imdb_id,metadata_status
                   ) VALUES (?,?,?,?,?,?,?)""",
                (
                    root_id,
                    "movie",
                    "Background Action Film",
                    2025,
                    "/movies/background-action-film.mkv",
                    "tt1234567",
                    "matched",
                ),
            ).lastrowid
            self.file_id = conn.execute(
                """INSERT INTO files(
                     title_id,path,filename,extension,size_bytes,modified_at,
                     runtime_seconds,width,height,video_codec,audio_codec,
                     audio_channels,bitrate,container,dynamic_range,media_info_at,
                     media_info_error,seen_scan
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    self.title_id,
                    "/movies/background-action-film.mkv",
                    "Background Action Film (2025).mkv",
                    ".mkv",
                    10_000_000_000,
                    1_700_000_000,
                    7200,
                    1920,
                    1080,
                    "h264",
                    "dts",
                    6,
                    24_600_000,
                    "matroska",
                    "SDR",
                    "2099-01-01 00:00:00",
                    "",
                    "test",
                ),
            ).lastrowid

        self.client = TestClient(main.app, follow_redirects=False)
        login = self.client.get("/login")
        token = re.search(r'name="preauth_token" value="([^"]+)', login.text).group(1)
        signed_in = self.client.post(
            "/login",
            data={
                "preauth_token": token,
                "identity": "title-action-tester",
                "password": "x",
                "next": "/",
            },
        )
        self.assertEqual(signed_in.status_code, 303)
        session = main.auth_service.session_from_token(
            self.client.cookies["infomancer_session"]
        )
        self.csrf = session.csrf_token

    def tearDown(self):
        self.client.close()
        (
            main.db,
            main.settings,
            main.auth_service,
            main.app_settings,
            main.engagement,
            main.media_info_job,
            main.imdb_genre_job,
            main.queue_metadata_refresh,
        ) = self.original
        self.temporary.cleanup()

    def test_media_inspection_returns_up_to_date_without_starting_a_job(self):
        response = self.client.post(
            f"/titles/{self.title_id}/media-info",
            data={"csrf_token": self.csrf},
            headers={"Accept": "application/json", "X-InfoMancer-Async": "1"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["started"])
        self.assertTrue(payload["up_to_date"])
        self.assertEqual(payload["detail"], "Media information is up to date.")
        self.assertEqual(main.media_info_job, {})

    def test_media_state_exposes_bitrate_and_compact_quality_facts(self):
        response = self.client.get(f"/api/titles/{self.title_id}/media-info-state")
        self.assertEqual(response.status_code, 200)
        snapshot = response.json()["snapshot"]
        facts = {item["label"]: item["value"] for item in snapshot["facts"]}
        self.assertEqual(facts["Resolution"], "1080p")
        self.assertEqual(facts["Video"], "H264")
        self.assertEqual(facts["Bitrate"], "24.6 Mbps")
        self.assertEqual(facts["Audio"], "DTS · 6ch")
        self.assertIn("24.6 Mbps", snapshot["files"][0]["summary"])

    def test_metadata_refresh_async_contract_does_not_redirect(self):
        def fake_queue(title_ids, user_id, label):
            self.assertEqual(title_ids, [self.title_id])
            self.assertEqual(user_id, self.user.id)
            self.assertIn("Background Action Film", label)
            main.imdb_genre_job.update({
                "status": "running",
                "title_ids": [self.title_id],
                "scope_label": label,
            })
            return "Metadata refresh queued for 1 title."

        main.queue_metadata_refresh = fake_queue
        response = self.client.post(
            f"/titles/{self.title_id}/imdb-refresh",
            data={"csrf_token": self.csrf},
            headers={"Accept": "application/json", "X-InfoMancer-Async": "1"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["started"])
        self.assertEqual(payload["title_id"], self.title_id)
        self.assertEqual(payload["status"], "running")

        state = self.client.get(
            f"/api/titles/{self.title_id}/metadata-refresh-state"
        )
        self.assertEqual(state.status_code, 200)
        self.assertEqual(state.json()["task"]["status"], "running")


if __name__ == "__main__":
    unittest.main()

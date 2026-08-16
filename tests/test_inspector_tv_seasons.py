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


class InspectorTvSeasonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        settings = replace(
            main.settings,
            database=Path(self.temporary.name) / "tv-inspector.db",
            auth_mode="local",
            cookie_secure="false",
            sandbox=True,
            media_browse_roots=(Path(self.temporary.name),),
        )
        database = Database(settings.database)
        database.initialize()
        self.original = (main.db, main.settings, main.auth_service, main.app_settings, main.engagement)
        self.addCleanup(self._restore_globals)
        main.db, main.settings = database, settings
        main.auth_service = AuthService(database, settings)
        main.app_settings = AppSettings(database, settings.search_url_template)
        main.engagement = EngagementService(database)
        self.database = database
        main.auth_service.create_user(
            "tv-inspector", "tv@example.com", "TV Inspector", "x", role="librarian",
        )
        with database.connect() as conn:
            root_id = conn.execute(
                "INSERT INTO roots(path,kind,label,health_status) VALUES (?,?,?,?)",
                (str(Path(self.temporary.name) / "tv"), "tv", "TV", "healthy"),
            ).lastrowid
            self.title_id = conn.execute(
                """INSERT INTO titles(root_id,kind,title,folder_path,metadata_title)
                   VALUES (?,?,?,?,?)""",
                (root_id, "tv", "Season Test", str(Path(self.temporary.name) / "tv" / "Season Test"), "Season Test"),
            ).lastrowid
            for season, episode, name in ((1, 1, "Pilot"), (1, 2, "Second"), (2, 1, "Return")):
                filename = f"Season Test - S{season:02d}E{episode:02d} - {name}.mkv"
                conn.execute(
                    """INSERT INTO files(
                         title_id,path,filename,extension,size_bytes,runtime_seconds,
                         width,height,video_codec,audio_codec,audio_channels,container,
                         dynamic_range,season,episode_start,seen_scan
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        self.title_id,
                        str(Path(self.temporary.name) / "tv" / filename),
                        filename, ".mkv", 1_500_000_000, 1320,
                        1920, 1080, "h264", "dts", 6, "matroska", "SDR",
                        season, episode, "test",
                    ),
                )
                conn.execute(
                    """INSERT INTO expected_episodes(title_id,season,episode,name)
                       VALUES (?,?,?,?)""",
                    (self.title_id, season, episode, name),
                )
        self.client = TestClient(main.app, follow_redirects=False)
        self.addCleanup(self.client.close)
        login = self.client.get("/login")
        token = re.search(r'name="preauth_token" value="([^"]+)', login.text).group(1)
        signed_in = self.client.post("/login", data={
            "preauth_token": token, "identity": "tv-inspector", "password": "x", "next": "/",
        })
        self.assertEqual(signed_in.status_code, 303)

    def _restore_globals(self):
        main.db, main.settings, main.auth_service, main.app_settings, main.engagement = self.original

    def tearDown(self):
        self.temporary.cleanup()

    def test_tv_inspector_uses_season_shell_instead_of_eager_file_rows(self):
        response = self.client.get(f"/library/inspector/{self.title_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("data-inspector-tv-seasons", response.text)
        self.assertNotIn("Season Test - S01E01 - Pilot.mkv", response.text)

    def test_season_summary_and_episode_payload_are_lazy_api_responses(self):
        summary = self.client.get(f"/api/titles/{self.title_id}/inspector-media")
        self.assertEqual(summary.status_code, 200)
        seasons = summary.json()["seasons"]
        self.assertEqual([(item["label"], item["file_count"]) for item in seasons], [
            ("Season 01", 2), ("Season 02", 1),
        ])

        season = self.client.get(f"/api/titles/{self.title_id}/inspector-media/1")
        self.assertEqual(season.status_code, 200)
        files = season.json()["files"]
        self.assertEqual([item["episode_code"] for item in files], ["S01E01", "S01E02"])
        self.assertEqual(files[0]["episode_name"], "Pilot")
        self.assertEqual(files[0]["resolution_display"], "1920×1080")

    def test_movie_or_unknown_title_does_not_expose_tv_season_api(self):
        self.assertEqual(self.client.get("/api/titles/999999/inspector-media").status_code, 404)


if __name__ == "__main__":
    unittest.main()

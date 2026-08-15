import os
import re
import tempfile
import unittest
from dataclasses import replace
from html import unescape
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("INFOMANCER_AUTH_MODE", "disabled")

import app.main as main
from app.app_settings import AppSettings
from app.auth import AuthService
from app.db import Database
from app.engagement import EngagementService


class WorkspaceInspectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        settings = replace(
            main.settings,
            database=Path(self.temporary.name) / "inspector.db",
            auth_mode="local",
            cookie_secure="false",
            sandbox=True,
            media_browse_roots=(Path(self.temporary.name),),
        )
        database = Database(settings.database)
        database.initialize()
        self.original = (main.db, main.settings, main.auth_service, main.app_settings, main.engagement)
        main.db, main.settings = database, settings
        main.auth_service = AuthService(database, settings)
        main.app_settings = AppSettings(database, settings.search_url_template)
        main.engagement = EngagementService(database)
        main.engagement.seed_official()
        self.database = database
        self.user = main.auth_service.create_user(
            "inspector-tester", "inspector@example.com", "Inspector Tester", "x", role="librarian",
        )
        with database.connect() as conn:
            root_id = conn.execute(
                "INSERT INTO roots(path,kind,label,health_status) VALUES ('/movies','movie','Movies','healthy')"
            ).lastrowid
            self.title_id = conn.execute(
                """INSERT INTO titles(
                     root_id,kind,title,year,folder_path,tmdb_id,metadata_title,metadata_year,
                     metadata_provider,metadata_status,overview,genres,imdb_rating,imdb_votes
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (root_id, "movie", "Inspector Film", 2024, "/movies/inspector-film", "42",
                 "Inspector Film", 2024, "tmdb", "complete", "A useful inspector test.",
                 "Drama,Thriller", 8.2, 12345),
            ).lastrowid
            self.file_id = conn.execute(
                """INSERT INTO files(
                     title_id,path,filename,extension,size_bytes,runtime_seconds,width,height,
                     video_codec,audio_codec,audio_channels,container,dynamic_range,edition_name,
                     version_name,identity_confirmed,version_preferred,seen_scan
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (self.title_id, "/movies/inspector-film/movie.mkv", "movie.mkv", ".mkv",
                 8_000_000_000, 7200, 3840, 2160, "hevc", "eac3", 6, "matroska", "HDR10",
                 "Director's Cut", "4K", 1, 1, "test"),
            ).lastrowid
            self.tag_id = conn.execute(
                "INSERT INTO user_tags(user_id,name) VALUES (?,?)", (self.user.id, "Keep"),
            ).lastrowid
            conn.execute(
                """INSERT INTO mie_findings(
                     fingerprint,rule_key,category,severity,title_id,summary,explanation,recommendation
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                ("w2-test", "w2", "quality", "warning", self.title_id,
                 "Quality deserves review", "Test evidence", "Inspect the file"),
            )
        self.client = TestClient(main.app, follow_redirects=False)
        login = self.client.get("/login")
        token = re.search(r'name="preauth_token" value="([^"]+)', login.text).group(1)
        signed_in = self.client.post("/login", data={
            "preauth_token": token, "identity": "inspector-tester", "password": "x", "next": "/",
        })
        self.assertEqual(signed_in.status_code, 303)
        session = main.auth_service.session_from_token(self.client.cookies["infomancer_session"])
        self.csrf = session.csrf_token

    def tearDown(self):
        self.client.close()
        main.db, main.settings, main.auth_service, main.app_settings, main.engagement = self.original
        self.temporary.cleanup()

    def test_inspector_renders_catalog_health_media_and_metadata(self):
        response = self.client.get(f"/library/inspector/{self.title_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        for expected in (
            "Inspector Film", "Health & attention", "Quality deserves review",
            "3840×2160", "HDR10", "Director's Cut", "TMDB 42", "Drama, Thriller",
        ):
            self.assertIn(expected, unescape(response.text))

    def test_inspector_personal_actions_update_without_redirect(self):
        favorite = self.client.post(
            f"/api/titles/{self.title_id}/favorite", headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(favorite.status_code, 200)
        self.assertTrue(favorite.json()["favorite"])
        tagged = self.client.post(
            f"/api/titles/{self.title_id}/tags/{self.tag_id}", headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(tagged.status_code, 200)
        self.assertTrue(tagged.json()["selected"])
        with self.database.connect() as conn:
            state = conn.execute(
                "SELECT favorite FROM user_title_state WHERE user_id=? AND title_id=?",
                (self.user.id, self.title_id),
            ).fetchone()
            tag = conn.execute(
                "SELECT 1 FROM title_tags WHERE title_id=? AND tag_id=?", (self.title_id, self.tag_id),
            ).fetchone()
        self.assertEqual(state["favorite"], 1)
        self.assertIsNotNone(tag)

    def test_missing_inspector_title_is_404(self):
        self.assertEqual(self.client.get("/library/inspector/999999").status_code, 404)


if __name__ == "__main__":
    unittest.main()

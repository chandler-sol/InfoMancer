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


class FavoriteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        settings = replace(
            main.settings,
            database=Path(self.temporary.name) / "favorites.db",
            auth_mode="local",
            cookie_secure="false",
            sandbox=True,
            media_browse_roots=(Path(self.temporary.name),),
        )
        database = Database(settings.database)
        database.initialize()
        self.original = (
            main.db, main.settings, main.auth_service, main.app_settings,
            main.engagement,
        )
        main.db, main.settings = database, settings
        main.auth_service = AuthService(database, settings)
        main.app_settings = AppSettings(database, settings.search_url_template)
        main.engagement = EngagementService(database)
        main.engagement.seed_official()
        self.database = database
        self.user = main.auth_service.create_user(
            "favorite-tester", "favorite@example.com", "Favorite Tester", "x",
            role="librarian",
        )
        with database.connect() as conn:
            movie_root = conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES ('/movies','movie','Movies')"
            ).lastrowid
            self.movie_id = conn.execute(
                """INSERT INTO titles(root_id,kind,title,year,folder_path)
                   VALUES (?,?,?,?,?)""",
                (movie_root, "movie", "Favorite Film", 2024, "/movies/favorite.mkv"),
            ).lastrowid
            tv_root = conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES ('/tv','tv','TV')"
            ).lastrowid
            self.show_id = conn.execute(
                """INSERT INTO titles(root_id,kind,title,year,folder_path)
                   VALUES (?,?,?,?,?)""",
                (tv_root, "tv", "Favorite Show", 2023, "/tv/favorite-show"),
            ).lastrowid
            self.file_id = conn.execute(
                """INSERT INTO files(
                     title_id,path,filename,extension,season,episode_start,episode_end,seen_scan
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    self.show_id, "/tv/favorite-show/S01E01-E02.mkv",
                    "S01E01-E02.mkv", ".mkv", 1, 1, 2, "test",
                ),
            ).lastrowid
            self.episode_ids = [
                conn.execute(
                    """INSERT INTO expected_episodes(
                         title_id,tvdb_episode_id,season,episode,name
                       ) VALUES (?,?,?,?,?)""",
                    (self.show_id, 9000 + number, 1, number, name),
                ).lastrowid
                for number, name in ((1, "First Favorite"), (2, "Second Favorite"))
            ]
        self.client = TestClient(main.app, follow_redirects=False)
        login = self.client.get("/login")
        token = re.search(
            r'name="preauth_token" value="([^"]+)', login.text
        ).group(1)
        signed_in = self.client.post("/login", data={
            "preauth_token": token,
            "identity": "favorite-tester",
            "password": "x",
            "next": "/",
        })
        self.assertEqual(signed_in.status_code, 303)
        session = main.auth_service.session_from_token(
            self.client.cookies["infomancer_session"]
        )
        self.csrf = session.csrf_token

    def tearDown(self):
        self.client.close()
        (
            main.db, main.settings, main.auth_service, main.app_settings,
            main.engagement,
        ) = self.original
        self.temporary.cleanup()

    def test_titles_and_individual_episodes_share_favorites_destination(self):
        title_saved = self.client.post(
            f"/titles/{self.movie_id}/favorite",
            data={"csrf_token": self.csrf, "return_to": "/favorites"},
        )
        self.assertEqual(title_saved.status_code, 303)
        confirmation = self.client.get(title_saved.headers["location"])
        self.assertIn(
            '"Favorite Film" has been added to favorites.',
            unescape(confirmation.text),
        )
        chooser = self.client.get(f"/files/{self.file_id}/favorite")
        self.assertIn("First Favorite", chooser.text)
        self.assertIn("Why is this a favorite?", chooser.text)

        saved = self.client.post(
            f"/files/{self.file_id}/favorite",
            data={
                "csrf_token": self.csrf,
                "selected": [str(self.episode_ids[1])],
                f"note_{self.episode_ids[1]}": "The ending always gets me.",
            },
        )
        self.assertEqual(saved.status_code, 303)
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT expected_episode_id,note FROM user_episode_favorites
                   WHERE user_id=?""",
                (self.user.id,),
            ).fetchall()
        self.assertEqual(
            [(row["expected_episode_id"], row["note"]) for row in rows],
            [(self.episode_ids[1], "The ending always gets me.")],
        )
        favorites = self.client.get("/favorites")
        self.assertIn("Favorite Film", favorites.text)
        self.assertIn('class="cover-card"', favorites.text)
        self.assertIn('class="cover-favorite-button active"', favorites.text)
        self.assertIn("Manage Collections", favorites.text)
        self.assertIn("Second Favorite", favorites.text)
        self.assertIn("The ending always gets me.", favorites.text)
        self.assertNotIn("First Favorite", favorites.text)
        home = self.client.get("/")
        self.assertIn('href="/favorites"', home.text)
        self.assertIn("2 saved items", home.text)

        detail = self.client.get(f"/titles/{self.movie_id}")
        self.assertLess(detail.text.index("★ Favorite"), detail.text.index("On Disk"))

    def test_search_history_is_saved_for_the_account_and_can_be_cleared(self):
        self.client.get("/library?q=David+Krumholtz&record_search=1")
        self.client.get("/library?q=Favorite+Film&record_search=1")
        history = self.client.get("/api/search-history").json()["history"]
        self.assertEqual(
            [item["query"] for item in history],
            ["Favorite Film", "David Krumholtz"],
        )
        cleared = self.client.post(
            "/api/search-history/clear",
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(self.client.get("/api/search-history").json()["history"], [])

    def test_account_control_is_a_single_avatar(self):
        page = self.client.get("/favorites")
        self.assertIn("Open account menu for Favorite Tester", page.text)
        self.assertNotIn('class="account-name"', page.text)


if __name__ == "__main__":
    unittest.main()

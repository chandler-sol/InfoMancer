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
from app.event_log import EventLog


class BulkOrganizeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        settings = replace(
            main.settings,
            database=Path(self.temporary.name) / "bulk-organize.db",
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
            main.event_log,
        )
        main.db, main.settings = database, settings
        main.auth_service = AuthService(database, settings)
        main.app_settings = AppSettings(database, settings.search_url_template)
        main.engagement = EngagementService(database)
        main.engagement.seed_official()
        # Use the same test database for activity logging. This is important for the
        # regression: the old bulk organizer tried to write an event through this
        # second connection before its collection transaction had committed.
        main.event_log = EventLog(database)
        self.database = database
        self.user = main.auth_service.create_user(
            "bulk-organize-tester",
            "bulk-organize@example.com",
            "Bulk Organize Tester",
            "x",
            role="librarian",
        )
        with database.connect() as conn:
            root_id = conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES ('/movies','movie','Movies')"
            ).lastrowid
            self.title_ids = [
                conn.execute(
                    """INSERT INTO titles(root_id,kind,title,year,folder_path)
                       VALUES (?,?,?,?,?)""",
                    (root_id, "movie", title, year, f"/movies/{slug}.mkv"),
                ).lastrowid
                for title, year, slug in (
                    ("The Avengers", 2012, "avengers"),
                    ("Avengers: Age of Ultron", 2015, "age-of-ultron"),
                    ("Avengers: Infinity War", 2018, "infinity-war"),
                    ("Avengers: Endgame", 2019, "endgame"),
                )
            ]
            self.collection_id = conn.execute(
                """INSERT INTO collections(name,description,created_by,collection_type)
                   VALUES (?,?,?,'manual')""",
                ("Marvel Cinematic Universe", "", self.user.id),
            ).lastrowid

        self.client = TestClient(main.app, follow_redirects=False)
        login = self.client.get("/login")
        token = re.search(r'name="preauth_token" value="([^"]+)', login.text).group(1)
        signed_in = self.client.post("/login", data={
            "preauth_token": token,
            "identity": "bulk-organize-tester",
            "password": "x",
            "next": "/movies",
        })
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
            main.event_log,
        ) = self.original
        self.temporary.cleanup()

    def test_bulk_organize_adds_selected_titles_to_collection_and_returns(self):
        response = self.client.post(
            "/titles/organize-bulk",
            data={
                "selected": [str(title_id) for title_id in self.title_ids],
                "apply": "1",
                "selected_collections": [str(self.collection_id)],
                "csrf_token": self.csrf,
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["location"].startswith("/library?message="))

        with self.database.connect() as conn:
            members = conn.execute(
                """SELECT title_id FROM collection_titles
                   WHERE collection_id=? ORDER BY position""",
                (self.collection_id,),
            ).fetchall()
            event = conn.execute(
                """SELECT message FROM event_logs
                   WHERE user_id=? AND category='library'
                   ORDER BY id DESC LIMIT 1""",
                (self.user.id,),
            ).fetchone()
        self.assertEqual(
            [row["title_id"] for row in members],
            self.title_ids,
        )
        self.assertIsNotNone(event)
        self.assertIn("Organization saved for 4 selected titles", event["message"])


if __name__ == "__main__":
    unittest.main()

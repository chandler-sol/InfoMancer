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


class CollectionDetailManagementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        settings = replace(
            main.settings,
            database=Path(self.temporary.name) / "collection-detail.db",
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
        main.event_log = EventLog(database)
        self.database = database
        self.user = main.auth_service.create_user(
            "collection-tester",
            "collection@example.com",
            "Collection Tester",
            "x",
            role="librarian",
        )

        with database.connect() as conn:
            root_id = conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES ('/movies','movie','Movies')"
            ).lastrowid
            self.iron_ids = []
            for number, year in ((1, 2008), (2, 2010), (3, 2013)):
                title = "Iron Man" if number == 1 else f"Iron Man {number}"
                self.iron_ids.append(conn.execute(
                    """INSERT INTO titles(root_id,kind,title,year,folder_path)
                       VALUES (?,?,?,?,?)""",
                    (root_id, "movie", title, year, f"/movies/iron-man-{number}.mkv"),
                ).lastrowid)
            self.ant_man_id = conn.execute(
                """INSERT INTO titles(root_id,kind,title,year,folder_path)
                   VALUES (?,?,?,?,?)""",
                (root_id, "movie", "Ant-Man", 2015, "/movies/ant-man.mkv"),
            ).lastrowid
            self.collection_id = conn.execute(
                """INSERT INTO collections(name,description,created_by,collection_type)
                   VALUES (?,?,?,'manual')""",
                ("Marvel Cinematic Universe", "", self.user.id),
            ).lastrowid
            conn.executemany(
                """INSERT INTO collection_titles(collection_id,title_id,position)
                   VALUES (?,?,?)""",
                [
                    (self.collection_id, title_id, position)
                    for position, title_id in enumerate(self.iron_ids)
                ],
            )

        self.client = TestClient(main.app, follow_redirects=False)
        login = self.client.get("/login")
        token = re.search(r'name="preauth_token" value="([^"]+)', login.text).group(1)
        signed_in = self.client.post("/login", data={
            "preauth_token": token,
            "identity": "collection-tester",
            "password": "x",
            "next": f"/collections/{self.collection_id}",
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

    def test_collection_page_uses_compact_management_dialogs_and_reorder_controls(self):
        response = self.client.get(f"/collections/{self.collection_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("manual order", response.text)
        self.assertNotIn("shown only on the Collections page", response.text)
        self.assertIn('id="collection-add-dialog"', response.text)
        self.assertIn('id="collection-edit-dialog"', response.text)
        self.assertIn("data-collection-reorder-toggle", response.text)
        self.assertIn("Reorder collection", response.text)
        self.assertIn("collection-danger-zone", response.text)
        self.assertIn("data-collection-cover-size", response.text)
        self.assertIn(
            f'data-collection-item="title:{self.iron_ids[0]}"',
            response.text,
        )

    def test_collection_search_ignores_punctuation(self):
        response = self.client.get(
            f"/api/collections/{self.collection_id}/search",
            params={"q": "ant man"},
            headers={"Accept": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["results"][0]["id"], self.ant_man_id)
        self.assertEqual(data["results"][0]["display_title"], "Ant-Man")

    def test_collection_reorder_persists_exact_manual_order(self):
        expected = [self.iron_ids[2], self.iron_ids[0], self.iron_ids[1]]
        response = self.client.post(
            f"/collections/{self.collection_id}/reorder",
            data={
                "order": [f"title:{title_id}" for title_id in expected],
                "csrf_token": self.csrf,
            },
            headers={"X-CSRF-Token": self.csrf, "Accept": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

        with self.database.connect() as conn:
            ordered = conn.execute(
                """SELECT title_id FROM collection_titles
                   WHERE collection_id=? ORDER BY position""",
                (self.collection_id,),
            ).fetchall()
            event = conn.execute(
                """SELECT message FROM event_logs
                   WHERE category='library' AND user_id=?
                   ORDER BY id DESC LIMIT 1""",
                (self.user.id,),
            ).fetchone()
        self.assertEqual([row["title_id"] for row in ordered], expected)
        self.assertIsNotNone(event)
        self.assertIn("Collection order updated", event["message"])


if __name__ == "__main__":
    unittest.main()

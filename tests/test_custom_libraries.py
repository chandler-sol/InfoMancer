import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from app.db import Database
from app.request_security import LOCAL_CSRF_COOKIE


class CustomLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            root = conn.execute("INSERT INTO roots(path,kind,label) VALUES ('/movies','movie','Movies')").lastrowid
            self.title_id = conn.execute("INSERT INTO titles(root_id,kind,title,year,folder_path) VALUES (?,'movie','Library Film',2024,'/movies/film')", (root,)).lastrowid
        self.original = main.db
        main.db = self.database
        self.auth_patch = patch.object(
            main, "settings", replace(main.settings, auth_mode="disabled")
        )
        self.auth_patch.start()
        self.client = TestClient(main.app)
        self.client.get("/")
        csrf_token = self.client.cookies.get(LOCAL_CSRF_COOKIE)
        self.assertTrue(csrf_token)
        self.client.headers.update({"X-CSRF-Token": csrf_token})

    def tearDown(self):
        self.client.close()
        self.auth_patch.stop()
        main.db = self.original
        self.temporary.cleanup()

    def test_title_can_join_multiple_libraries_and_create_one_inline(self):
        first = self.client.post("/libraries", data={"name": "Awards", "library_kind": "movie"}, follow_redirects=False)
        second = self.client.post("/libraries", data={"name": "Family", "library_kind": "mixed"}, follow_redirects=False)
        self.assertEqual((first.status_code, second.status_code), (303, 303))
        with self.database.connect() as conn:
            ids = [row["id"] for row in conn.execute("SELECT id FROM custom_libraries ORDER BY id")]
        saved = self.client.post(f"/titles/{self.title_id}/libraries", data={"selected": [str(value) for value in ids], "new_library_name": "Favorites Shelf"}, follow_redirects=False)
        self.assertEqual(saved.status_code, 303)
        with self.database.connect() as conn:
            memberships = conn.execute("SELECT COUNT(*) FROM custom_library_titles WHERE title_id=?", (self.title_id,)).fetchone()[0]
        self.assertEqual(memberships, 3)
        chooser = self.client.get(f"/titles/{self.title_id}/libraries")
        self.assertIn("Add to Libraries", chooser.text)
        self.assertIn("Create a New Library", chooser.text)
        detail = self.client.get(f"/libraries/{ids[0]}")
        self.assertIn('class="cover-card"', detail.text)
        self.assertIn("Library Film", detail.text)

    def test_library_kind_rejects_incompatible_title_membership(self):
        self.client.post("/libraries", data={"name": "Shows Only", "library_kind": "tv"})
        with self.database.connect() as conn:
            library_id = conn.execute("SELECT id FROM custom_libraries").fetchone()[0]
        self.client.post(f"/titles/{self.title_id}/libraries", data={"selected": str(library_id)})
        with self.database.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM custom_library_titles").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
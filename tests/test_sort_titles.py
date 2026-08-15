import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from app.db import Database


class SortTitleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO users(id,username,display_name,role,password_hash)
                   VALUES (0,'disabled','Disabled','librarian','')"""
            )
            root = conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES ('/movies','movie','Movies')"
            ).lastrowid
            self.fast_two = conn.execute(
                """INSERT INTO titles(root_id,kind,title,folder_path)
                   VALUES (?,'movie','2 Fast 2 Furious','/movies/fast-two')""", (root,),
            ).lastrowid
            conn.execute(
                """INSERT INTO titles(root_id,kind,title,folder_path)
                   VALUES (?,'movie','The Batman','/movies/batman')""", (root,),
            )
            fast_four = conn.execute(
                """INSERT INTO titles(root_id,kind,title,folder_path)
                   VALUES (?,'movie','Fast & Furious','/movies/fast-four')""", (root,),
            ).lastrowid
            conn.executemany(
                "INSERT INTO user_title_state(user_id,title_id,sort_title) VALUES (0,?,?)",
                ((fast_four, "Fast 01"), (self.fast_two, "Fast 02")),
            )
        self.original_db = main.db
        main.db = self.database
        self.auth_patch = patch.object(main, "settings", replace(main.settings, auth_mode="disabled"))
        self.auth_patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        self.auth_patch.stop()
        main.db = self.original_db
        self.temporary.cleanup()

    def test_alphabetical_uses_sort_title_and_ignores_leading_articles(self):
        page = self.client.get("/movies")
        self.assertLess(page.text.index("The Batman"), page.text.index("2 Fast 2 Furious"))
        self.assertLess(page.text.index("Fast &amp; Furious"), page.text.index("2 Fast 2 Furious"))
        self.assertIn('id="letter-B"', page.text)
        self.assertNotIn('id="letter-T"', page.text)
        self.assertIn("The Batman", self.client.get("/movies?letter=B").text)
        self.assertNotIn("The Batman", self.client.get("/movies?letter=T").text)

    def test_bulk_prefix_applies_click_order_with_padded_numbers(self):
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO users(id,username,display_name,role,password_hash)
                   VALUES (1,'member','Member','member','')"""
            )
            ids = [row["id"] for row in conn.execute("SELECT id FROM titles ORDER BY id")]
        request = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=1)))
        with patch.object(main, "record_event"):
            response = main.apply_sort_titles(
                request, selected=[ids[2], ids[0], ids[1]], sequence_number=[1, 2, 3],
                sequence_letter=["", "", ""], number_style="padded",
                prefix="Fast", return_to="/movies",
            )
        self.assertEqual(response.status_code, 303)
        with self.database.connect() as conn:
            values = {
                row["title_id"]: row["sort_title"] for row in conn.execute(
                    "SELECT title_id,sort_title FROM user_title_state WHERE user_id=1"
                )
            }
        self.assertEqual(
            [values[ids[2]], values[ids[0]], values[ids[1]]],
            ["Fast 01", "Fast 02", "Fast 03"],
        )

    def test_bulk_prefix_supports_shared_numbers_with_optional_letters(self):
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO users(id,username,display_name,role,password_hash)
                   VALUES (1,'member','Member','member','')"""
            )
            ids = [row["id"] for row in conn.execute("SELECT id FROM titles ORDER BY id LIMIT 2")]
        request = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=1)))
        with patch.object(main, "record_event"):
            main.apply_sort_titles(
                request, selected=ids, sequence_number=[7, 7],
                sequence_letter=["a", "b"], number_style="plain",
                prefix="Harry", return_to="/movies",
            )
        with self.database.connect() as conn:
            values = [row["sort_title"] for row in conn.execute(
                "SELECT sort_title FROM user_title_state WHERE user_id=1 ORDER BY title_id"
            )]
        self.assertEqual(values, ["Harry 7a", "Harry 7b"])


if __name__ == "__main__":
    unittest.main()

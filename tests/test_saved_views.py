from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.saved_views import SavedViewError, SavedViewService


class SavedViewServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            self.user_one = int(conn.execute(
                """INSERT INTO users(username,display_name,role,password_hash)
                   VALUES ('one','One','member','test')"""
            ).lastrowid)
            self.user_two = int(conn.execute(
                """INSERT INTO users(username,display_name,role,password_hash)
                   VALUES ('two','Two','member','test')"""
            ).lastrowid)
        self.views = SavedViewService(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def test_saved_view_normalizes_filters_and_never_keeps_arbitrary_parameters(self):
        path, query = self.views.normalize_target(
            "/movies",
            "q=Alien&sort=rating&root=7&gaps=missing&record_search=1&next=https%3A%2F%2Fevil.test",
        )
        self.assertEqual(path, "/movies")
        self.assertEqual(query, "q=Alien&root=7&sort=rating")
        fallback_path, fallback_query = self.views.normalize_target(
            "https://evil.test", "sort=not-real&letter=A&favorite=favorites"
        )
        self.assertEqual(fallback_path, "/library")
        self.assertEqual(fallback_query, "letter=A&favorite=favorites")

    def test_views_are_private_to_the_account_and_same_name_updates(self):
        first, created = self.views.save(
            self.user_one, "Needs matching", "/movies", "match=unmatched", pinned=True
        )
        self.assertTrue(created)
        self.assertTrue(first["pinned"])
        updated, created = self.views.save(
            self.user_one, "Needs matching", "/shows", "gaps=missing", pinned=False
        )
        self.assertFalse(created)
        self.assertEqual(updated["href"], "/shows?gaps=missing")
        self.assertFalse(updated["pinned"])
        self.assertEqual(len(self.views.list_for_user(self.user_one)), 1)
        self.assertEqual(self.views.list_for_user(self.user_two), [])
        with self.assertRaises(SavedViewError):
            self.views.delete(self.user_two, first["id"])

    def test_pin_limit_and_rename_are_enforced_per_user(self):
        for index in range(self.views.MAX_PINNED):
            self.views.save(
                self.user_one, f"Pinned {index}", "/library", f"q={index}", pinned=True
            )
        with self.assertRaisesRegex(SavedViewError, "Pin up to"):
            self.views.save(self.user_one, "One too many", "/library", "q=extra", pinned=True)
        item = self.views.list_for_user(self.user_one)[0]
        renamed = self.views.rename(self.user_one, item["id"], "Renamed view")
        self.assertEqual(renamed["name"], "Renamed view")
        self.assertEqual(len(self.views.list_for_user(self.user_one, pinned_only=True)), 8)


class SavedViewUiContractTests(unittest.TestCase):
    def test_library_and_dashboard_surface_saved_views(self):
        root = Path(__file__).resolve().parents[1]
        library = (root / "app/templates/library.html").read_text(encoding="utf-8")
        dashboard = (root / "app/templates/dashboard.html").read_text(encoding="utf-8")
        script = (root / "app/static/library-saved-views.js").read_text(encoding="utf-8")
        styles = (root / "app/static/library-saved-views.css").read_text(encoding="utf-8")
        self.assertIn('action="/library/views"', library)
        self.assertIn("Pin to Library and Dashboard", library)
        self.assertIn("saved-view-chip", library)
        self.assertIn("home-saved-view-grid", dashboard)
        self.assertIn("bar.querySelectorAll('.saved-view-chip')", script)
        self.assertIn("chip.classList.add('catalog-saved-view-pin')", script)
        self.assertIn(".catalog-tabs .catalog-saved-view-pin", styles)
        self.assertLess(script.index("tabs.append(chip)"), script.index("tabs.append(manager)"))
        self.assertLess(script.index("tabs.append(chip)"), script.index("bar.hidden = true"))


if __name__ == "__main__":
    unittest.main()

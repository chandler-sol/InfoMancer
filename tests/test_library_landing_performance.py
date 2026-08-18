from pathlib import Path
import tempfile
import unittest

from app.db import Database
from app.routes.library_cached import _cacheable_landing, _library_signature


ROOT = Path(__file__).resolve().parents[1]


class LibraryLandingPerformanceTests(unittest.TestCase):
    def test_default_library_landing_is_cacheable_but_filtered_views_are_not(self):
        base = dict(
            q="", kind="all", letter="", genre="", title_type="", root="",
            person="", person_name="", credit_role="", match="", gaps="",
            favorite="", tag="", sort="title", record_search="",
        )
        self.assertTrue(_cacheable_landing(**base))
        self.assertFalse(_cacheable_landing(**{**base, "q": "Alien"}))
        self.assertFalse(_cacheable_landing(**{**base, "sort": "file_size"}))
        self.assertFalse(_cacheable_landing(**{**base, "record_search": "1"}))

    def test_library_signature_changes_with_catalog_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "library.db")
            database.initialize()
            before = _library_signature(database, 0)
            with database.connect() as conn:
                root_id = conn.execute(
                    "INSERT INTO roots(path,kind,label) VALUES ('/media/movies','movie','Movies')"
                ).lastrowid
                conn.execute(
                    "INSERT INTO titles(root_id,kind,title,folder_path,discovered_at) "
                    "VALUES (?,?,?,?,'2026-01-01T00:00:00')",
                    (root_id, "movie", "Alien", "/media/movies/Alien"),
                )
            after = _library_signature(database, 0)
            self.assertNotEqual(before, after)

    def test_library_router_and_navigation_use_warm_render_path(self):
        routes = (ROOT / "app/routes/__init__.py").read_text(encoding="utf-8")
        navigation = (ROOT / "app/static/app-navigation.js").read_text(encoding="utf-8")
        cache = (ROOT / "app/routes/library_cached.py").read_text(encoding="utf-8")

        self.assertIn(".library_cached import build_router", routes)
        self.assertIn('fetch("/library"', navigation)
        self.assertIn("navigator.connection?.saveData", navigation)
        self.assertIn('X-InfoMancer-Prefetch', navigation)
        self.assertIn('X-InfoMancer-Library-Render', cache)
        self.assertIn('name="library"', cache)


if __name__ == "__main__":
    unittest.main()

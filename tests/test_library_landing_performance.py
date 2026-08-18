from pathlib import Path
import tempfile
import unittest

from fastapi import APIRouter

from app.db import Database
from app.routes import library_optimized
from app.routes.library_cached import (
    _cacheable_landing, _library_signature, _trim_library_surface,
)


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

    def test_preferred_view_keeps_only_one_large_library_surface(self):
        html = b'''<main>
<section class="cover-library" id="cover-library" aria-label="Library covers"><article>COVER</article></section>
<form method="post" action="/movies/bulk-match/analyze" id="library-bulk-form">
<section class="panel table-wrap library-table" data-library-kind="all"><table><thead><tr><th>Title</th></tr></thead><tbody><tr><td>LIST</td></tr></tbody></table></section>
</form></main>'''
        list_html = _trim_library_surface(html, "list").decode()
        cover_html = _trim_library_surface(html, "covers").decode()

        self.assertIn("LIST", list_html)
        self.assertNotIn("COVER", list_html)
        self.assertIn('data-library-surface-placeholder="covers"', list_html)
        self.assertIn("COVER", cover_html)
        self.assertNotIn("LIST", cover_html)
        self.assertIn('data-library-surface-placeholder="list"', cover_html)

    def test_default_landing_scopes_expensive_aggregates_to_visible_candidates(self):
        cache = (ROOT / "app/routes/library_cached.py").read_text(encoding="utf-8")

        self.assertIn("WITH candidates AS", cache)
        self.assertIn("LIMIT 1000", cache)
        self.assertIn("FROM files f JOIN candidates c ON c.id=f.title_id", cache)
        self.assertIn("FROM expected_episodes e JOIN candidates c ON c.id=e.title_id", cache)
        self.assertIn('X-InfoMancer-Library-Query', cache)
        self.assertIn('"scoped"', cache)

    def test_optimized_library_preserves_router_handler_bundle_contract(self):
        base_router = APIRouter()

        @base_router.get("/library")
        def original_library():
            return "ok"

        original_builder = library_optimized.build_base_router
        library_optimized.build_base_router = lambda _ctx: (
            base_router,
            {"library": original_library, "sentinel": object()},
        )

        class Context:
            def live(self, name):
                if name == "display_title_type":
                    return lambda value: value
                return object()

        try:
            router, handlers = library_optimized.build_router(Context())
        finally:
            library_optimized.build_base_router = original_builder

        self.assertIs(router, base_router)
        self.assertIn("library", handlers)
        self.assertIn("sentinel", handlers)
        self.assertIsNot(handlers["library"], original_library)
        self.assertTrue(any(getattr(route, "path", "") == "/library" for route in router.routes))

    def test_library_router_and_navigation_use_warm_render_path(self):
        routes = (ROOT / "app/routes/__init__.py").read_text(encoding="utf-8")
        adapter = (ROOT / "app/routes/library_optimized.py").read_text(encoding="utf-8")
        loader = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")
        navigation = (ROOT / "app/static/app-navigation.js").read_text(encoding="utf-8")
        lazy = (ROOT / "app/static/library-surface-lazy.js").read_text(encoding="utf-8")
        cache = (ROOT / "app/routes/library_cached.py").read_text(encoding="utf-8")

        self.assertIn(".library_optimized import build_router", routes)
        self.assertIn("router, handlers = build_base_router(ctx)", adapter)
        self.assertIn("return router, updated_handlers", adapter)
        self.assertIn('fetch("/library"', navigation)
        self.assertIn("navigator.connection?.saveData", navigation)
        self.assertIn('infomancer_library_view', navigation)
        self.assertIn('X-InfoMancer-Prefetch', navigation)
        self.assertIn('library-surface-lazy.js', loader)
        self.assertIn('library-performance.css', loader)
        self.assertIn('X-InfoMancer-Library-View', lazy)
        self.assertIn('librarySurfacePlaceholder', lazy)
        self.assertIn('setViewCookie(view);', lazy)
        self.assertIn('hydrateSurface(currentView());', lazy)
        self.assertIn('X-InfoMancer-Library-Render', cache)
        self.assertIn('X-InfoMancer-Library-Surface', cache)
        self.assertIn('name="library"', adapter)


if __name__ == "__main__":
    unittest.main()

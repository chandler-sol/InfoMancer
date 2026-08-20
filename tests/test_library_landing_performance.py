from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.db import Database
from app.routes import library_optimized
from app.routes.library_cached import _trim_library_surface
from app.routes.library_landing_optimized import fast_landing_response
from app.routes.library_optimized import (
    _cacheable_landing, _live_results_fragment, _warm_response,
)
from app.routes.library_search_optimized import eligible_search, search_response
from app.routes.library_signature_optimized import library_signature


ROOT = Path(__file__).resolve().parents[1]


class LibraryLandingPerformanceTests(unittest.TestCase):
    def test_default_library_landings_are_cacheable_but_filtered_views_are_not(self):
        base = dict(
            q="", kind="all", letter="", genre="", title_type="", root="",
            person="", person_name="", credit_role="", match="", gaps="",
            favorite="", tag="", sort="title", record_search="",
        )
        self.assertTrue(_cacheable_landing(**base))
        self.assertTrue(_cacheable_landing(**{**base, "kind": "movie"}))
        self.assertTrue(_cacheable_landing(**{**base, "kind": "tv"}))
        self.assertFalse(_cacheable_landing(**{**base, "q": "Alien"}))
        self.assertFalse(_cacheable_landing(**{**base, "sort": "file_size"}))
        self.assertFalse(_cacheable_landing(**{**base, "record_search": "1"}))

    def test_library_signature_changes_with_catalog_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "library.db")
            database.initialize()
            before = library_signature(database, 0)
            with database.connect() as conn:
                root_id = conn.execute(
                    "INSERT INTO roots(path,kind,label) VALUES ('/media/movies','movie','Movies')"
                ).lastrowid
                conn.execute(
                    "INSERT INTO titles(root_id,kind,title,folder_path,discovered_at) "
                    "VALUES (?,?,?,?,'2026-01-01T00:00:00')",
                    (root_id, "movie", "Alien", "/media/movies/Alien"),
                )
            after = library_signature(database, 0)
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

    def test_live_result_fragment_drops_application_chrome(self):
        body = b'''<!doctype html><html><body><header>EXPENSIVE CHROME</header>
<section class="cover-library" id="cover-library" aria-label="Library covers"><article>COVER</article></section>
<aside>MORE CHROME</aside>
<section class="panel table-wrap library-table" data-library-kind="all"><table><tbody><tr><td>LIST</td></tr></tbody></table></section>
<footer>EXPENSIVE FOOTER</footer></body></html>'''
        fragment = _live_results_fragment(body)
        self.assertIsNotNone(fragment)
        text = fragment.decode("utf-8")
        self.assertIn("COVER", text)
        self.assertIn("LIST", text)
        self.assertNotIn("EXPENSIVE CHROME", text)
        self.assertNotIn("MORE CHROME", text)
        self.assertNotIn("EXPENSIVE FOOTER", text)

    def test_default_landing_scopes_expensive_aggregates_to_visible_candidates(self):
        source = (ROOT / "app/routes/library_landing_optimized.py").read_text(encoding="utf-8")
        self.assertIn("WITH candidates AS", source)
        self.assertIn("LIMIT 1000", source)
        self.assertIn("FROM files f JOIN candidates c ON c.id=f.title_id", source)
        self.assertIn("FROM expected_episodes e JOIN candidates c ON c.id=e.title_id", source)
        self.assertIn("WHERE t.kind=?", source)

    def test_movie_and_tv_landings_filter_candidates_before_aggregation(self):
        class Templates:
            def __init__(self):
                self.context = None

            def TemplateResponse(self, request, template, context):
                self.context = context
                return Response("<main>landing</main>", media_type="text/html")

        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "kind-landings.db")
            database.initialize()
            with database.connect() as conn:
                movie_root = conn.execute(
                    "INSERT INTO roots(path,kind,label) VALUES ('/movies','movie','Movies')"
                ).lastrowid
                tv_root = conn.execute(
                    "INSERT INTO roots(path,kind,label) VALUES ('/tv','tv','TV')"
                ).lastrowid
                conn.execute(
                    "INSERT INTO titles(root_id,kind,title,folder_path) VALUES (?,?,?,?)",
                    (movie_root, "movie", "Alien", "/movies/Alien"),
                )
                conn.execute(
                    "INSERT INTO titles(root_id,kind,title,folder_path) VALUES (?,?,?,?)",
                    (tv_root, "tv", "The Expanse", "/tv/The Expanse"),
                )

            request = Request({
                "type": "http", "method": "GET", "scheme": "http",
                "path": "/movies", "raw_path": b"/movies", "query_string": b"",
                "headers": [], "client": ("127.0.0.1", 1000),
                "server": ("test", 80),
            })
            request.state.user = SimpleNamespace(id=0)
            movie_templates = Templates()
            fast_landing_response(
                database, movie_templates, lambda value: value, request, kind="movie",
            )
            self.assertEqual(movie_templates.context["kind"], "movie")
            self.assertEqual(movie_templates.context["current_view_path"], "/movies")
            self.assertEqual([row["kind"] for row in movie_templates.context["rows"]], ["movie"])

            tv_templates = Templates()
            fast_landing_response(
                database, tv_templates, lambda value: value, request, kind="tv",
            )
            self.assertEqual(tv_templates.context["kind"], "tv")
            self.assertEqual(tv_templates.context["current_view_path"], "/shows")
            self.assertEqual([row["kind"] for row in tv_templates.context["rows"]], ["tv"])

    def test_common_text_search_uses_candidate_first_plan(self):
        base = dict(
            q="Alien", kind="all", letter="", genre="", title_type="", root="",
            person="", person_name="", credit_role="", match="", gaps="",
            favorite="", tag="", sort="title", record_search="",
        )
        self.assertTrue(eligible_search(**base))
        self.assertFalse(eligible_search(**{**base, "genre": "Sci-Fi"}))
        self.assertFalse(eligible_search(**{**base, "sort": "rating"}))

        source = (ROOT / "app/routes/library_search_optimized.py").read_text(encoding="utf-8")
        self.assertIn("WITH raw_candidates(id) AS", source)
        self.assertIn("), candidates AS (", source)
        self.assertIn("LIMIT 1000", source)
        self.assertIn("FROM files f JOIN candidates c ON c.id=f.title_id", source)
        self.assertIn("FROM expected_episodes e JOIN candidates c ON c.id=e.title_id", source)
        self.assertIn('"candidate-search"', source)

    def test_candidate_search_matches_filename_without_whole_catalog_aggregation(self):
        class Templates:
            def __init__(self):
                self.context = None

            def TemplateResponse(self, request, template, context):
                self.context = context
                return Response("<main>search</main>", media_type="text/html")

        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "search.db")
            database.initialize()
            with database.connect() as conn:
                root_id = conn.execute(
                    "INSERT INTO roots(path,kind,label) VALUES ('/media/movies','movie','Movies')"
                ).lastrowid
                title_id = conn.execute(
                    "INSERT INTO titles(root_id,kind,title,folder_path) VALUES (?,?,?,?)",
                    (root_id, "movie", "Prometheus", "/media/movies/Prometheus"),
                ).lastrowid
                conn.execute(
                    """INSERT INTO files(
                         title_id,path,filename,extension,size_bytes,seen_scan
                       ) VALUES (?,?,?,?,?,?)""",
                    (
                        title_id, "/media/movies/Prometheus/Alien-reference.mkv",
                        "Alien-reference.mkv", ".mkv", 100, "test-scan",
                    ),
                )

            request = Request({
                "type": "http", "method": "GET", "scheme": "http",
                "path": "/library", "raw_path": b"/library",
                "query_string": b"q=Alien", "headers": [],
                "client": ("127.0.0.1", 1000), "server": ("test", 80),
            })
            request.state.user = SimpleNamespace(id=0)
            templates = Templates()
            response = search_response(
                database, templates, lambda value: value, lambda *_args: [], request,
                q="Alien", kind="all", view="",
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["X-InfoMancer-Library-Query"], "candidate-search")
            self.assertEqual(len(templates.context["rows"]), 1)
            self.assertEqual(templates.context["rows"][0]["id"], title_id)

    def test_live_candidate_search_skips_full_page_option_and_context_queries(self):
        class RenderedTemplate:
            def __init__(self, owner):
                self.owner = owner

            def render(self, context):
                self.owner.context = context
                return '<section class="cover-library" id="cover-library"></section>'

        class Environment:
            def __init__(self, owner):
                self.owner = owner

            def get_template(self, name):
                self.owner.template_name = name
                return RenderedTemplate(self.owner)

        class Templates:
            def __init__(self):
                self.context = None
                self.template_name = ""
                self.full_page_calls = 0
                self.env = Environment(self)

            def TemplateResponse(self, request, template, context):
                self.full_page_calls += 1
                return Response("unexpected full page", media_type="text/html")

        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "partial-search.db")
            database.initialize()
            with database.connect() as conn:
                root_id = conn.execute(
                    "INSERT INTO roots(path,kind,label) VALUES ('/media/movies','movie','Movies')"
                ).lastrowid
                title_id = conn.execute(
                    "INSERT INTO titles(root_id,kind,title,folder_path) VALUES (?,?,?,?)",
                    (root_id, "movie", "Alien", "/media/movies/Alien"),
                ).lastrowid
                conn.execute(
                    """INSERT INTO files(
                         title_id,path,filename,extension,size_bytes,seen_scan
                       ) VALUES (?,?,?,?,?,?)""",
                    (
                        title_id, "/media/movies/Alien/Alien.mkv",
                        "Alien.mkv", ".mkv", 100, "test-scan",
                    ),
                )

            original_connect = database.connect
            connection_count = 0

            @contextmanager
            def counted_connect():
                nonlocal connection_count
                connection_count += 1
                with original_connect() as conn:
                    yield conn

            database.connect = counted_connect
            request = Request({
                "type": "http", "method": "GET", "scheme": "http",
                "path": "/library", "raw_path": b"/library",
                "query_string": b"q=Alien", "headers": [
                    (b"x-infomancer-partial", b"library"),
                ],
                "client": ("127.0.0.1", 1000), "server": ("test", 80),
            })
            request.state.user = SimpleNamespace(id=0)
            templates = Templates()
            response = search_response(
                database, templates, lambda value: value, lambda *_args: [], request,
                q="Alien", kind="all", view="list",
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["X-InfoMancer-Partial"], "library")
            self.assertEqual(response.headers["X-InfoMancer-Library-Query"], "candidate-search")
            self.assertEqual(connection_count, 1)
            self.assertEqual(templates.full_page_calls, 0)
            self.assertEqual(templates.template_name, "library_results.html")
            self.assertEqual(templates.context["view"], "list")
            self.assertEqual(templates.context["rows"][0]["id"], title_id)
            self.assertNotIn("saved_views", templates.context)
            self.assertNotIn("root_options", templates.context)

    def test_library_warm_response_never_carries_rendered_html(self):
        response = _warm_response("hit", "covers")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.body, b"")
        self.assertEqual(response.headers["X-InfoMancer-Library-Render"], "hit")
        self.assertEqual(response.headers["X-InfoMancer-Library-Prefetch"], "warm")
        self.assertEqual(response.headers["X-InfoMancer-Library-Surface"], "covers")

    def test_optimized_library_preserves_router_handler_bundle_contract(self):
        base_router = APIRouter()

        @base_router.get("/library")
        def original_library():
            return "ok"

        @base_router.get("/movies")
        def original_movies():
            return "movies"

        @base_router.get("/shows")
        def original_shows():
            return "shows"

        original_builder = library_optimized.build_base_router
        library_optimized.build_base_router = lambda _ctx: (
            base_router,
            {
                "library": original_library,
                "movies": original_movies,
                "shows": original_shows,
                "sentinel": object(),
            },
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
        self.assertIn("movies", handlers)
        self.assertIn("shows", handlers)
        self.assertIn("sentinel", handlers)
        self.assertIsNot(handlers["library"], original_library)
        self.assertIsNot(handlers["movies"], original_movies)
        self.assertIsNot(handlers["shows"], original_shows)
        paths = [getattr(route, "path", "") for route in router.routes]
        self.assertEqual(paths.count("/library"), 1)
        self.assertEqual(paths.count("/movies"), 1)
        self.assertEqual(paths.count("/shows"), 1)

    def test_library_router_and_navigation_use_warm_render_path(self):
        routes = (ROOT / "app/routes/__init__.py").read_text(encoding="utf-8")
        adapter = (ROOT / "app/routes/library_optimized.py").read_text(encoding="utf-8")
        landing = (ROOT / "app/routes/library_landing_optimized.py").read_text(encoding="utf-8")
        loader = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")
        navigation = (ROOT / "app/static/app-navigation.js").read_text(encoding="utf-8")
        lazy = (ROOT / "app/static/library-surface-lazy.js").read_text(encoding="utf-8")
        cache = (ROOT / "app/routes/library_cached.py").read_text(encoding="utf-8")
        search = (ROOT / "app/routes/library_search_optimized.py").read_text(encoding="utf-8")
        partial = (ROOT / "app/templates/library_results.html").read_text(encoding="utf-8")

        self.assertIn(".library_optimized import build_router", routes)
        self.assertIn("router, handlers = build_base_router(ctx)", adapter)
        self.assertIn("fast_landing_response", adapter)
        self.assertIn('@router.get("/movies"', adapter)
        self.assertIn('@router.get("/shows"', adapter)
        self.assertIn("eligible_search", adapter)
        self.assertIn("search_response", adapter)
        self.assertIn("return router, updated_handlers", adapter)
        self.assertIn("WITH candidates AS", landing)
        self.assertIn('fetch("/library"', navigation)
        self.assertIn("navigator.connection?.saveData", navigation)
        self.assertIn('infomancer_library_view', navigation)
        self.assertIn('X-InfoMancer-Prefetch', navigation)
        self.assertIn("response.body?.cancel()", navigation)
        self.assertNotIn("response.arrayBuffer()", navigation)
        self.assertIn('request.headers.get("x-infomancer-prefetch"', adapter)
        self.assertIn('request.headers.get("x-infomancer-partial"', adapter)
        self.assertIn('return _warm_response("hit", view)', adapter)
        self.assertIn('return _warm_response("miss", view)', adapter)
        self.assertIn('X-InfoMancer-Partial', adapter)
        self.assertIn('templates.env.get_template("library_results.html")', search)
        self.assertIn("if _live_partial(request):", search)
        self.assertIn("{% if view == 'list' %}", partial)
        self.assertIn("{% if view == 'covers' %}", partial)
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

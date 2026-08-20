from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest

from jinja2 import Environment

from app.db import Database
from app.routes.library_optimized import _cached_landing_response
from app.routes.library_signature_optimized import library_signature


ROOT = Path(__file__).resolve().parents[1]


class LibraryOptimizationContracts(unittest.TestCase):
    def test_cache_signature_uses_one_database_connection_and_tracks_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "signature.db")
            database.initialize()
            original_connect = database.connect
            connection_count = 0

            @contextmanager
            def counted_connect():
                nonlocal connection_count
                connection_count += 1
                with original_connect() as conn:
                    yield conn

            database.connect = counted_connect
            before = library_signature(database, 0)
            self.assertEqual(connection_count, 1)

            with original_connect() as conn:
                root_id = conn.execute(
                    "INSERT INTO roots(path,kind,label) VALUES ('/movies','movie','Movies')"
                ).lastrowid
                conn.execute(
                    "INSERT INTO titles(root_id,kind,title,folder_path) VALUES (?,?,?,?)",
                    (root_id, "movie", "Alien", "/movies/Alien"),
                )

            after = library_signature(database, 0)
            self.assertEqual(connection_count, 2)
            self.assertNotEqual(before, after)

    def test_one_cached_source_can_serve_both_library_views(self):
        source = b'''<!doctype html><html><body>
<section class="cover-library" id="cover-library"><article>COVER</article></section>
<form method="post" action="/movies/bulk-match/analyze" id="library-bulk-form">
<section class="panel table-wrap library-table"><table><tbody><tr><td>LIST</td></tr></tbody></table></section>
</form></body></html>'''
        list_response = _cached_landing_response(source, view="list", render_state="hit")
        cover_response = _cached_landing_response(source, view="covers", render_state="hit")

        self.assertIn(b"LIST", list_response.body)
        self.assertNotIn(b"COVER", list_response.body)
        self.assertIn(b"COVER", cover_response.body)
        self.assertNotIn(b"LIST", cover_response.body)
        self.assertEqual(list_response.headers["X-InfoMancer-Library-Render"], "hit")
        self.assertEqual(cover_response.headers["X-InfoMancer-Library-Render"], "hit")

    def test_lightweight_library_results_template_has_valid_jinja_syntax(self):
        template_source = (ROOT / "app/templates/library_results.html").read_text(
            encoding="utf-8"
        )
        Environment().parse(template_source)

    def test_optimized_adapter_uses_new_signature_and_single_source_cache(self):
        source = (ROOT / "app/routes/library_optimized.py").read_text(encoding="utf-8")
        signature = (ROOT / "app/routes/library_signature_optimized.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("from .library_signature_optimized import library_signature", source)
        self.assertIn("signature = library_signature(db, user_id)", source)
        self.assertIn('source_key = (_session_key(request), request.url.path, kind, "source")', source)
        self.assertIn("cached_source = _cache_get(source_key, signature)", source)
        self.assertIn("_cache_put(source_key, signature, body)", source)
        self.assertIn("SELECT\n                 (SELECT COUNT(*) FROM titles)", signature)


if __name__ == "__main__":
    unittest.main()

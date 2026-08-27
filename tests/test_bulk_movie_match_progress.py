import json
import sqlite3
import threading
import unittest
from pathlib import Path

from fastapi import Response

from app.routes.bulk_match_progress import build_router
from app.routes.context import RouteContext


ROOT = Path(__file__).resolve().parents[1]


class _FakeDatabase:
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return self.connection


class BulkMovieMatchProgressTests(unittest.TestCase):
    def test_progress_endpoint_returns_saved_match_without_raw_provider_blob(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE titles (
                id INTEGER PRIMARY KEY,
                kind TEXT,
                title TEXT,
                metadata_title TEXT
            );
            CREATE TABLE movie_match_suggestions (
                title_id INTEGER PRIMARY KEY,
                candidate_json TEXT,
                confidence_score INTEGER,
                confidence_label TEXT,
                result_count INTEGER,
                exact INTEGER,
                error TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO titles (id,kind,title,metadata_title) VALUES (7,'movie','Alien',NULL)"
        )
        connection.execute(
            """INSERT INTO movie_match_suggestions
               (title_id,candidate_json,confidence_score,confidence_label,result_count,exact,error)
               VALUES (7,?,?,?,?,?,?)""",
            (
                json.dumps(
                    {
                        "tvdb_id": 123,
                        "name": "Alien",
                        "year": "1979",
                        "image_url": "https://example.invalid/poster.jpg",
                        "_possible_match": False,
                        "_search_query": "Alien 1979",
                        "provider_internal": "must not escape",
                    }
                ),
                99,
                "Very high",
                1,
                1,
                "",
            ),
        )
        connection.commit()
        namespace = {
            "db": _FakeDatabase(connection),
            "json": json,
            "movie_match_job": {
                "status": "running",
                "processed": 1,
                "total": 2,
                "matched": 1,
                "errors": 0,
                "title_ids": [7, 8],
            },
            "movie_match_lock": threading.Lock(),
        }
        router, _ = build_router(RouteContext(namespace))
        endpoint = next(
            route.endpoint
            for route in router.routes
            if route.path == "/api/movies/bulk-match/progress"
        )
        response = Response()
        payload = endpoint(response)
        connection.close()

        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(payload["processed"], 1)
        self.assertEqual(len(payload["items"]), 1)
        item = payload["items"][0]
        self.assertEqual(item["title_id"], 7)
        self.assertEqual(item["candidate"]["id"], "123")
        self.assertEqual(item["candidate"]["name"], "Alien")
        self.assertNotIn("provider_internal", item["candidate"])

    def test_progress_api_reads_saved_suggestions_for_active_job(self):
        route = (ROOT / "app" / "routes" / "bulk_match_progress.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"/api/movies/bulk-match/progress"', route)
        self.assertIn("Depends(require_librarian)", route)
        self.assertIn('job.get("title_ids")', route)
        self.assertIn("movie_match_suggestions", route)
        self.assertIn('response.headers["Cache-Control"] = "no-store"', route)
        self.assertIn('"processed": int(job.get("processed") or 0)', route)

    def test_bulk_movie_rows_expose_progressive_render_targets(self):
        template = (ROOT / "app" / "templates" / "bulk_movie_match.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('data-bulk-match-progress-url="/api/movies/bulk-match/progress"', template)
        self.assertIn('data-bulk-movie-id="{{ movie.id }}"', template)
        self.assertIn("data-bulk-suggestion-cell", template)
        self.assertIn("data-bulk-confidence-cell", template)
        self.assertIn("Suggested TVDB result", template)

    def test_feedback_fetches_only_when_progress_advances(self):
        script = (ROOT / "app" / "static" / "bulk-match-feedback.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("refreshProgressiveMatches", script)
        self.assertIn("requested <= lastProgressiveProcessed", script)
        self.assertIn("fetch(progressUrl", script)
        self.assertIn("items.forEach(renderProgressiveItem)", script)
        self.assertIn("textContent", script)
        self.assertNotIn("innerHTML", script)

    def test_progress_router_is_registered(self):
        routes = (ROOT / "app" / "routes" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("build_bulk_match_progress_router", routes)
        self.assertLess(
            routes.index("build_bulk_match_progress_router,"),
            routes.index("build_review_router,"),
        )


if __name__ == "__main__":
    unittest.main()

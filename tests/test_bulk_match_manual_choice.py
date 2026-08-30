import json
import sqlite3
import unittest

from fastapi import Form, HTTPException

from app.routes.context import RouteContext
from app.routes.movie_manual_match import build_router


class _FakeDatabase:
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return self.connection


class _FakeTVDBError(RuntimeError):
    pass


class _FakeTVDB:
    def movie(self, movie_id):
        return {
            "id": movie_id,
            "name": "Run, Fatboy, Run",
            "year": "2007",
            "image": "https://example.invalid/run-fatboy-run.jpg",
        }

    def movie_id_from_reference(self, reference):
        if reference == "bad":
            raise ValueError("Bad TVDB reference")
        return 4901


class BulkMatchManualMovieChoiceTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE titles (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                year INTEGER,
                tvdb_movie_id INTEGER
            );
            CREATE TABLE movie_match_suggestions (
                title_id INTEGER PRIMARY KEY,
                candidate_json TEXT,
                confidence_score INTEGER,
                confidence_label TEXT,
                result_count INTEGER,
                exact INTEGER,
                error TEXT,
                analyzed_at TEXT
            );
            INSERT INTO titles (id,kind,title,year,tvdb_movie_id)
            VALUES (7,'movie','Run, Fat Boy, Run',2008,NULL);
            INSERT INTO movie_match_suggestions
                (title_id,candidate_json,confidence_score,confidence_label,result_count,exact,error,analyzed_at)
            VALUES
                (7,'{"id": 320806, "name": "Fan gun ba! Nan hai", "year": "2005"}',26,'Low',1,0,'','old');
            """
        )
        self.connection.commit()
        self.store_calls = []

        def redirect(path, message=""):
            return {"path": path, "message": message}

        def store_movie_match(title_id, movie_id):
            self.store_calls.append((title_id, movie_id))
            self.connection.execute(
                "UPDATE titles SET tvdb_movie_id=? WHERE id=?", (movie_id, title_id)
            )
            self.connection.commit()
            return "TVDB"

        def match_success_redirect(title_id, message, return_to="", match_origin=""):
            return {
                "path": f"/titles/{title_id}",
                "message": message,
                "return_to": return_to,
                "match_origin": match_origin,
            }

        namespace = {
            "Form": Form,
            "HTTPException": HTTPException,
            "TVDBError": _FakeTVDBError,
            "db": _FakeDatabase(self.connection),
            "match_confidence": lambda title, year, candidate: {
                "score": 92,
                "label": "High",
                "exact_title": False,
                "exact_year": False,
            },
            "match_success_redirect": match_success_redirect,
            "redirect": redirect,
            "store_movie_match": store_movie_match,
            "tvdb": _FakeTVDB(),
        }
        router, _ = build_router(RouteContext(namespace))
        self.routes = {route.path: route.endpoint for route in router.routes}

    def tearDown(self):
        self.connection.close()

    def test_bulk_result_choice_replaces_suggestion_without_applying_metadata(self):
        response = self.routes["/titles/{title_id}/movie/{movie_id}"](
            title_id=7,
            movie_id=4901,
            return_to="/movies/bulk-match?review=true&selected=true",
            match_origin="bulk-movie-selected",
            search_query="Run Fatboy Run",
            result_count=2,
        )

        self.assertEqual(
            response["path"],
            "/movies/bulk-match?review=true&selected=true#bulk-title-7",
        )
        self.assertEqual(self.store_calls, [])
        title = self.connection.execute(
            "SELECT tvdb_movie_id FROM titles WHERE id=7"
        ).fetchone()
        self.assertIsNone(title["tvdb_movie_id"])

        suggestion = self.connection.execute(
            "SELECT * FROM movie_match_suggestions WHERE title_id=7"
        ).fetchone()
        candidate = json.loads(suggestion["candidate_json"])
        self.assertEqual(candidate["tvdb_id"], 4901)
        self.assertEqual(candidate["name"], "Run, Fatboy, Run")
        self.assertEqual(candidate["_search_query"], "Run Fatboy Run")
        self.assertTrue(candidate["_manual_choice"])
        self.assertEqual(suggestion["confidence_score"], 92)
        self.assertEqual(suggestion["confidence_label"], "High")
        self.assertEqual(suggestion["result_count"], 2)
        self.assertEqual(suggestion["exact"], 1)

    def test_bulk_direct_reference_choice_also_stays_in_review(self):
        response = self.routes["/titles/{title_id}/movie-manual"](
            title_id=7,
            tvdb_reference="https://thetvdb.com/movies/run-fatboy-run",
            return_to="/movies/bulk-match?review=true",
            match_origin="bulk-movie",
            search_query="Run Fat Boy Run",
            result_count=1,
        )

        self.assertEqual(
            response["path"], "/movies/bulk-match?review=true#bulk-title-7"
        )
        self.assertEqual(self.store_calls, [])
        self.assertIsNone(
            self.connection.execute(
                "SELECT tvdb_movie_id FROM titles WHERE id=7"
            ).fetchone()["tvdb_movie_id"]
        )

    def test_non_bulk_choice_keeps_existing_immediate_match_behavior(self):
        response = self.routes["/titles/{title_id}/movie/{movie_id}"](
            title_id=7,
            movie_id=4901,
            return_to="/titles/7/tvdb",
            match_origin="",
            search_query="Run Fatboy Run",
            result_count=2,
        )

        self.assertEqual(self.store_calls, [(7, 4901)])
        self.assertEqual(response["path"], "/titles/7")
        self.assertEqual(
            self.connection.execute(
                "SELECT tvdb_movie_id FROM titles WHERE id=7"
            ).fetchone()["tvdb_movie_id"],
            4901,
        )

    def test_manual_search_template_remembers_corrected_bulk_selection(self):
        from pathlib import Path

        template = (
            Path(__file__).resolve().parents[1] / "app" / "templates" / "tvdb.html"
        ).read_text(encoding="utf-8")
        self.assertIn('name="search_query" value="{{ q }}"', template)
        self.assertIn('name="result_count" value="{{ results|length }}"', template)
        self.assertIn('"bulk-movie-selected"', template)
        self.assertIn(
            'infomancer:bulk-match-selection:/movies/bulk-match:${scope}', template
        )
        self.assertIn('remembered["{{ title.id }}"] = true', template)
        self.assertIn("https://www.thetvdb.com/search?query=", template)


if __name__ == "__main__":
    unittest.main()

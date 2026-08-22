from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from fastapi import Request

from app.db import Database
from app.routes.library_search_optimized import search_response


class _RenderedTemplate:
    def render(self, context):
        return '<section class="cover-library" id="cover-library"></section>'


class _Environment:
    def get_template(self, name):
        if name != "library_results.html":
            raise AssertionError(name)
        return _RenderedTemplate()


class _Templates:
    env = _Environment()

    def TemplateResponse(self, *_args, **_kwargs):
        raise AssertionError("live search should not render the full Library page")


def _request(query: str) -> Request:
    request = Request({
        "type": "http", "method": "GET", "scheme": "http",
        "path": "/library", "raw_path": b"/library",
        "query_string": f"q={query}".encode(),
        "headers": [(b"x-infomancer-partial", b"library")],
        "client": ("127.0.0.1", 1000), "server": ("test", 80),
    })
    request.state.user = SimpleNamespace(id=0)
    return request


class LibrarySearchOptimizationTests(unittest.TestCase):
    def test_direct_search_result_does_not_pay_for_fuzzy_people_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "direct.db")
            database.initialize()
            with database.connect() as conn:
                root_id = conn.execute(
                    "INSERT INTO roots(path,kind,label) VALUES ('/movies','movie','Movies')"
                ).lastrowid
                conn.execute(
                    "INSERT INTO titles(root_id,kind,title,folder_path) VALUES (?,?,?,?)",
                    (root_id, "movie", "Alien", "/movies/Alien"),
                )

            def fuzzy_should_not_run(*_args):
                raise AssertionError("fuzzy_people should be fallback-only")

            response = search_response(
                database, _Templates(), lambda value: value,
                fuzzy_should_not_run, _request("Alien"),
                q="Alien", kind="all", view="list",
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["X-InfoMancer-Library-Query"], "candidate-search")

    def test_fuzzy_people_runs_when_direct_search_has_no_candidates(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "fuzzy.db")
            database.initialize()
            with database.connect() as conn:
                root_id = conn.execute(
                    "INSERT INTO roots(path,kind,label) VALUES ('/movies','movie','Movies')"
                ).lastrowid
                title_id = conn.execute(
                    "INSERT INTO titles(root_id,kind,title,folder_path) VALUES (?,?,?,?)",
                    (root_id, "movie", "Movie One", "/movies/Movie One"),
                ).lastrowid
                conn.execute(
                    """INSERT INTO title_credits(title_id,imdb_person_id,person_name,role)
                       VALUES (?,?,?,?)""",
                    (title_id, "nm0000001", "Sigourney Weaver", "actor"),
                )

            calls = []

            def fuzzy_people(query, kind, limit):
                calls.append((query, kind, limit))
                return [{"person_name": "Sigourney Weaver"}]

            response = search_response(
                database, _Templates(), lambda value: value,
                fuzzy_people, _request("Sigorny"),
                q="Sigorny", kind="all", view="list",
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(calls, [("Sigorny", "all", 6)])


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import unittest

from app.tvdb import TVDBClient


ROOT = Path(__file__).resolve().parents[1]


class StubTVDBClient(TVDBClient):
    def __init__(self):
        super().__init__("test-key")
        self.calls = []

    def _get(
        self, path: str, params: dict | None = None, *,
        allow_not_found: bool = False, _retry_auth: bool = True,
    ) -> dict:
        self.calls.append((path, params, allow_not_found))
        if path == "/search":
            return {"data": []}
        if path == "/movies/slug/bring-me-the-head-of-alfredo-garcia":
            return {
                "data": {
                    "id": 7464,
                    "name": "Bring Me the Head of Alfredo Garcia",
                    "slug": "bring-me-the-head-of-alfredo-garcia",
                    "year": "1974",
                    "image": "https://artworks.thetvdb.com/example.jpg",
                }
            }
        return {}


class TVDBMovieSlugFallbackTests(unittest.TestCase):
    def test_movie_search_uses_canonical_slug_after_text_search_miss(self):
        client = StubTVDBClient()

        results = client.search_movies("Bring Me the Head of Alfredo Garcia", 1974)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["tvdb_id"], 7464)
        self.assertEqual(results[0]["year"], "1974")
        self.assertEqual(results[0]["image_url"], "https://artworks.thetvdb.com/example.jpg")
        self.assertEqual(client.calls[0][0], "/search")
        self.assertEqual(
            client.calls[1][0],
            "/movies/slug/bring-me-the-head-of-alfredo-garcia",
        )

    def test_slug_fallback_respects_a_known_conflicting_year(self):
        client = StubTVDBClient()

        self.assertEqual(
            client.search_movies("Bring Me the Head of Alfredo Garcia", 1975),
            [],
        )

    def test_movie_page_link_resolves_directly_through_slug_endpoint(self):
        client = StubTVDBClient()

        movie_id = client.movie_id_from_reference(
            "https://thetvdb.com/movies/bring-me-the-head-of-alfredo-garcia"
        )

        self.assertEqual(movie_id, 7464)
        self.assertEqual(
            client.calls[-1][0],
            "/movies/slug/bring-me-the-head-of-alfredo-garcia",
        )

    def test_numeric_movie_id_does_not_need_slug_lookup(self):
        client = StubTVDBClient()

        self.assertEqual(client.movie_id_from_reference("7464"), 7464)
        self.assertEqual(client.calls, [])

    def test_manual_movie_match_is_exposed_and_registered(self):
        template = (ROOT / "app/templates/tvdb.html").read_text(encoding="utf-8")
        routes = (ROOT / "app/routes/__init__.py").read_text(encoding="utf-8")
        manual_route = (ROOT / "app/routes/movie_manual_match.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("'movie-manual' if entity == 'movie' else 'tvdb-manual'", template)
        self.assertIn("TVDB {{ 'movie' if entity == 'movie' else 'series' }} link or ID", template)
        self.assertIn("https://www.thetvdb.com/search?query={{ q|urlencode }}", template)
        self.assertIn("Search TVDB website", template)
        self.assertIn('target="_blank" rel="noopener noreferrer"', template)
        self.assertIn("build_movie_manual_match_router", routes)
        self.assertIn('@librarian_post("/titles/{title_id}/movie-manual")', manual_route)
        self.assertIn("tvdb.movie_id_from_reference(tvdb_reference)", manual_route)
        self.assertIn("store_movie_match(title_id, movie_id)", manual_route)


if __name__ == "__main__":
    unittest.main()

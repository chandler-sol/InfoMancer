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
            query = (params or {}).get("query")
            if query == "Jackass 3.5":
                return {
                    "data": [{
                        "id": 12345,
                        "name": "Jackass 3.5",
                        "year": "2011",
                    }]
                }
            if query == "The Painted Bird":
                return {
                    "data": [{
                        "id": 8661,
                        "name": "Trail of the Pink Panther",
                        "year": "1982",
                    }]
                }
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
        if path == "/movies/slug/the-painted-bird":
            return {
                "data": {
                    "id": 135322,
                    "name": "The Painted Bird",
                    "slug": "the-painted-bird",
                    "year": "2019",
                    "image": "https://artworks.thetvdb.com/painted-bird.jpg",
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
        self.assertTrue(any(
            call[0] == "/movies/slug/bring-me-the-head-of-alfredo-garcia"
            for call in client.calls
        ))

    def test_noisy_text_search_still_checks_exact_slug(self):
        client = StubTVDBClient()

        results = client.search_movies("The Painted Bird", 2020)

        self.assertEqual(results[0]["tvdb_id"], 135322)
        self.assertEqual(results[0]["name"], "The Painted Bird")
        self.assertEqual(results[0]["year"], "2019")
        self.assertTrue(results[0]["_possible_match"])
        self.assertEqual(results[1]["id"], 8661)
        self.assertTrue(any(
            call[0] == "/movies/slug/the-painted-bird" for call in client.calls
        ))

    def test_slug_fallback_allows_one_year_release_market_difference(self):
        client = StubTVDBClient()

        results = client.search_movies("Bring Me the Head of Alfredo Garcia", 1975)

        self.assertEqual(results[0]["tvdb_id"], 7464)
        self.assertTrue(results[0]["_possible_match"])

    def test_slug_fallback_rejects_a_larger_known_year_conflict(self):
        client = StubTVDBClient()

        self.assertEqual(
            client.search_movies("Bring Me the Head of Alfredo Garcia", 1976),
            [],
        )

    def test_decimal_movie_title_is_recovered_after_strict_search_miss(self):
        client = StubTVDBClient()

        results = client.search_movies("Jackass 3 5", 2011)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Jackass 3.5")
        self.assertEqual(results[0]["_search_query"], "Jackass 3.5")
        self.assertTrue(results[0]["_possible_match"])
        self.assertEqual(client.calls[0][1]["query"], "Jackass 3 5")
        self.assertEqual(client.calls[1][1]["query"], "Jackass 3.5")

    def test_decimal_recovery_does_not_rewrite_unrelated_numeric_titles(self):
        client = StubTVDBClient()

        self.assertEqual(client._decimal_query_candidate("Apollo 13"), "")
        self.assertEqual(client._decimal_query_candidate("Ocean's 11 12"), "")
        self.assertEqual(client._decimal_query_candidate("Jackass 3 5"), "Jackass 3.5")

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
        self.assertIn("build_movie_manual_match_router", routes)
        self.assertIn('@librarian_post("/titles/{title_id}/movie-manual")', manual_route)
        self.assertIn("tvdb.movie_id_from_reference(tvdb_reference)", manual_route)
        self.assertIn("store_movie_match(title_id, movie_id)", manual_route)

    def test_tvdb_website_search_is_only_offered_after_an_api_search_miss(self):
        template = (ROOT / "app/templates/tvdb.html").read_text(encoding="utf-8")

        self.assertIn("{% if q and not results %}", template)
        self.assertIn("https://www.thetvdb.com/search?query={{ q|urlencode }}", template)
        self.assertIn("Search TVDB website", template)


if __name__ == "__main__":
    unittest.main()

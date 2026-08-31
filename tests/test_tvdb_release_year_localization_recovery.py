from __future__ import annotations

import unittest

from app.tvdb import TVDBClient


class YearRelaxedTVDBClient(TVDBClient):
    def __init__(self):
        super().__init__(api_key="test")
        self.calls = []

    def _get(self, path, params=None, *, allow_not_found=False, _retry_auth=True):
        params = params or {}
        self.calls.append((path, dict(params)))
        if path == "/search":
            if params.get("query") == "The Painted Bird" and params.get("year") == 2020:
                return {
                    "data": [{
                        "id": 8661,
                        "name": "Trail of the Pink Panther",
                        "year": "1982",
                        "type": "movie",
                    }]
                }
            if params.get("query") == "The Painted Bird" and "year" not in params:
                return {
                    "data": [{
                        "id": 135322,
                        "name": "The Painted Bird",
                        "year": "2019",
                        "type": "movie",
                    }]
                }
            return {"data": []}
        if path.startswith("/movies/slug/"):
            return {}
        return {}


class LocalizedTVDBClient(TVDBClient):
    def __init__(self):
        super().__init__(api_key="test")
        self.calls = []

    def _get(self, path, params=None, *, allow_not_found=False, _retry_auth=True):
        params = params or {}
        self.calls.append((path, dict(params)))
        if path == "/search":
            return {
                "data": [
                    {
                        "id": 8661,
                        "name": "Trail of the Pink Panther",
                        "year": "1982",
                        "type": "movie",
                    },
                    {
                        "id": 135322,
                        "name": "Malowany ptak",
                        "year": "2019",
                        "type": "movie",
                    },
                ]
            }
        if path.startswith("/movies/slug/"):
            return {}
        if path == "/movies/135322/translations/eng":
            return {
                "data": {
                    "name": "The Painted Bird",
                    "overview": "A boy wanders through wartime Eastern Europe.",
                }
            }
        if path == "/movies/8661/translations/eng":
            return {"data": {"name": "Trail of the Pink Panther"}}
        return {}


class TVDBReleaseYearLocalizationRecoveryTests(unittest.TestCase):
    def test_movie_search_retries_without_year_for_regional_release_difference(self):
        client = YearRelaxedTVDBClient()

        results = client.search_movies("The Painted Bird", 2020)

        self.assertEqual(results[0]["id"], 135322)
        self.assertEqual(results[0]["name"], "The Painted Bird")
        self.assertEqual(results[0]["year"], "2019")
        self.assertTrue(results[0]["_possible_match"])
        search_calls = [params for path, params in client.calls if path == "/search"]
        self.assertIn(
            {"query": "The Painted Bird", "type": "movie", "year": 2020},
            search_calls,
        )
        self.assertIn(
            {"query": "The Painted Bird", "type": "movie"},
            search_calls,
        )

    def test_localized_provider_result_is_reranked_by_english_translation(self):
        client = LocalizedTVDBClient()

        results = client.search_movies("The Painted Bird", 2020)

        self.assertEqual(results[0]["id"], 135322)
        self.assertEqual(results[0]["name"], "The Painted Bird")
        self.assertEqual(results[0]["_default_name"], "Malowany ptak")
        self.assertTrue(results[0]["_possible_match"])
        self.assertTrue(results[0]["overview"].startswith("A boy wanders"))
        self.assertTrue(any(
            path == "/movies/135322/translations/eng"
            for path, _ in client.calls
        ))


if __name__ == "__main__":
    unittest.main()

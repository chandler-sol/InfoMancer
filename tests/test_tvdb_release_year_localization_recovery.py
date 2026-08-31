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
    def __init__(self, *, exact_year: bool = False):
        super().__init__(api_key="test")
        self.calls = []
        self.exact_year = exact_year

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
                        "year": "2020" if self.exact_year else "2019",
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


class LocalizedSeriesTVDBClient(TVDBClient):
    def __init__(self):
        super().__init__(api_key="test")
        self.calls = []

    def _get(self, path, params=None, *, allow_not_found=False, _retry_auth=True):
        params = params or {}
        self.calls.append((path, dict(params)))
        if path == "/search":
            return {
                "data": [
                    {"id": 22, "name": "Haus des Geldes", "type": "series"},
                    {"id": 23, "name": "Unrelated", "type": "series"},
                ]
            }
        if path == "/series/22/translations/eng":
            return {"data": {"name": "Money Heist"}}
        if path == "/series/23/translations/eng":
            return {"data": {"name": "Unrelated"}}
        return {}


class AliasIdentityTVDBClient(TVDBClient):
    def __init__(self):
        super().__init__(api_key="test")
        self.calls = []

    def _get(self, path, params=None, *, allow_not_found=False, _retry_auth=True):
        params = params or {}
        self.calls.append((path, dict(params)))
        if path == "/search":
            return {
                "data": [{
                    "id": 555,
                    "name": "La vita è bella",
                    "aliases": ["Life Is Beautiful"],
                    "year": "1997",
                    "type": "movie",
                }]
            }
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
        self.assertIn("Malowany ptak", results[0]["aliases"])
        self.assertIn("The Painted Bird", results[0]["aliases"])
        self.assertEqual(results[0]["_identity_match_source"], "translation")
        self.assertTrue(results[0]["_possible_match"])
        self.assertTrue(results[0]["overview"].startswith("A boy wanders"))
        self.assertTrue(any(
            path == "/movies/135322/translations/eng"
            for path, _ in client.calls
        ))

    def test_translation_is_identity_evidence_not_automatically_a_possible_match(self):
        client = LocalizedTVDBClient(exact_year=True)

        results = client.search_movies("The Painted Bird", 2020)

        self.assertEqual(results[0]["name"], "The Painted Bird")
        self.assertEqual(results[0]["year"], "2020")
        self.assertNotIn("_possible_match", results[0])
        self.assertEqual(client._movie_result_similarity("The Painted Bird", results[0]), 1.0)

    def test_provider_alias_is_first_class_identity_without_translation_lookup(self):
        client = AliasIdentityTVDBClient()

        results = client.search_movies("Life Is Beautiful", 1997)

        self.assertEqual(results[0]["id"], 555)
        self.assertEqual(client._movie_result_similarity("Life Is Beautiful", results[0]), 1.0)
        self.assertEqual(
            [path for path, _ in client.calls],
            ["/search"],
        )

    def test_series_search_uses_the_same_translation_identity_pipeline(self):
        client = LocalizedSeriesTVDBClient()

        results = client.search_series("Money Heist")

        self.assertEqual(results[0]["id"], 22)
        self.assertEqual(results[0]["name"], "Money Heist")
        self.assertIn("Haus des Geldes", results[0]["aliases"])
        self.assertIn("Money Heist", results[0]["aliases"])
        self.assertEqual(results[0]["_identity_match_source"], "translation")
        self.assertTrue(any(
            path == "/series/22/translations/eng"
            for path, _ in client.calls
        ))

    def test_identity_normalization_preserves_non_latin_provider_aliases(self):
        client = TVDBClient(api_key="test")

        result = {
            "name": "Crouching Tiger, Hidden Dragon",
            "aliases": ["卧虎藏龙"],
        }

        self.assertEqual(client._movie_result_similarity("卧虎藏龙", result), 1.0)


if __name__ == "__main__":
    unittest.main()

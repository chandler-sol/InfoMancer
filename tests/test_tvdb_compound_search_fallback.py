import unittest

from app.tvdb import TVDBClient


class CompoundSearchTVDBClient(TVDBClient):
    def __init__(self, responses: dict[str, list[dict]]):
        super().__init__("test-key")
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def _get(
        self, path: str, params: dict | None = None, *,
        allow_not_found: bool = False, _retry_auth: bool = True,
    ) -> dict:
        params = dict(params or {})
        self.calls.append((path, params))
        if path != "/search":
            return {}
        return {"data": self.responses.get(str(params.get("query") or ""), [])}


class TVDBCompoundSearchFallbackTests(unittest.TestCase):
    def test_unrelated_strict_hit_retries_compound_title_and_prefers_match(self):
        client = CompoundSearchTVDBClient({
            "Run Fat Boy Run": [{
                "id": 320806,
                "name": "Fan gun ba! Nan hai",
                "year": "2005",
            }],
            "Run FatBoy Run": [{
                "id": 4901,
                "name": "Run, Fatboy, Run",
                "year": "2007",
            }],
        })

        results = client.search_movies("Run Fat Boy Run")

        self.assertEqual(results[0]["id"], 4901)
        self.assertEqual(results[0]["_search_query"], "Run FatBoy Run")
        self.assertTrue(results[0]["_possible_match"])
        self.assertEqual(
            [params["query"] for path, params in client.calls if path == "/search"],
            ["Run Fat Boy Run", "Run FatBoy Run"],
        )

    def test_plausible_strict_hit_does_not_spend_an_extra_provider_request(self):
        client = CompoundSearchTVDBClient({
            "Alien": [{"id": 101, "name": "Alien", "year": "1979"}],
        })

        results = client.search_movies("Alien")

        self.assertEqual(results[0]["id"], 101)
        self.assertEqual(len(client.calls), 1)

    def test_compound_retry_relaxes_year_once_and_preserves_strict_fallback(self):
        strict = {"id": 77, "name": "Completely Different", "year": "2001"}
        client = CompoundSearchTVDBClient({"Odd Title Words": [strict]})

        results = client.search_movies("Odd Title Words", 2001)

        self.assertEqual(results, [strict])
        search_calls = [params for path, params in client.calls if path == "/search"]
        self.assertEqual(search_calls[0].get("year"), 2001)
        self.assertEqual(
            sum(1 for call in search_calls if "year" not in call),
            1,
        )
        self.assertTrue(all(
            call.get("year") == 2001
            for call in search_calls
            if "year" in call
        ))
        self.assertLessEqual(len(search_calls), 4)
        self.assertTrue(any(path.startswith("/movies/slug/") for path, _ in client.calls))

    def test_compact_similarity_recognizes_space_vs_compound_spelling(self):
        client = CompoundSearchTVDBClient({})

        score = client._movie_result_similarity(
            "Run Fat Boy Run",
            {"name": "Run, Fatboy, Run"},
        )

        self.assertEqual(score, 1.0)


if __name__ == "__main__":
    unittest.main()

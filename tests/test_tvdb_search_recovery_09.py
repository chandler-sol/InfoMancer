import unittest

from app.tvdb import TVDBClient


class StubTVDBClient(TVDBClient):
    def __init__(self):
        super().__init__("test-key")
        self.calls: list[tuple[str, dict]] = []

    def _get(
        self, path: str, params: dict | None = None, *,
        allow_not_found: bool = False, _retry_auth: bool = True,
    ) -> dict:
        params = dict(params or {})
        self.calls.append((path, params))
        if path == "/search":
            query = str(params.get("query") or "")
            if query == "Run Fat Boy Run":
                return {"data": [{"id": 320806, "name": "Fan gun ba! Nan hai", "year": "2005"}]}
            if query == "Run FatBoy Run":
                return {"data": [{"id": 4901, "name": "Run, Fatboy, Run", "year": "2007"}]}
            if query == "The Painted Bird":
                return {"data": [{"id": 8661, "name": "Trail of the Pink Panther", "year": "1982"}]}
            if query == "Jackass 3.5":
                return {"data": [{"id": 12345, "name": "Jackass 3.5", "year": "2011"}]}
            if query == "Alien":
                return {"data": [{"id": 101, "name": "Alien", "year": "1979"}]}
            return {"data": []}
        if path == "/movies/slug/the-painted-bird":
            return {"data": {"id": 135322, "name": "The Painted Bird", "year": "2019"}}
        return {}


class TVDBSearchRecovery09Tests(unittest.TestCase):
    def test_compound_title_recovers_from_unrelated_strict_hit(self):
        client = StubTVDBClient()
        results = client.search_movies("Run Fat Boy Run")
        self.assertEqual(results[0]["id"], 4901)
        self.assertTrue(results[0]["_possible_match"])
        self.assertEqual(results[0]["_search_query"], "Run FatBoy Run")

    def test_noisy_search_checks_canonical_slug_and_allows_one_year_delta(self):
        client = StubTVDBClient()
        results = client.search_movies("The Painted Bird", 2020)
        self.assertEqual(results[0]["tvdb_id"], 135322)
        self.assertEqual(results[0]["year"], "2019")
        self.assertTrue(results[0]["_possible_match"])
        self.assertEqual(results[1]["id"], 8661)

    def test_decimal_title_recovery_is_review_only(self):
        client = StubTVDBClient()
        results = client.search_movies("Jackass 3 5", 2011)
        self.assertEqual(results[0]["name"], "Jackass 3.5")
        self.assertTrue(results[0]["_possible_match"])
        self.assertEqual(results[0]["_search_query"], "Jackass 3.5")

    def test_good_search_does_not_spend_fallback_requests(self):
        client = StubTVDBClient()
        results = client.search_movies("Alien", 1979)
        self.assertEqual(results[0]["id"], 101)
        self.assertEqual(len(client.calls), 1)


if __name__ == "__main__":
    unittest.main()

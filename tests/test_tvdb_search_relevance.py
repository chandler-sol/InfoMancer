from app.tvdb import TVDBClient


class RelevanceTVDBClient(TVDBClient):
    def __init__(self):
        super().__init__(api_key="test")
        self.calls = []

    def _get(self, path, params=None, *, allow_not_found=False, _retry_auth=True):
        self.calls.append((path, params or {}))
        if path == "/search":
            query = (params or {}).get("query")
            if query == "Painted Bird":
                return {
                    "data": [
                        {
                            "id": 999001,
                            "name": "Trail of the Pink Panther",
                            "year": "1982",
                            "type": "movie",
                        },
                        {
                            "id": 135322,
                            "name": "The Painted Bird",
                            "year": "2019",
                            "type": "movie",
                        },
                    ]
                }
            if query == "painted bird":
                return {"data": []}
            return {"data": []}
        if path == "/movies/slug/painted-bird":
            return {}
        if path == "/movies/slug/the-painted-bird":
            return {
                "data": {
                    "id": 135322,
                    "name": "The Painted Bird",
                    "year": "2019",
                    "type": "movie",
                }
            }
        return {}


def test_provider_results_are_ranked_by_title_relevance():
    client = RelevanceTVDBClient()

    results = client.search_movies("Painted Bird", 2020)

    assert results[0]["tvdb_id"] == 135322
    assert results[0]["name"] == "The Painted Bird"
    assert results[1]["name"] == "Trail of the Pink Panther"


def test_manual_search_without_leading_the_finds_canonical_movie():
    client = RelevanceTVDBClient()

    results = client.search_movies("painted bird")

    assert results[0]["tvdb_id"] == 135322
    assert results[0]["name"] == "The Painted Bird"
    assert results[0]["_possible_match"] is True
    assert any(path == "/movies/slug/the-painted-bird" for path, _ in client.calls)


def test_article_difference_counts_as_exact_title_similarity():
    result = {"name": "The Painted Bird", "year": "2019"}

    assert TVDBClient._movie_result_similarity("painted bird", result) == 1.0


def test_matching_year_breaks_exact_title_ties():
    ranked = TVDBClient._rank_movie_results(
        "The Thing",
        1982,
        [
            {"id": 1, "name": "The Thing", "year": "2011"},
            {"id": 2, "name": "The Thing", "year": "1982"},
        ],
    )

    assert ranked[0]["id"] == 2

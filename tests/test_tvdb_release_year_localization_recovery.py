from __future__ import annotations

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


def test_movie_search_retries_without_year_for_regional_release_difference():
    client = YearRelaxedTVDBClient()

    results = client.search_movies("The Painted Bird", 2020)

    assert results[0]["id"] == 135322
    assert results[0]["name"] == "The Painted Bird"
    assert results[0]["year"] == "2019"
    assert results[0]["_possible_match"] is True
    search_calls = [params for path, params in client.calls if path == "/search"]
    assert {"query": "The Painted Bird", "type": "movie", "year": 2020} in search_calls
    assert {"query": "The Painted Bird", "type": "movie"} in search_calls


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


def test_localized_provider_result_is_reranked_by_english_translation():
    client = LocalizedTVDBClient()

    results = client.search_movies("The Painted Bird", 2020)

    assert results[0]["id"] == 135322
    assert results[0]["name"] == "The Painted Bird"
    assert results[0]["_default_name"] == "Malowany ptak"
    assert results[0]["_possible_match"] is True
    assert results[0]["overview"].startswith("A boy wanders")
    assert any(
        path == "/movies/135322/translations/eng"
        for path, _ in client.calls
    )


def test_year_relaxation_does_not_promote_distant_same_title_remake():
    class DistantYearClient(YearRelaxedTVDBClient):
        def _get(self, path, params=None, *, allow_not_found=False, _retry_auth=True):
            params = params or {}
            if path == "/search" and "year" not in params:
                return {
                    "data": [{
                        "id": 1,
                        "name": "The Painted Bird",
                        "year": "1980",
                        "type": "movie",
                    }]
                }
            return super()._get(
                path, params,
                allow_not_found=allow_not_found,
                _retry_auth=_retry_auth,
            )

    client = DistantYearClient()
    results = client.search_movies("The Painted Bird", 2020)

    assert results[0]["id"] == 8661
    assert all(result.get("id") != 1 for result in results[:1])

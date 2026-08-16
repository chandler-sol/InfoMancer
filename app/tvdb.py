from __future__ import annotations

from dataclasses import dataclass

import httpx


BASE_URL = "https://api4.thetvdb.com/v4"


class TVDBError(RuntimeError):
    pass


@dataclass
class TVDBClient:
    api_key: str
    pin: str = ""
    _token: str = ""

    def _login(self) -> str:
        if not self.api_key:
            raise TVDBError("TVDB_API_KEY is not configured")
        payload = {"apikey": self.api_key}
        if self.pin:
            payload["pin"] = self.pin
        try:
            response = httpx.post(f"{BASE_URL}/login", json=payload, timeout=20)
        except httpx.RequestError as exc:
            raise TVDBError(
                "TheTVDB could not be reached while signing in. Try again shortly."
            ) from exc
        self._check(response)
        self._token = response.json()["data"]["token"]
        return self._token

    def test_connection(self) -> None:
        """Force a fresh credential check without changing catalog data."""
        self._token = ""
        self._login()

    def _get(
        self, path: str, params: dict | None = None, *,
        allow_not_found: bool = False,
    ) -> dict:
        token = self._token or self._login()
        try:
            response = httpx.get(
                f"{BASE_URL}{path}", params=params,
                headers={"Authorization": f"Bearer {token}"}, timeout=30,
            )
        except httpx.RequestError as exc:
            raise TVDBError(
                "TheTVDB disconnected before the metadata request finished. "
                "Try again shortly."
            ) from exc
        if response.status_code == 401:
            self._token = ""
            return self._get(path, params, allow_not_found=allow_not_found)
        if allow_not_found and response.status_code == 404:
            return {}
        self._check(response)
        return response.json()

    @staticmethod
    def _check(response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text[:300]
            raise TVDBError(f"TVDB returned {response.status_code}: {detail}") from exc

    @staticmethod
    def _apply_english_translation(record: dict, translation: dict) -> dict:
        """Prefer English display text without discarding the provider base record."""
        record["_english_translation"] = translation
        if translation.get("name"):
            record["name"] = translation["name"]
        if translation.get("overview"):
            record["overview"] = translation["overview"]
        return record

    def search_series(self, query: str) -> list[dict]:
        payload = self._get("/search", {"query": query, "type": "series"})
        return payload.get("data") or []

    def search_movies(self, query: str, year: int | None = None) -> list[dict]:
        params = {"query": query, "type": "movie"}
        if year:
            params["year"] = year
        payload = self._get("/search", params)
        return payload.get("data") or []

    def series(self, series_id: int) -> dict:
        record = self._get(f"/series/{series_id}/extended").get("data") or {}
        record["_default_name"] = record.get("name")
        return self._apply_english_translation(
            record, self.translation("series", series_id, "eng")
        )

    def movie(self, movie_id: int) -> dict:
        record = self._get(f"/movies/{movie_id}/extended").get("data") or {}
        record["_default_name"] = record.get("name")
        return self._apply_english_translation(
            record, self.translation("movie", movie_id, "eng")
        )

    def translation(self, entity: str, entity_id: int, language: str = "eng") -> dict:
        """Load one localized title without making missing translations fatal."""
        if entity not in {"series", "movie"}:
            raise ValueError("Translation entity must be series or movie")
        endpoint = "series" if entity == "series" else "movies"
        return (
            self._get(
                f"/{endpoint}/{entity_id}/translations/{language}",
                allow_not_found=True,
            ).get("data") or {}
        )

    def episodes(self, series_id: int) -> list[dict]:
        episodes: list[dict] = []
        page = 0
        while True:
            payload = self._get(
                f"/series/{series_id}/episodes/default/eng", {"page": page}
            )
            episodes.extend(payload.get("data", {}).get("episodes") or [])
            links = payload.get("links") or {}
            if links.get("next") is None:
                break
            page += 1
        return episodes

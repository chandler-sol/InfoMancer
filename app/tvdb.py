from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata
from urllib.parse import quote, urlparse

import httpx


BASE_URL = "https://api4.thetvdb.com/v4"
MOVIE_SEARCH_PLAUSIBLE_SCORE = 0.65


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
        allow_not_found: bool = False, _retry_auth: bool = True,
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
        if response.status_code == 401 and _retry_auth:
            # A cached bearer token may have expired. Retry exactly once with a
            # fresh login so persistent 401 responses cannot recurse indefinitely.
            self._token = ""
            return self._get(
                path, params, allow_not_found=allow_not_found, _retry_auth=False,
            )
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

    @staticmethod
    def _movie_slug_candidate(query: str) -> str:
        """Build the conservative TVDB slug used only after normal search misses."""
        value = query.strip()
        if not value or "://" in value or "/" in value:
            return ""
        ascii_value = unicodedata.normalize("NFKD", value).encode(
            "ascii", "ignore"
        ).decode("ascii")
        return re.sub(r"[^a-z0-9]+", "-", ascii_value.casefold()).strip("-")

    @staticmethod
    def _decimal_query_candidate(query: str) -> str:
        """Recover one likely decimal title damaged by filename separator cleanup.

        Older catalog scans converted every period to a space, so a legitimate title
        such as ``Jackass 3.5`` could be stored as ``Jackass 3 5``. Only repair a
        trailing numeric pair whose fractional part is one digit, and only after the
        provider's strict query misses. The returned candidates still go through the
        normal InfoMancer confidence/review flow; this never auto-accepts a match.
        """
        value = query.strip()
        match = re.search(r"(?<!\d)(\d{1,2})\s+(\d)(?!\d)\s*$", value)
        if not match:
            return ""
        return f"{value[:match.start()]}{match.group(1)}.{match.group(2)}"

    @staticmethod
    def _compact_movie_title(value: str) -> str:
        """Normalize a title for provider-result plausibility checks."""
        ascii_value = unicodedata.normalize("NFKD", str(value or "")).encode(
            "ascii", "ignore"
        ).decode("ascii")
        return re.sub(r"[^a-z0-9]+", "", ascii_value.casefold())

    @classmethod
    def _movie_result_similarity(cls, query: str, result: dict) -> float:
        """Score whether a TVDB search hit is plausibly related to the query."""
        expected = cls._compact_movie_title(query)
        if not expected:
            return 0.0

        names: list[str] = []
        for key in ("name", "title"):
            if result.get(key):
                names.append(str(result[key]))
        for alias in result.get("aliases") or []:
            if isinstance(alias, str):
                names.append(alias)
            elif isinstance(alias, dict):
                for key in ("name", "title"):
                    if alias.get(key):
                        names.append(str(alias[key]))

        best = 0.0
        for name in names:
            offered = cls._compact_movie_title(name)
            if offered:
                best = max(best, SequenceMatcher(None, expected, offered).ratio())
        return best

    @staticmethod
    def _compound_query_candidates(query: str) -> list[str]:
        """Build a few adjacent-word compounds for a clearly bad provider hit.

        TVDB's text index can distinguish ``Fat Boy`` from ``Fatboy`` even though
        those forms are effectively identical for matching. Try pairs nearest the
        middle of the title first and keep the retry set deliberately small.
        """
        value = query.strip()
        if not value or "://" in value or "/" in value:
            return []
        words = re.findall(r"[A-Za-z0-9]+", value)
        if len(words) < 2 or len(words) > 8:
            return []

        indexes = list(range(len(words) - 1))
        center = (len(words) - 2) / 2
        indexes.sort(key=lambda index: abs(index - center))

        candidates: list[str] = []
        for index in indexes[:4]:
            joined = (
                words[:index]
                + [words[index] + words[index + 1]]
                + words[index + 2:]
            )
            candidate = " ".join(joined)
            if candidate and candidate != value and candidate not in candidates:
                candidates.append(candidate)
        return candidates

    def search_series(self, query: str) -> list[dict]:
        payload = self._get("/search", {"query": query, "type": "series"})
        return payload.get("data") or []

    def movie_by_slug(self, slug: str) -> dict:
        """Resolve one movie directly through TVDB's canonical slug endpoint."""
        cleaned = slug.strip().strip("/")
        if not cleaned:
            return {}
        payload = self._get(
            f"/movies/slug/{quote(cleaned, safe='-')}", allow_not_found=True,
        )
        record = payload.get("data") or {}
        if not record:
            return {}
        if record.get("id") and not record.get("tvdb_id"):
            record["tvdb_id"] = record["id"]
        record.setdefault("type", "movie")
        if not record.get("image_url") and record.get("image"):
            record["image_url"] = record["image"]
        return record

    def movie_id_from_reference(self, reference: str) -> int:
        """Resolve a numeric TVDB movie ID or canonical TVDB movie-page link."""
        value = reference.strip()
        if not value:
            raise ValueError("Paste a TVDB movie link or numeric movie ID")
        if value.isdigit():
            return int(value)

        candidate_url = value if "://" in value else f"https://{value}"
        parsed = urlparse(candidate_url)
        if (parsed.hostname or "").casefold() not in {"thetvdb.com", "www.thetvdb.com"}:
            raise ValueError("That is not a TheTVDB movie link")
        parts = [part for part in parsed.path.split("/") if part]
        lowered = [part.casefold() for part in parts]
        if "movies" not in lowered:
            raise ValueError("That TVDB link is not a movie page")
        movie_index = lowered.index("movies")
        if movie_index + 1 >= len(parts):
            raise ValueError("The TVDB movie link is incomplete")
        identifier = parts[movie_index + 1].strip()
        if identifier.isdigit():
            return int(identifier)

        record = self.movie_by_slug(identifier)
        movie_id = record.get("tvdb_id") or record.get("id")
        if not str(movie_id or "").isdigit():
            raise ValueError("That TVDB movie page could not be resolved through the API")
        return int(movie_id)

    def search_movies(self, query: str, year: int | None = None) -> list[dict]:
        params = {"query": query, "type": "movie"}
        if year:
            params["year"] = year
        payload = self._get("/search", params)
        results = payload.get("data") or []
        if results:
            best_strict_score = max(
                (self._movie_result_similarity(query, result) for result in results),
                default=0.0,
            )
            if best_strict_score >= MOVIE_SEARCH_PLAUSIBLE_SCORE:
                return results

            # A non-empty provider response is not necessarily a useful response.
            # When every strict hit is clearly unrelated, retry a few conservative
            # adjacent-word compounds. This recovers titles such as
            # ``Run Fat Boy Run`` -> ``Run FatBoy Run`` without adding requests to
            # normal successful searches.
            for compound_query in self._compound_query_candidates(query):
                compound_params = {"query": compound_query, "type": "movie"}
                if year:
                    compound_params["year"] = year
                compound_results = (
                    self._get("/search", compound_params).get("data") or []
                )
                plausible_results = [
                    result for result in compound_results
                    if self._movie_result_similarity(query, result)
                    >= MOVIE_SEARCH_PLAUSIBLE_SCORE
                ]
                if plausible_results:
                    for result in plausible_results:
                        result.setdefault("_search_query", compound_query)
                        result["_possible_match"] = True
                    return plausible_results + results
            return results

        # Recover a narrow class of titles damaged by the pre-0.8.1 scanner's
        # period-as-separator cleanup. For example, "Jackass 3 5" is retried as
        # "Jackass 3.5" after the strict provider lookup fails. Mark every hit as
        # a possible match so punctuation-normalized confidence cannot auto-apply it.
        decimal_query = self._decimal_query_candidate(query)
        if decimal_query:
            decimal_params = {"query": decimal_query, "type": "movie"}
            if year:
                decimal_params["year"] = year
            decimal_results = self._get("/search", decimal_params).get("data") or []
            if decimal_results:
                for result in decimal_results:
                    result.setdefault("_search_query", decimal_query)
                    result["_possible_match"] = True
                return decimal_results

        # TVDB's text-search index can occasionally miss a movie that its website
        # and canonical movie endpoint both know about. On a strict search miss,
        # try the title as a canonical slug before InfoMancer gives up. This keeps
        # the fallback exact and avoids widening every successful provider search.
        slug = self._movie_slug_candidate(decimal_query or query)
        if not slug:
            return []
        record = self.movie_by_slug(slug)
        if not record:
            return []
        record_year_text = str(record.get("year") or "")[:4]
        if year and record_year_text.isdigit() and int(record_year_text) != year:
            return []
        if decimal_query:
            record.setdefault("_search_query", decimal_query)
            record["_possible_match"] = True
        return [record]

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

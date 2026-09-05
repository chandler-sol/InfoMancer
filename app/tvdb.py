from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata
from urllib.parse import quote, urlparse

import httpx


BASE_URL = "https://api4.thetvdb.com/v4"
SEARCH_PLAUSIBLE_SCORE = 0.65
SEARCH_IDENTITY_ENRICH_LIMIT = 8

# Backward-compatible names used by existing tests and callers.
MOVIE_SEARCH_PLAUSIBLE_SCORE = SEARCH_PLAUSIBLE_SCORE
MOVIE_SEARCH_ENRICH_LIMIT = SEARCH_IDENTITY_ENRICH_LIMIT


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
    def _dedupe_names(names: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for name in names:
            value = str(name or "").strip()
            if not value:
                continue
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(value)
        return deduped

    @classmethod
    def _attach_identity_names(cls, record: dict, *names: str) -> dict:
        """Preserve provider-native and translated names as equal identity evidence."""
        existing = []
        for alias in record.get("aliases") or []:
            if isinstance(alias, str):
                existing.append(alias)
            elif isinstance(alias, dict):
                existing.append(str(alias.get("name") or alias.get("title") or ""))
        identity_names = cls._dedupe_names([
            *record.get("_identity_names", []),
            str(record.get("_default_name") or ""),
            str(record.get("name") or ""),
            str(record.get("title") or ""),
            *existing,
            *names,
        ])
        record["_identity_names"] = identity_names
        # Keep aliases populated because InfoMancer's shared confidence scorer already
        # treats aliases as first-class title evidence.
        record["aliases"] = identity_names
        return record

    @classmethod
    def _apply_english_translation(cls, record: dict, translation: dict) -> dict:
        """Prefer English display text while retaining every known identity title."""
        original_name = str(record.get("name") or record.get("title") or "").strip()
        record["_english_translation"] = translation
        if original_name:
            record.setdefault("_default_name", original_name)
        translated_name = str(translation.get("name") or "").strip()
        cls._attach_identity_names(record, original_name, translated_name)
        if translated_name:
            record["name"] = translated_name
        if translation.get("overview"):
            record["overview"] = translation["overview"]
        return record

    @staticmethod
    def _slug_candidate(query: str) -> str:
        value = query.strip()
        if not value or "://" in value or "/" in value:
            return ""
        ascii_value = unicodedata.normalize("NFKD", value).encode(
            "ascii", "ignore"
        ).decode("ascii")
        return re.sub(r"[^a-z0-9]+", "-", ascii_value.casefold()).strip("-")

    @staticmethod
    def _decimal_query_candidate(query: str) -> str:
        """Recover one likely decimal title damaged by old filename cleanup."""
        value = query.strip()
        match = re.search(r"(?<!\d)(\d{1,2})\s+(\d)(?!\d)\s*$", value)
        if not match:
            return ""
        return f"{value[:match.start()]}{match.group(1)}.{match.group(2)}"

    @staticmethod
    def _identity_text(value: str) -> str:
        """Case-fold title text while preserving non-Latin letters and digits."""
        decomposed = unicodedata.normalize("NFKD", str(value or "")).casefold()
        return "".join(
            character for character in decomposed
            if character.isalnum() or character.isspace()
        )

    @classmethod
    def _compact_title(cls, value: str) -> str:
        return "".join(
            character for character in cls._identity_text(value)
            if character.isalnum()
        )

    @classmethod
    def _title_words(cls, value: str) -> list[str]:
        return re.findall(r"[^\W_]+", cls._identity_text(value), flags=re.UNICODE)

    @classmethod
    def _article_free_title(cls, value: str) -> str:
        words = cls._title_words(value)
        if words and words[0] in {"a", "an", "the"}:
            words = words[1:]
        return "".join(words)

    # Compatibility wrappers retained for current tests/callers.
    _movie_slug_candidate = _slug_candidate
    _compact_movie_title = _compact_title
    _movie_title_words = _title_words
    _article_free_movie_title = _article_free_title

    @classmethod
    def _movie_slug_candidates(cls, query: str) -> list[str]:
        original = cls._slug_candidate(query)
        if not original:
            return []
        candidates = [original]
        words = cls._title_words(query)
        if words and words[0] not in {"a", "an", "the"}:
            with_article = cls._slug_candidate(f"the {query}")
            if with_article and with_article not in candidates:
                candidates.append(with_article)
        return candidates

    @classmethod
    def _result_names(cls, result: dict) -> list[str]:
        names: list[str] = []
        for key in ("name", "title", "_default_name"):
            if result.get(key):
                names.append(str(result[key]))
        names.extend(str(value) for value in result.get("_identity_names") or [])
        translation = result.get("_english_translation") or {}
        if isinstance(translation, dict) and translation.get("name"):
            names.append(str(translation["name"]))
        for alias in result.get("aliases") or []:
            if isinstance(alias, str):
                names.append(alias)
            elif isinstance(alias, dict):
                for key in ("name", "title"):
                    if alias.get(key):
                        names.append(str(alias[key]))
        translations = result.get("translations") or []
        if isinstance(translations, dict):
            translations = translations.values()
        for item in translations:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                for key in ("name", "title"):
                    if item.get(key):
                        names.append(str(item[key]))
        return cls._dedupe_names(names)

    _movie_result_names = _result_names

    @classmethod
    def _result_similarity(cls, query: str, result: dict) -> float:
        expected_forms = {
            value for value in (
                cls._compact_title(query),
                cls._article_free_title(query),
            ) if value
        }
        if not expected_forms:
            return 0.0

        best = 0.0
        for name in cls._result_names(result):
            offered_forms = {
                value for value in (
                    cls._compact_title(name),
                    cls._article_free_title(name),
                ) if value
            }
            if expected_forms & offered_forms:
                return 1.0
            for expected_form in expected_forms:
                for offered_form in offered_forms:
                    best = max(
                        best,
                        SequenceMatcher(None, expected_form, offered_form).ratio(),
                    )
        return best

    _movie_result_similarity = _result_similarity

    @staticmethod
    def _result_year(result: dict) -> int | None:
        for key in ("year", "releaseYear", "firstAired", "release_date"):
            value = str(result.get(key) or "")
            match = re.search(r"\b((?:19|20)\d{2})\b", value)
            if match:
                return int(match.group(1))
        return None

    _movie_result_year = _result_year

    @classmethod
    def _year_compatible(
        cls, result: dict, year: int | None, *, tolerance: int = 1,
    ) -> bool:
        if not year:
            return True
        candidate_year = cls._result_year(result)
        if candidate_year is None:
            return True
        return abs(candidate_year - year) <= tolerance

    _movie_year_compatible = _year_compatible

    @classmethod
    def _rank_results(
        cls, query: str, year: int | None, results: list[dict],
    ) -> list[dict]:
        ranked: list[tuple[tuple[float, float, int, int, int], dict]] = []
        for index, result in enumerate(results):
            similarity = cls._result_similarity(query, result)
            candidate_year = cls._result_year(result)
            year_exact = int(bool(year and candidate_year == year))
            year_near = int(bool(
                year and candidate_year is not None
                and abs(candidate_year - year) == 1
            ))
            exact_title = float(similarity >= 0.999999)
            ranked.append((
                (exact_title, similarity, year_exact, year_near, -index), result
            ))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [result for _, result in ranked]

    _rank_movie_results = _rank_results

    @staticmethod
    def _result_key(result: dict) -> tuple[str, str, str]:
        entity_id = str(result.get("tvdb_id") or result.get("id") or "").strip()
        if entity_id:
            return ("id", entity_id, "")
        return (
            "title",
            str(result.get("name") or result.get("title") or "").casefold(),
            str(TVDBClient._result_year(result) or ""),
        )

    _movie_result_key = _result_key

    @classmethod
    def _merge_results(cls, *groups: list[dict]) -> list[dict]:
        merged: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for group in groups:
            for result in group:
                key = cls._result_key(result)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(result)
        return merged

    _merge_movie_results = _merge_results

    @staticmethod
    def _compound_query_candidates(query: str) -> list[str]:
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

    def _enrich_identity_results(
        self, entity: str, query: str, year: int | None, results: list[dict],
    ) -> list[dict]:
        """Hydrate ambiguous provider hits with official English identity data.

        This is the shared identity layer for movie and series search. Provider
        aliases, original titles, translated titles, and the display name all remain
        equivalent evidence. Translation lookups are bounded and only spent while the
        provider's existing names fail to explain the user's query.
        """
        if entity not in {"movie", "series"}:
            raise ValueError("Identity entity must be series or movie")
        ranked = self._rank_results(query, year, [dict(item) for item in results])
        if not ranked:
            return []

        enriched: list[dict] = []
        translation_lookups = 0
        exact_found = False
        for candidate in ranked:
            self._attach_identity_names(candidate)
            current_score = self._result_similarity(query, candidate)
            if (
                not exact_found
                and current_score < 0.999999
                and self._year_compatible(candidate, year)
                and translation_lookups < SEARCH_IDENTITY_ENRICH_LIMIT
            ):
                entity_id = candidate.get("tvdb_id") or candidate.get("id")
                try:
                    numeric_id = int(entity_id)
                except (TypeError, ValueError):
                    numeric_id = 0
                if numeric_id:
                    translation_lookups += 1
                    try:
                        translation = self.translation(entity, numeric_id, "eng")
                    except TVDBError:
                        translation = {}
                    if translation.get("name"):
                        before = current_score
                        self._apply_english_translation(candidate, translation)
                        after = self._result_similarity(query, candidate)
                        if after > before:
                            candidate["_identity_match_source"] = "translation"
                            candidate["_identity_match_name"] = translation["name"]
                        current_score = after

            candidate_year = self._result_year(candidate)
            if (
                entity == "movie" and year and candidate_year is not None
                and abs(candidate_year - year) == 1
                and current_score >= SEARCH_PLAUSIBLE_SCORE
            ):
                candidate["_possible_match"] = True
                candidate.setdefault("_search_query", query)

            enriched.append(candidate)
            if current_score >= 0.999999 and self._year_compatible(candidate, year):
                exact_found = True

        return self._rank_results(query, year, enriched)

    def _prepare_search_results(
        self, entity: str, query: str, results: list[dict],
        year: int | None = None,
    ) -> list[dict]:
        """Normalize every TVDB search result set through one identity pipeline."""
        return self._enrich_identity_results(entity, query, year, results)

    def _english_enrich_movie_results(
        self, query: str, year: int | None, results: list[dict],
    ) -> list[dict]:
        """Compatibility wrapper for the pre-global movie-only enrichment helper."""
        return self._enrich_identity_results("movie", query, year, results)

    def search_series(self, query: str) -> list[dict]:
        payload = self._get("/search", {"query": query, "type": "series"})
        return self._prepare_search_results(
            "series", query, payload.get("data") or [],
        )

    def movie_by_slug(self, slug: str) -> dict:
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
        self._attach_identity_names(record)
        return record

    def _movie_by_query_slug(
        self, query: str, year: int | None = None,
    ) -> dict:
        slug_candidates = self._movie_slug_candidates(query)
        if not slug_candidates:
            return {}
        original_slug = slug_candidates[0]
        for slug in slug_candidates:
            record = self.movie_by_slug(slug)
            if not record:
                continue
            if self._result_similarity(query, record) < SEARCH_PLAUSIBLE_SCORE:
                continue

            inferred_article = slug != original_slug
            record_year = self._result_year(record)
            if year and record_year is not None:
                year_delta = abs(record_year - year)
                if year_delta > 1:
                    continue
                if year_delta == 1:
                    record["_possible_match"] = True
                    record.setdefault("_search_query", query)
            if inferred_article:
                record["_possible_match"] = True
                record.setdefault("_search_query", query)
            return record
        return {}

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

    def _movie_search(
        self, query: str, year: int | None = None,
    ) -> list[dict]:
        params = {"query": query, "type": "movie"}
        if year:
            params["year"] = year
        payload = self._get("/search", params)
        return self._prepare_search_results(
            "movie", query, payload.get("data") or [], year,
        )

    def search_movies(self, query: str, year: int | None = None) -> list[dict]:
        results = self._movie_search(query, year)
        if results:
            best_score = self._result_similarity(query, results[0])
            if best_score >= 0.999999:
                return results

            slug_record = self._movie_by_query_slug(query, year)
            if slug_record:
                slug_id = str(slug_record.get("tvdb_id") or slug_record.get("id") or "")
                remaining = [
                    result for result in results
                    if str(result.get("tvdb_id") or result.get("id") or "") != slug_id
                ]
                return [slug_record, *remaining]

            if best_score >= SEARCH_PLAUSIBLE_SCORE:
                return results

            if year:
                relaxed = self._movie_search(query)
                plausible_relaxed = [
                    result for result in relaxed
                    if self._result_similarity(query, result) >= SEARCH_PLAUSIBLE_SCORE
                    and self._year_compatible(result, year)
                ]
                if plausible_relaxed:
                    for result in plausible_relaxed:
                        candidate_year = self._result_year(result)
                        if candidate_year != year:
                            result["_possible_match"] = True
                            result.setdefault("_search_query", query)
                    return self._merge_results(plausible_relaxed, results)
                results = self._merge_results(relaxed, results)

            for compound_query in self._compound_query_candidates(query):
                compound_params = {"query": compound_query, "type": "movie"}
                if year:
                    compound_params["year"] = year
                payload = self._get("/search", compound_params)
                compound_results = self._prepare_search_results(
                    "movie", query, payload.get("data") or [], year,
                )
                plausible_results = [
                    result for result in compound_results
                    if self._result_similarity(query, result) >= SEARCH_PLAUSIBLE_SCORE
                    and self._year_compatible(result, year)
                ]
                if plausible_results:
                    for result in plausible_results:
                        result.setdefault("_search_query", compound_query)
                        result["_possible_match"] = True
                    return plausible_results + results
            return results

        decimal_query = self._decimal_query_candidate(query)
        if decimal_query:
            decimal_params = {"query": decimal_query, "type": "movie"}
            if year:
                decimal_params["year"] = year
            payload = self._get("/search", decimal_params)
            decimal_results = self._prepare_search_results(
                "movie", query, payload.get("data") or [], year,
            )
            if decimal_results:
                for result in decimal_results:
                    result.setdefault("_search_query", decimal_query)
                    result["_possible_match"] = True
                return decimal_results

        if year:
            relaxed = self._movie_search(query)
            plausible_relaxed = [
                result for result in relaxed
                if self._result_similarity(query, result) >= SEARCH_PLAUSIBLE_SCORE
                and self._year_compatible(result, year)
            ]
            if plausible_relaxed:
                for result in plausible_relaxed:
                    if self._result_year(result) != year:
                        result["_possible_match"] = True
                        result.setdefault("_search_query", query)
                return plausible_relaxed

        slug_query = decimal_query or query
        record = self._movie_by_query_slug(slug_query, year)
        if not record:
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
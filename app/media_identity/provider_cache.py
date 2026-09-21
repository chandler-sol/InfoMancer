from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from ..db import Database
from .tvdb_orders import (
    TVDB_ORDER_MAX_EPISODES,
    TVDB_ORDER_MAX_PAGES,
    TVDB_ORDER_RESPONSE_MAX_BYTES,
    TVDBOrderTransport,
    episode_orders,
    episodes_for_order,
)


class ProviderEpisodeRefreshError(RuntimeError):
    """Raised when a provider refresh is not safe to commit as a complete snapshot."""


@dataclass(frozen=True)
class ProviderEpisodeLimits:
    max_orders: int = 32
    max_records: int = 25_000
    max_identities: int = 10_000
    max_mappings: int = 30_000
    max_text_chars: int = 16_000_000
    max_episodes_per_order: int = TVDB_ORDER_MAX_EPISODES
    max_pages_per_order: int = TVDB_ORDER_MAX_PAGES
    max_response_bytes: int = TVDB_ORDER_RESPONSE_MAX_BYTES


@dataclass(frozen=True)
class ProviderEpisodeRefresh:
    provider: str
    provider_series_id: str
    language: str
    source_signature: str
    episode_count: int
    mapping_count: int
    order_namespaces: tuple[str, ...]


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _first_present(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
    return None


class ProviderEpisodeCache:
    """Store provider episode identity separately from numbering/order mappings."""

    def __init__(
        self, database: Database, *, limits: ProviderEpisodeLimits | None = None,
    ):
        self.database = database
        self.limits = limits or ProviderEpisodeLimits()
        for field in self.limits.__dataclass_fields__:
            if int(getattr(self.limits, field)) <= 0:
                raise ValueError("Episode Identity provider-cache limits must be positive")

    @staticmethod
    def _coordinate_key(
        season: int | None, episode: int | None, absolute_number: int | None,
    ) -> str:
        return _canonical_json([season, episode, absolute_number])

    @staticmethod
    def _identity_metadata(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "runtime": record.get("runtime"),
            "year": record.get("year"),
            "finale_type": _first_present(record, "finaleType", "finale_type"),
            "season_name": _first_present(record, "seasonName", "season_name"),
        }

    @staticmethod
    def _mapping_details(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "aired": _clean_text(record.get("aired")),
            "season_name": _clean_text(
                _first_present(record, "seasonName", "season_name")
            ),
        }

    def refresh_tvdb_title(
        self, title_id: int, client: TVDBOrderTransport, language: str = "eng",
    ) -> ProviderEpisodeRefresh:
        """Refresh the provider cache for one already-matched local TV title."""
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT kind,tvdb_id FROM titles WHERE id=?", (int(title_id),)
            ).fetchone()
        if not row or row["kind"] != "tv":
            raise ValueError("Episode identity provider cache requires a TV title.")
        if row["tvdb_id"] is None:
            raise ValueError("Match this TV title to TVDB before refreshing episode identity data.")
        return self.refresh_tvdb_series(int(row["tvdb_id"]), client, language)

    def refresh_tvdb_series(
        self, series_id: int, client: TVDBOrderTransport, language: str = "eng",
    ) -> ProviderEpisodeRefresh:
        """Fetch a complete TVDB order snapshot before atomically replacing cache rows."""
        provider_series_id = str(int(series_id))
        language = _clean_text(language).casefold() or "eng"
        order_info = episode_orders(
            client,
            series_id,
            max_response_bytes=self.limits.max_response_bytes,
        )
        orders = list(order_info.get("orders") or [])
        if len(orders) > self.limits.max_orders:
            raise ProviderEpisodeRefreshError(
                "TVDB advertised too many episode-order namespaces for a safe refresh."
            )
        if not orders:
            orders = [{"namespace": "default", "name": "Default", "default": True}]

        identities: dict[str, dict[str, Any]] = {}
        mappings: list[dict[str, Any]] = []
        seen_mappings: set[tuple[str, str, str]] = set()
        order_summaries: list[dict[str, Any]] = []
        total_records = 0
        total_text_chars = 0

        for order in orders:
            namespace = _clean_text(order.get("namespace")).casefold()
            if not namespace:
                continue
            order_name = _clean_text(order.get("name")) or namespace
            episodes = episodes_for_order(
                client,
                series_id,
                namespace,
                language=language,
                max_response_bytes=self.limits.max_response_bytes,
                max_pages=self.limits.max_pages_per_order,
                max_episodes=self.limits.max_episodes_per_order,
            )
            total_records += len(episodes)
            if total_records > self.limits.max_records:
                raise ProviderEpisodeRefreshError(
                    "TVDB episode metadata exceeded the Episode Identity aggregate record limit."
                )
            order_summaries.append({
                "namespace": namespace,
                "name": order_name,
                "default": bool(order.get("default")),
                "episode_count": len(episodes),
            })
            for record in episodes:
                total_text_chars += sum(
                    len(_clean_text(value))
                    for value in (
                        record.get("name"),
                        record.get("overview"),
                        record.get("aired"),
                        _first_present(record, "seasonName", "season_name"),
                    )
                )
                if total_text_chars > self.limits.max_text_chars:
                    raise ProviderEpisodeRefreshError(
                        "TVDB episode metadata exceeded the Episode Identity text budget."
                    )

                provider_episode_id = _clean_text(record.get("id"))
                if not provider_episode_id:
                    continue
                absolute_number = _optional_int(
                    _first_present(record, "absoluteNumber", "absolute_number")
                )
                existing = identities.get(provider_episode_id)
                if existing is None and len(identities) >= self.limits.max_identities:
                    raise ProviderEpisodeRefreshError(
                        "TVDB episode metadata exceeded the Episode Identity identity limit."
                    )
                current = {
                    "provider_episode_id": provider_episode_id,
                    "name": _clean_text(record.get("name")),
                    "overview": _clean_text(record.get("overview")),
                    "aired": _clean_text(record.get("aired")),
                    "metadata": self._identity_metadata(record),
                }
                if existing is None:
                    identities[provider_episode_id] = current
                else:
                    for field in ("name", "overview", "aired"):
                        if existing.get(field) in {None, ""} and current.get(field) not in {None, ""}:
                            existing[field] = current[field]
                    for key, value in current["metadata"].items():
                        if existing["metadata"].get(key) in {None, ""} and value not in {None, ""}:
                            existing["metadata"][key] = value

                season = _optional_int(
                    _first_present(record, "seasonNumber", "season_number")
                )
                episode = _optional_int(record.get("number"))
                if season is None and episode is None and absolute_number is None:
                    continue
                coordinate_key = self._coordinate_key(season, episode, absolute_number)
                dedupe_key = (provider_episode_id, namespace, coordinate_key)
                if dedupe_key in seen_mappings:
                    continue
                if len(mappings) >= self.limits.max_mappings:
                    raise ProviderEpisodeRefreshError(
                        "TVDB episode metadata exceeded the Episode Identity mapping limit."
                    )
                seen_mappings.add(dedupe_key)
                mappings.append({
                    "provider_episode_id": provider_episode_id,
                    "order_namespace": namespace,
                    "order_name": order_name,
                    "season": season,
                    "episode": episode,
                    "absolute_number": absolute_number,
                    "coordinate_key": coordinate_key,
                    "details": self._mapping_details(record),
                })

        normalized_identities = sorted(
            identities.values(), key=lambda item: item["provider_episode_id"]
        )
        mappings.sort(key=lambda item: (
            item["order_namespace"],
            item["season"] if item["season"] is not None else -1,
            item["episode"] if item["episode"] is not None else -1,
            item["absolute_number"] if item["absolute_number"] is not None else -1,
            item["provider_episode_id"],
        ))
        order_summaries.sort(key=lambda item: (not item["default"], item["namespace"]))
        signature_payload = {
            "provider": "tvdb",
            "provider_series_id": provider_series_id,
            "language": language,
            "provider_updated_at": _clean_text(order_info.get("provider_updated_at")),
            "orders": order_summaries,
            "identities": normalized_identities,
            "mappings": mappings,
        }
        source_signature = hashlib.sha256(
            _canonical_json(signature_payload).encode("utf-8")
        ).hexdigest()

        with self.database.connect() as conn:
            previous = conn.execute(
                """SELECT episode_count FROM provider_episode_series_cache
                   WHERE provider='tvdb' AND provider_series_id=? AND language=?""",
                (provider_series_id, language),
            ).fetchone()
            if not normalized_identities and previous and int(previous["episode_count"] or 0) > 0:
                raise ProviderEpisodeRefreshError(
                    "TVDB returned an empty series snapshot; preserving the previous non-empty cache."
                )
            conn.execute(
                """DELETE FROM provider_episode_series_cache
                   WHERE provider='tvdb' AND provider_series_id=? AND language=?""",
                (provider_series_id, language),
            )
            conn.execute(
                """INSERT INTO provider_episode_series_cache(
                     provider,provider_series_id,language,provider_updated_at,
                     source_signature,episode_count,mapping_count,order_namespaces_json
                   ) VALUES ('tvdb',?,?,?,?,?,?,?)""",
                (
                    provider_series_id,
                    language,
                    _clean_text(order_info.get("provider_updated_at")),
                    source_signature,
                    len(normalized_identities),
                    len(mappings),
                    _canonical_json(order_summaries),
                ),
            )
            conn.executemany(
                """INSERT INTO provider_episode_identities(
                     provider,provider_series_id,provider_episode_id,language,
                     name,overview,aired,metadata_json
                   ) VALUES ('tvdb',?,?,?,?,?,?,?)""",
                [
                    (
                        provider_series_id,
                        identity["provider_episode_id"],
                        language,
                        identity["name"],
                        identity["overview"],
                        identity["aired"],
                        _canonical_json(identity["metadata"]),
                    )
                    for identity in normalized_identities
                ],
            )
            conn.executemany(
                """INSERT INTO provider_episode_mappings(
                     provider,provider_series_id,provider_episode_id,language,
                     order_namespace,order_name,season,episode,absolute_number,
                     coordinate_key,details_json
                   ) VALUES ('tvdb',?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        provider_series_id,
                        mapping["provider_episode_id"],
                        language,
                        mapping["order_namespace"],
                        mapping["order_name"],
                        mapping["season"],
                        mapping["episode"],
                        mapping["absolute_number"],
                        mapping["coordinate_key"],
                        _canonical_json(mapping["details"]),
                    )
                    for mapping in mappings
                ],
            )

        return ProviderEpisodeRefresh(
            provider="tvdb",
            provider_series_id=provider_series_id,
            language=language,
            source_signature=source_signature,
            episode_count=len(normalized_identities),
            mapping_count=len(mappings),
            order_namespaces=tuple(item["namespace"] for item in order_summaries),
        )

    def cache_status(
        self, provider: str, provider_series_id: str, language: str = "eng",
    ) -> dict[str, Any] | None:
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT * FROM provider_episode_series_cache
                   WHERE provider=? AND provider_series_id=? AND language=?""",
                (provider.casefold(), str(provider_series_id), language.casefold()),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["order_namespaces"] = json.loads(result.pop("order_namespaces_json") or "[]")
        return result

    def mappings_for_episode(
        self, provider: str, provider_series_id: str, provider_episode_id: str,
        language: str = "eng",
    ) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT order_namespace,order_name,season,episode,absolute_number,
                          coordinate_key,details_json
                   FROM provider_episode_mappings
                   WHERE provider=? AND provider_series_id=? AND provider_episode_id=?
                     AND language=?
                   ORDER BY order_namespace,season,episode,absolute_number,id""",
                (
                    provider.casefold(), str(provider_series_id),
                    str(provider_episode_id), language.casefold(),
                ),
            ).fetchall()
        mappings = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json") or "{}")
            mappings.append(item)
        return mappings

    def resolve_coordinate(
        self, provider: str, provider_series_id: str, order_namespace: str,
        season: int | None, episode: int | None, language: str = "eng", *,
        absolute_number: int | None = None,
    ) -> dict[str, Any]:
        """Return every identity at one full coordinate; never guess through ambiguity."""
        if season is None and episode is None and absolute_number is None:
            raise ValueError(
                "Provider coordinate lookup requires season/episode or an absolute number."
            )

        query = """SELECT i.provider_episode_id,i.name,i.overview,i.aired,
                          m.order_name,m.season,m.episode,
                          m.absolute_number mapping_absolute_number,m.coordinate_key
                   FROM provider_episode_mappings m
                   JOIN provider_episode_identities i
                     ON i.provider=m.provider
                    AND i.provider_series_id=m.provider_series_id
                    AND i.provider_episode_id=m.provider_episode_id
                    AND i.language=m.language
                   WHERE m.provider=? AND m.provider_series_id=? AND m.language=?
                     AND m.order_namespace=? AND m.season IS ? AND m.episode IS ?"""
        parameters: list[Any] = [
            provider.casefold(), str(provider_series_id), language.casefold(),
            order_namespace.casefold(), season, episode,
        ]
        if absolute_number is not None:
            query += " AND m.absolute_number IS ?"
            parameters.append(absolute_number)
        query += " ORDER BY i.provider_episode_id,m.id"

        with self.database.connect() as conn:
            rows = conn.execute(query, parameters).fetchall()
        candidates_by_id: dict[str, dict[str, Any]] = {}
        mapping_variants: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            item = dict(row)
            provider_episode_id = item["provider_episode_id"]
            mapping_variant = {
                "order_name": item["order_name"],
                "season": item["season"],
                "episode": item["episode"],
                "absolute_number": item["mapping_absolute_number"],
                "coordinate_key": item["coordinate_key"],
            }
            mapping_variants.setdefault(provider_episode_id, []).append(mapping_variant)
            if provider_episode_id not in candidates_by_id:
                candidates_by_id[provider_episode_id] = {
                    "provider_episode_id": provider_episode_id,
                    "name": item["name"],
                    "overview": item["overview"],
                    "aired": item["aired"],
                }
        candidates = []
        for provider_episode_id, candidate in candidates_by_id.items():
            candidate["mapping_variants"] = mapping_variants[provider_episode_id]
            candidates.append(candidate)
        return {"candidates": candidates, "ambiguous": len(candidates) > 1}

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable

from ..db import Database
from ..tvdb import TVDBClient


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


class ProviderEpisodeCache:
    """Store provider episode identity separately from numbering/order mappings."""

    def __init__(self, database: Database):
        self.database = database

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
            "finale_type": record.get("finaleType") or record.get("finale_type"),
            "season_name": record.get("seasonName") or record.get("season_name"),
        }

    @staticmethod
    def _mapping_details(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "aired": _clean_text(record.get("aired")),
            "season_name": _clean_text(
                record.get("seasonName") or record.get("season_name")
            ),
        }

    def refresh_tvdb_series(
        self, series_id: int, client: TVDBClient, language: str = "eng",
    ) -> ProviderEpisodeRefresh:
        """Fetch a complete TVDB order snapshot before atomically replacing cache rows."""
        provider_series_id = str(int(series_id))
        language = _clean_text(language).casefold() or "eng"
        order_info = client.episode_orders(series_id)
        orders = list(order_info.get("orders") or [])
        if not orders:
            orders = [{"namespace": "default", "name": "Default", "default": True}]

        identities: dict[str, dict[str, Any]] = {}
        mappings: list[dict[str, Any]] = []
        seen_mappings: set[tuple[str, str, str]] = set()
        order_summaries: list[dict[str, Any]] = []

        for order in orders:
            namespace = _clean_text(order.get("namespace")).casefold()
            if not namespace:
                continue
            order_name = _clean_text(order.get("name")) or namespace
            episodes = client.episodes_for_order(
                series_id, namespace, language=language,
            )
            order_summaries.append({
                "namespace": namespace,
                "name": order_name,
                "default": bool(order.get("default")),
                "episode_count": len(episodes),
            })
            for record in episodes:
                provider_episode_id = _clean_text(record.get("id"))
                if not provider_episode_id:
                    continue
                existing = identities.get(provider_episode_id)
                current = {
                    "provider_episode_id": provider_episode_id,
                    "name": _clean_text(record.get("name")),
                    "overview": _clean_text(record.get("overview")),
                    "aired": _clean_text(record.get("aired")),
                    "absolute_number": _optional_int(
                        record.get("absoluteNumber") or record.get("absolute_number")
                    ),
                    "metadata": self._identity_metadata(record),
                }
                if existing is None:
                    identities[provider_episode_id] = current
                else:
                    for field in ("name", "overview", "aired", "absolute_number"):
                        if existing.get(field) in {None, ""} and current.get(field) not in {None, ""}:
                            existing[field] = current[field]
                    for key, value in current["metadata"].items():
                        if existing["metadata"].get(key) in {None, ""} and value not in {None, ""}:
                            existing["metadata"][key] = value

                season = _optional_int(
                    record.get("seasonNumber")
                    if record.get("seasonNumber") is not None
                    else record.get("season_number")
                )
                episode = _optional_int(record.get("number"))
                absolute_number = _optional_int(
                    record.get("absoluteNumber") or record.get("absolute_number")
                )
                if season is None and episode is None and absolute_number is None:
                    continue
                coordinate_key = self._coordinate_key(season, episode, absolute_number)
                dedupe_key = (provider_episode_id, namespace, coordinate_key)
                if dedupe_key in seen_mappings:
                    continue
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
                     name,overview,aired,absolute_number,metadata_json
                   ) VALUES ('tvdb',?,?,?,?,?,?,?,?)""",
                [
                    (
                        provider_series_id,
                        identity["provider_episode_id"],
                        language,
                        identity["name"],
                        identity["overview"],
                        identity["aired"],
                        identity["absolute_number"],
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
        return [
            {
                **dict(row),
                "details": json.loads(row["details_json"] or "{}"),
            }
            for row in rows
        ]

    def resolve_coordinate(
        self, provider: str, provider_series_id: str, order_namespace: str,
        season: int | None, episode: int | None, language: str = "eng",
    ) -> dict[str, Any]:
        """Return every identity at a coordinate; never guess through provider ambiguity."""
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT i.provider_episode_id,i.name,i.overview,i.aired,
                          i.absolute_number,m.order_name,m.season,m.episode,
                          m.absolute_number mapping_absolute_number,m.coordinate_key
                   FROM provider_episode_mappings m
                   JOIN provider_episode_identities i
                     ON i.provider=m.provider
                    AND i.provider_series_id=m.provider_series_id
                    AND i.provider_episode_id=m.provider_episode_id
                    AND i.language=m.language
                   WHERE m.provider=? AND m.provider_series_id=? AND m.language=?
                     AND m.order_namespace=? AND m.season IS ? AND m.episode IS ?
                   ORDER BY i.provider_episode_id""",
                (
                    provider.casefold(), str(provider_series_id), language.casefold(),
                    order_namespace.casefold(), season, episode,
                ),
            ).fetchall()
        candidates = [dict(row) for row in rows]
        return {"candidates": candidates, "ambiguous": len(candidates) > 1}

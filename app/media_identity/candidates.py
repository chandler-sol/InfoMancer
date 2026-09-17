from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from typing import Any

from .models import IdentityCandidate, IdentityReference


MAX_FAST_CANDIDATES = 80
MAX_FAST_SPECIALS = 24


@dataclass(frozen=True)
class CandidateSet:
    candidates: tuple[IdentityCandidate, ...]
    provider_series_id: str
    provider_signature: str
    used_provider_cache: bool


def _mapping_sort_key(mapping: dict[str, Any]) -> tuple:
    return (
        mapping.get("order_namespace") != "default",
        str(mapping.get("order_namespace") or ""),
        mapping.get("season") if mapping.get("season") is not None else -1,
        mapping.get("episode") if mapping.get("episode") is not None else -1,
        mapping.get("absolute_number") if mapping.get("absolute_number") is not None else -1,
    )


def _origin_priority(origins: set[str]) -> int:
    if "claimed_coordinate" in origins:
        return 0
    if "nearby_same_season" in origins:
        return 1
    if "same_season" in origins:
        return 2
    if "special" in origins:
        return 3
    return 9


def _provider_candidates(
    conn: sqlite3.Connection,
    *,
    title_id: int,
    provider_series_id: str,
    season: int,
    episode_start: int,
    episode_end: int,
    include_specials: bool,
    language: str,
) -> CandidateSet:
    status = conn.execute(
        """SELECT source_signature FROM provider_episode_series_cache
           WHERE provider='tvdb' AND provider_series_id=? AND language=?""",
        (provider_series_id, language),
    ).fetchone()
    if not status:
        return CandidateSet((), provider_series_id, "", False)

    rows = conn.execute(
        """SELECT i.provider_episode_id,i.name,i.overview,i.aired,
                  m.order_namespace,m.order_name,m.season,m.episode,m.absolute_number,
                  m.coordinate_key,e.id expected_episode_id
           FROM provider_episode_identities i
           LEFT JOIN provider_episode_mappings m
             ON m.provider=i.provider
            AND m.provider_series_id=i.provider_series_id
            AND m.provider_episode_id=i.provider_episode_id
            AND m.language=i.language
           LEFT JOIN expected_episodes e
             ON e.title_id=? AND CAST(e.tvdb_episode_id AS TEXT)=i.provider_episode_id
           WHERE i.provider='tvdb' AND i.provider_series_id=? AND i.language=?
           ORDER BY i.provider_episode_id,m.order_namespace,m.season,m.episode,m.id""",
        (title_id, provider_series_id, language),
    ).fetchall()

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        provider_episode_id = str(row["provider_episode_id"])
        item = grouped.setdefault(provider_episode_id, {
            "provider_episode_id": provider_episode_id,
            "name": row["name"] or "",
            "overview": row["overview"] or "",
            "aired": row["aired"] or "",
            "expected_episode_id": row["expected_episode_id"],
            "mappings": [],
            "origins": set(),
        })
        if row["order_namespace"] is None:
            continue
        mapping = {
            "order_namespace": row["order_namespace"],
            "order_name": row["order_name"] or "",
            "season": row["season"],
            "episode": row["episode"],
            "absolute_number": row["absolute_number"],
            "coordinate_key": row["coordinate_key"],
        }
        item["mappings"].append(mapping)

        mapped_season = row["season"]
        mapped_episode = row["episode"]
        namespace = row["order_namespace"]
        if (
            mapped_season == season
            and mapped_episode is not None
            and episode_start <= mapped_episode <= episode_end
        ):
            item["origins"].add("claimed_coordinate")
        if namespace == "default" and mapped_season == season and mapped_episode is not None:
            distance = min(
                abs(mapped_episode - episode_start),
                abs(mapped_episode - episode_end),
            )
            if distance <= 2 and not (episode_start <= mapped_episode <= episode_end):
                item["origins"].add("nearby_same_season")
            item["origins"].add("same_season")
        if include_specials and namespace == "default" and mapped_season == 0:
            item["origins"].add("special")

    selected = [item for item in grouped.values() if item["origins"]]
    selected.sort(key=lambda item: (
        _origin_priority(item["origins"]),
        min(
            [
                abs((mapping.get("episode") or episode_start) - episode_start)
                for mapping in item["mappings"]
                if mapping.get("season") == season and mapping.get("episode") is not None
            ] or [10_000]
        ),
        item["provider_episode_id"],
    ))

    if include_specials:
        regular = [item for item in selected if "special" not in item["origins"] or len(item["origins"]) > 1]
        specials = [item for item in selected if item not in regular][:MAX_FAST_SPECIALS]
        selected = regular + specials
    selected = selected[:MAX_FAST_CANDIDATES]

    candidates: list[IdentityCandidate] = []
    for rank, item in enumerate(selected, start=1):
        mappings = sorted(item["mappings"], key=_mapping_sort_key)
        preferred = next(
            (
                mapping for mapping in mappings
                if mapping["season"] == season
                and mapping["episode"] is not None
                and episode_start <= mapping["episode"] <= episode_end
            ),
            next((mapping for mapping in mappings if mapping["order_namespace"] == "default"), None),
        )
        preferred = preferred or (mappings[0] if mappings else {})
        identity = IdentityReference(
            identity_kind="episode",
            provider="tvdb",
            provider_item_id=item["provider_episode_id"],
            expected_episode_id=item["expected_episode_id"],
            order_namespace=str(preferred.get("order_namespace") or ""),
            season=preferred.get("season"),
            episode=preferred.get("episode"),
            display_name=item["name"],
        )
        candidates.append(IdentityCandidate(
            identity=identity,
            rank=rank,
            details={
                "origins": sorted(item["origins"]),
                "mappings": mappings,
                "overview": item["overview"],
                "aired": item["aired"],
            },
        ))

    return CandidateSet(
        candidates=tuple(candidates),
        provider_series_id=provider_series_id,
        provider_signature=str(status["source_signature"] or ""),
        used_provider_cache=True,
    )


def _fallback_candidates(
    conn: sqlite3.Connection,
    *,
    title_id: int,
    season: int,
    episode_start: int,
    episode_end: int,
) -> CandidateSet:
    rows = conn.execute(
        """SELECT id,tvdb_episode_id,season,episode,name,aired
           FROM expected_episodes
           WHERE title_id=? AND season=?
           ORDER BY episode,id LIMIT ?""",
        (title_id, season, MAX_FAST_CANDIDATES),
    ).fetchall()
    candidates: list[IdentityCandidate] = []
    for rank, row in enumerate(rows, start=1):
        origin = (
            "claimed_coordinate"
            if episode_start <= int(row["episode"]) <= episode_end
            else "same_season"
        )
        candidates.append(IdentityCandidate(
            identity=IdentityReference(
                identity_kind="episode",
                provider="tvdb",
                provider_item_id=str(row["tvdb_episode_id"]),
                expected_episode_id=int(row["id"]),
                order_namespace="default",
                season=int(row["season"]),
                episode=int(row["episode"]),
                display_name=row["name"] or "",
            ),
            rank=rank,
            details={"origins": [origin], "mappings": [], "overview": "", "aired": row["aired"] or ""},
        ))
    return CandidateSet(tuple(candidates), "", "", False)


def generate_episode_candidates(
    conn: sqlite3.Connection,
    *,
    title_id: int,
    season: int,
    episode_start: int,
    episode_end: int | None = None,
    include_specials: bool = False,
    language: str = "eng",
) -> CandidateSet:
    """Generate a bounded Fast candidate set without turning numbering into content identity."""
    episode_end = episode_start if episode_end is None else max(episode_start, episode_end)
    title = conn.execute(
        "SELECT kind,tvdb_id FROM titles WHERE id=?", (title_id,)
    ).fetchone()
    if not title or title["kind"] != "tv":
        raise ValueError("Episode Identity candidates require a TV title.")

    if title["tvdb_id"] is not None:
        provider_series_id = str(title["tvdb_id"])
        provider = _provider_candidates(
            conn,
            title_id=title_id,
            provider_series_id=provider_series_id,
            season=season,
            episode_start=episode_start,
            episode_end=episode_end,
            include_specials=include_specials,
            language=language.casefold(),
        )
        if provider.candidates:
            return provider

    return _fallback_candidates(
        conn,
        title_id=title_id,
        season=season,
        episode_start=episode_start,
        episode_end=episode_end,
    )

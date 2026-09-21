from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from typing import Any

from .models import IdentityCandidate, IdentityReference


MAX_FAST_CANDIDATES = 80
MAX_FAST_SPECIALS = 24
MAX_FAST_MAPPINGS_PER_CANDIDATE = 32


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


def _json_object(value: Any) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _provider_candidate_ids(
    conn: sqlite3.Connection,
    *,
    provider_series_id: str,
    season: int,
    episode_start: int,
    episode_end: int,
    include_specials: bool,
    language: str,
) -> list[str]:
    """Select the bounded provider identity set before materializing mappings."""
    regular_rows = conn.execute(
        """SELECT m.provider_episode_id,
                  MIN(
                    CASE
                      WHEN m.season=? AND m.episode BETWEEN ? AND ? THEN 0
                      WHEN m.order_namespace='default' AND m.season=?
                       AND m.episode IS NOT NULL
                       AND MIN(ABS(m.episode-?),ABS(m.episode-?))<=2 THEN 1
                      WHEN m.order_namespace='default' AND m.season=?
                       AND m.episode IS NOT NULL THEN 2
                      ELSE 9
                    END
                  ) priority,
                  MIN(
                    CASE
                      WHEN m.season=? AND m.episode IS NOT NULL
                      THEN MIN(ABS(m.episode-?),ABS(m.episode-?))
                      ELSE 10000
                    END
                  ) distance
           FROM provider_episode_mappings m
           WHERE m.provider='tvdb' AND m.provider_series_id=? AND m.language=?
             AND (
               (m.season=? AND m.episode BETWEEN ? AND ?)
               OR (
                 m.order_namespace='default' AND m.season=?
                 AND m.episode IS NOT NULL
               )
             )
           GROUP BY m.provider_episode_id
           ORDER BY priority,distance,m.provider_episode_id
           LIMIT ?""",
        (
            season,
            episode_start,
            episode_end,
            season,
            episode_start,
            episode_end,
            season,
            season,
            episode_start,
            episode_end,
            provider_series_id,
            language,
            season,
            episode_start,
            episode_end,
            season,
            MAX_FAST_CANDIDATES,
        ),
    ).fetchall()
    regular_ids = [str(row["provider_episode_id"]) for row in regular_rows]

    special_ids: list[str] = []
    if include_specials and season != 0:
        parameters: list[Any] = [provider_series_id, language]
        exclusion = ""
        if regular_ids:
            placeholders = ",".join("?" for _ in regular_ids)
            exclusion = f" AND m.provider_episode_id NOT IN ({placeholders})"
            parameters.extend(regular_ids)
        parameters.append(MAX_FAST_SPECIALS)
        special_rows = conn.execute(
            f"""SELECT m.provider_episode_id
                FROM provider_episode_mappings m
                WHERE m.provider='tvdb' AND m.provider_series_id=?
                  AND m.language=? AND m.order_namespace='default'
                  AND m.season=0{exclusion}
                GROUP BY m.provider_episode_id
                ORDER BY m.provider_episode_id
                LIMIT ?""",
            parameters,
        ).fetchall()
        special_ids = [str(row["provider_episode_id"]) for row in special_rows]

    regular_limit = max(0, MAX_FAST_CANDIDATES - len(special_ids))
    return regular_ids[:regular_limit] + special_ids


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

    selected_ids = _provider_candidate_ids(
        conn,
        provider_series_id=provider_series_id,
        season=season,
        episode_start=episode_start,
        episode_end=episode_end,
        include_specials=include_specials,
        language=language,
    )
    if not selected_ids:
        return CandidateSet(
            (), provider_series_id, str(status["source_signature"] or ""), True
        )

    placeholders = ",".join("?" for _ in selected_ids)
    rows = conn.execute(
        f"""WITH ranked_mappings AS (
              SELECT m.*,
                     ROW_NUMBER() OVER (
                       PARTITION BY m.provider_episode_id
                       ORDER BY
                         CASE
                           WHEN m.season=? AND m.episode BETWEEN ? AND ? THEN 0
                           WHEN m.order_namespace='default' THEN 1
                           ELSE 2
                         END,
                         m.order_namespace,m.season,m.episode,m.absolute_number,m.id
                     ) mapping_rank
              FROM provider_episode_mappings m
              WHERE m.provider='tvdb' AND m.provider_series_id=?
                AND m.language=? AND m.provider_episode_id IN ({placeholders})
            )
            SELECT i.provider_episode_id,i.name,i.overview,i.aired,i.metadata_json,
                   m.order_namespace,m.order_name,m.season,m.episode,m.absolute_number,
                   m.coordinate_key,e.id expected_episode_id
            FROM provider_episode_identities i
            LEFT JOIN ranked_mappings m
              ON m.provider=i.provider
             AND m.provider_series_id=i.provider_series_id
             AND m.provider_episode_id=i.provider_episode_id
             AND m.language=i.language
             AND m.mapping_rank<=?
            LEFT JOIN expected_episodes e
              ON e.title_id=? AND CAST(e.tvdb_episode_id AS TEXT)=i.provider_episode_id
            WHERE i.provider='tvdb' AND i.provider_series_id=? AND i.language=?
              AND i.provider_episode_id IN ({placeholders})
            ORDER BY i.provider_episode_id,m.mapping_rank""",
        (
            season,
            episode_start,
            episode_end,
            provider_series_id,
            language,
            *selected_ids,
            MAX_FAST_MAPPINGS_PER_CANDIDATE,
            title_id,
            provider_series_id,
            language,
            *selected_ids,
        ),
    ).fetchall()

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        provider_episode_id = str(row["provider_episode_id"])
        item = grouped.setdefault(provider_episode_id, {
            "provider_episode_id": provider_episode_id,
            "name": row["name"] or "",
            "overview": row["overview"] or "",
            "aired": row["aired"] or "",
            "metadata": _json_object(row["metadata_json"]),
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

    if include_specials and season != 0:
        regular = [
            item for item in selected
            if "special" not in item["origins"] or len(item["origins"]) > 1
        ]
        specials = [item for item in selected if item not in regular][:MAX_FAST_SPECIALS]
        regular_limit = max(0, MAX_FAST_CANDIDATES - len(specials))
        selected = regular[:regular_limit] + specials
    else:
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
                "metadata": item["metadata"],
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
           ORDER BY
             CASE
               WHEN episode BETWEEN ? AND ? THEN 0
               WHEN MIN(ABS(episode-?),ABS(episode-?))<=2 THEN 1
               ELSE 2
             END,
             MIN(ABS(episode-?),ABS(episode-?)),episode,id
           LIMIT ?""",
        (
            title_id,
            season,
            episode_start,
            episode_end,
            episode_start,
            episode_end,
            episode_start,
            episode_end,
            MAX_FAST_CANDIDATES,
        ),
    ).fetchall()
    prepared: list[tuple[int, int, sqlite3.Row]] = []
    for row in rows:
        episode = int(row["episode"])
        claimed = episode_start <= episode <= episode_end
        distance = min(abs(episode - episode_start), abs(episode - episode_end))
        priority = 0 if claimed else (1 if distance <= 2 else 2)
        prepared.append((priority, distance, row))
    prepared.sort(key=lambda item: (item[0], item[1], int(item[2]["episode"]), int(item[2]["id"])))

    candidates: list[IdentityCandidate] = []
    for rank, (_, distance, row) in enumerate(prepared, start=1):
        episode = int(row["episode"])
        if episode_start <= episode <= episode_end:
            origins = ["claimed_coordinate", "same_season"]
        elif distance <= 2:
            origins = ["nearby_same_season", "same_season"]
        else:
            origins = ["same_season"]
        candidates.append(IdentityCandidate(
            identity=IdentityReference(
                identity_kind="episode",
                provider="tvdb",
                provider_item_id=str(row["tvdb_episode_id"]),
                expected_episode_id=int(row["id"]),
                order_namespace="default",
                season=int(row["season"]),
                episode=episode,
                display_name=row["name"] or "",
            ),
            rank=rank,
            details={
                "origins": origins,
                "mappings": [],
                "overview": "",
                "aired": row["aired"] or "",
                "metadata": {},
            },
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

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from itertools import combinations, islice
import json
import sqlite3
from typing import Any, Mapping

from .candidates import CandidateSet, generate_episode_candidates
from .models import IdentityCandidate
from .versions import DEEP_ORCHESTRATION_VERSION


MAX_DEEP_CANDIDATES = 160
MAX_DEEP_SPECIALS = 24
MAX_DEEP_ADJACENT_SEASON_RADIUS = 2
MAX_DEEP_CORRELATION_FILES = 48
MAX_DEEP_PAIRWISE_COMPARISONS = 256


class DeepIdentityError(ValueError):
    """Raised when a Deep plan would violate its deterministic safety contract."""


def _strict_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DeepIdentityError(f"{name} must be an integer.")
    return value


def _stable_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DeepIdentityError("Deep plan identity must be JSON-safe.") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DeepCandidatePolicy:
    """Bounded permission for widening the per-file candidate search."""

    adjacent_season_radius: int = 1
    include_specials: bool = True
    max_candidates: int = MAX_DEEP_CANDIDATES
    max_specials: int = MAX_DEEP_SPECIALS

    def __post_init__(self) -> None:
        radius = _strict_int(
            self.adjacent_season_radius,
            name="Deep adjacent-season radius",
        )
        max_candidates = _strict_int(
            self.max_candidates,
            name="Deep candidate limit",
        )
        max_specials = _strict_int(
            self.max_specials,
            name="Deep specials limit",
        )
        if not isinstance(self.include_specials, bool):
            raise DeepIdentityError("Deep include-specials must be boolean.")
        if radius < 0 or radius > MAX_DEEP_ADJACENT_SEASON_RADIUS:
            raise DeepIdentityError(
                "Deep adjacent-season radius exceeds the supported bound."
            )
        if max_candidates < 1 or max_candidates > MAX_DEEP_CANDIDATES:
            raise DeepIdentityError(
                "Deep candidate limit exceeds the supported bound."
            )
        if max_specials < 0 or max_specials > MAX_DEEP_SPECIALS:
            raise DeepIdentityError(
                "Deep specials limit exceeds the supported bound."
            )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "adjacent_season_radius": self.adjacent_season_radius,
            "include_specials": self.include_specials,
            "max_candidates": self.max_candidates,
            "max_specials": self.max_specials,
        }

    @classmethod
    def from_payload(cls, value: object) -> "DeepCandidatePolicy":
        if not isinstance(value, Mapping):
            raise DeepIdentityError("Deep candidate policy metadata is missing.")
        try:
            return cls(
                adjacent_season_radius=value["adjacent_season_radius"],
                include_specials=value["include_specials"],
                max_candidates=value["max_candidates"],
                max_specials=value["max_specials"],
            )
        except KeyError as exc:
            raise DeepIdentityError(
                "Deep candidate policy metadata is incomplete."
            ) from exc

    def adjacent_seasons(self, claimed_season: int) -> tuple[int, ...]:
        claimed = _strict_int(claimed_season, name="Claimed season")
        if claimed <= 0 or self.adjacent_season_radius == 0:
            return ()
        seasons: list[int] = []
        for distance in range(1, self.adjacent_season_radius + 1):
            previous = claimed - distance
            following = claimed + distance
            if previous >= 1:
                seasons.append(previous)
            seasons.append(following)
        return tuple(seasons)


@dataclass(frozen=True)
class DeepCandidatePlan:
    candidates: tuple[IdentityCandidate, ...]
    provider_series_id: str
    provider_signature: str
    used_provider_cache: bool
    policy: DeepCandidatePolicy
    plan_signature: str

    def as_candidate_set(self) -> CandidateSet:
        return CandidateSet(
            candidates=self.candidates,
            provider_series_id=self.provider_series_id,
            provider_signature=self.provider_signature,
            used_provider_cache=self.used_provider_cache,
        )


def _candidate_identity(candidate: IdentityCandidate) -> dict[str, Any]:
    identity = candidate.identity
    return {
        "candidate_key": candidate.key,
        "identity_kind": identity.identity_kind,
        "provider": identity.provider,
        "provider_item_id": identity.provider_item_id,
        "expected_episode_id": identity.expected_episode_id,
        "order_namespace": identity.order_namespace,
        "season": identity.season,
        "episode": identity.episode,
        "display_name": identity.display_name,
        "details": dict(candidate.details),
    }


def _with_deep_origin(
    candidate: IdentityCandidate,
    *,
    origin: str,
    rank: int,
) -> IdentityCandidate:
    details = dict(candidate.details)
    prior_origins = details.get("origins")
    origins = (
        {
            str(item)
            for item in prior_origins
            if str(item).strip()
        }
        if isinstance(prior_origins, (list, tuple, set))
        else set()
    )
    origins.difference_update({
        "claimed_coordinate",
        "nearby_same_season",
        "same_season",
        "special",
    })
    origins.add(origin)
    details["origins"] = sorted(origins)
    return replace(candidate, rank=rank, details=details)


def _merge_candidate_source(
    plan_candidates: list[IdentityCandidate],
    seen_keys: set[str],
    candidate_set: CandidateSet,
    *,
    origin: str | None,
    limit: int,
) -> None:
    for candidate in candidate_set.candidates:
        if len(plan_candidates) >= limit:
            return
        if candidate.key in seen_keys:
            continue
        rank = len(plan_candidates) + 1
        prepared = (
            replace(candidate, rank=rank)
            if origin is None
            else _with_deep_origin(candidate, origin=origin, rank=rank)
        )
        plan_candidates.append(prepared)
        seen_keys.add(candidate.key)


def generate_deep_episode_candidates(
    conn: sqlite3.Connection,
    *,
    title_id: int,
    season: int,
    episode_start: int,
    episode_end: int | None = None,
    language: str = "eng",
    policy: DeepCandidatePolicy | None = None,
) -> DeepCandidatePlan:
    """Build a deterministic, bounded candidate set for Deep analysis.

    Fast/Normal candidate semantics are left untouched. Deep starts with the same
    claimed-season candidates, then may add specials and adjacent seasons. Wider
    search remains explicitly bounded and is represented in the plan signature.
    """

    policy = policy or DeepCandidatePolicy()
    title_id = _strict_int(title_id, name="Deep title id")
    season = _strict_int(season, name="Deep claimed season")
    episode_start = _strict_int(
        episode_start,
        name="Deep claimed episode",
    )
    if title_id <= 0 or season < 0 or episode_start < 0:
        raise DeepIdentityError(
            "Deep candidate coordinates must be non-negative and identify a title."
        )
    if episode_end is None:
        episode_end = episode_start
    else:
        episode_end = _strict_int(
            episode_end,
            name="Deep claimed episode end",
        )
        if episode_end < episode_start:
            raise DeepIdentityError(
                "Deep claimed episode range cannot run backward."
            )
    language = str(language or "eng").strip().casefold() or "eng"

    sources: list[CandidateSet] = []
    base = generate_episode_candidates(
        conn,
        title_id=title_id,
        season=season,
        episode_start=episode_start,
        episode_end=episode_end,
        include_specials=False,
        language=language,
    )
    sources.append(base)

    candidates: list[IdentityCandidate] = []
    seen: set[str] = set()
    _merge_candidate_source(
        candidates,
        seen,
        base,
        origin=None,
        limit=policy.max_candidates,
    )

    if (
        len(candidates) < policy.max_candidates
        and season != 0
        and policy.include_specials
        and policy.max_specials > 0
    ):
        specials = generate_episode_candidates(
            conn,
            title_id=title_id,
            season=0,
            episode_start=0,
            episode_end=0,
            include_specials=False,
            language=language,
        )
        sources.append(specials)
        before = len(candidates)
        _merge_candidate_source(
            candidates,
            seen,
            specials,
            origin="deep_special",
            limit=min(
                policy.max_candidates,
                before + policy.max_specials,
            ),
        )

    for adjacent_season in policy.adjacent_seasons(season):
        if len(candidates) >= policy.max_candidates:
            break
        adjacent = generate_episode_candidates(
            conn,
            title_id=title_id,
            season=adjacent_season,
            episode_start=0,
            episode_end=0,
            include_specials=False,
            language=language,
        )
        sources.append(adjacent)
        _merge_candidate_source(
            candidates,
            seen,
            adjacent,
            origin="deep_adjacent_season",
            limit=policy.max_candidates,
        )

    provider_series_ids = {
        item.provider_series_id for item in sources if item.provider_series_id
    }
    provider_signatures = {
        item.provider_signature for item in sources if item.provider_signature
    }
    if len(provider_series_ids) > 1 or len(provider_signatures) > 1:
        raise DeepIdentityError(
            "Provider candidate identity changed while building the Deep plan."
        )

    provider_series_id = next(iter(provider_series_ids), "")
    provider_signature = next(iter(provider_signatures), "")
    used_provider_cache = any(item.used_provider_cache for item in sources)
    payload = {
        "version": DEEP_ORCHESTRATION_VERSION,
        "kind": "episode-candidates",
        "title_id": title_id,
        "claimed": {
            "season": season,
            "episode_start": episode_start,
            "episode_end": episode_end,
        },
        "language": language,
        "policy": {
            "adjacent_season_radius": policy.adjacent_season_radius,
            "include_specials": policy.include_specials,
            "max_candidates": policy.max_candidates,
            "max_specials": policy.max_specials,
        },
        "provider_series_id": provider_series_id,
        "provider_signature": provider_signature,
        "candidates": [_candidate_identity(item) for item in candidates],
    }
    return DeepCandidatePlan(
        candidates=tuple(candidates),
        provider_series_id=provider_series_id,
        provider_signature=provider_signature,
        used_provider_cache=used_provider_cache,
        policy=policy,
        plan_signature=_digest(payload),
    )


@dataclass(frozen=True)
class DeepCorrelationPolicy:
    """Hard limits for cross-file Deep correlation.

    The default keeps sequence/swap correlation inside the claimed season. Candidate
    search may widen farther, but cross-file voting does not silently cross season
    boundaries unless a later caller explicitly opts into a bounded radius.
    """

    season_radius: int = 0
    max_files: int = MAX_DEEP_CORRELATION_FILES
    max_pairwise_comparisons: int = MAX_DEEP_PAIRWISE_COMPARISONS

    def __post_init__(self) -> None:
        radius = _strict_int(self.season_radius, name="Deep correlation season radius")
        max_files = _strict_int(self.max_files, name="Deep correlation file limit")
        max_pairs = _strict_int(
            self.max_pairwise_comparisons,
            name="Deep pairwise comparison limit",
        )
        if radius < 0 or radius > MAX_DEEP_ADJACENT_SEASON_RADIUS:
            raise DeepIdentityError(
                "Deep correlation season radius exceeds the supported bound."
            )
        if max_files < 2 or max_files > MAX_DEEP_CORRELATION_FILES:
            raise DeepIdentityError(
                "Deep correlation file limit exceeds the supported bound."
            )
        if (
            max_pairs < max_files - 1
            or max_pairs > MAX_DEEP_PAIRWISE_COMPARISONS
        ):
            raise DeepIdentityError(
                "Deep pairwise limit must cover every target-to-peer comparison "
                "without exceeding the supported bound."
            )


    def identity_payload(self) -> dict[str, Any]:
        return {
            "season_radius": self.season_radius,
            "max_files": self.max_files,
            "max_pairwise_comparisons": self.max_pairwise_comparisons,
        }

    @classmethod
    def from_payload(cls, value: object) -> "DeepCorrelationPolicy":
        if not isinstance(value, Mapping):
            raise DeepIdentityError("Deep correlation policy metadata is missing.")
        try:
            return cls(
                season_radius=value["season_radius"],
                max_files=value["max_files"],
                max_pairwise_comparisons=value["max_pairwise_comparisons"],
            )
        except KeyError as exc:
            raise DeepIdentityError(
                "Deep correlation policy metadata is incomplete."
            ) from exc


@dataclass(frozen=True)
class DeepFileSnapshot:
    file_id: int
    title_id: int
    season: int
    episode_start: int
    episode_end: int
    size_bytes: int
    modified_at: float | None

    def identity_payload(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "title_id": self.title_id,
            "season": self.season,
            "episode_start": self.episode_start,
            "episode_end": self.episode_end,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
        }


@dataclass(frozen=True)
class DeepCorrelationPlan:
    target: DeepFileSnapshot
    peers: tuple[DeepFileSnapshot, ...]
    comparison_pairs: tuple[tuple[int, int], ...]
    policy: DeepCorrelationPolicy
    plan_signature: str

    @property
    def files(self) -> tuple[DeepFileSnapshot, ...]:
        return (self.target, *self.peers)


def _file_snapshot(row: sqlite3.Row | dict[str, Any]) -> DeepFileSnapshot:
    episode_start = int(row["episode_start"])
    episode_end = (
        episode_start
        if row["episode_end"] is None
        else max(episode_start, int(row["episode_end"]))
    )
    return DeepFileSnapshot(
        file_id=int(row["id"]),
        title_id=int(row["title_id"]),
        season=int(row["season"]),
        episode_start=episode_start,
        episode_end=episode_end,
        size_bytes=max(0, int(row["size_bytes"] or 0)),
        modified_at=(
            None
            if row["modified_at"] is None
            else float(row["modified_at"])
        ),
    )


def plan_deep_correlation(
    conn: sqlite3.Connection,
    *,
    file_id: int,
    policy: DeepCorrelationPolicy | None = None,
) -> DeepCorrelationPlan:
    """Select a bounded, snapshot-bound cross-file comparison cohort."""

    policy = policy or DeepCorrelationPolicy()
    file_id = _strict_int(file_id, name="Deep correlation file id")
    if file_id <= 0:
        raise DeepIdentityError("Deep correlation file id must be positive.")
    target_row = conn.execute(
        """SELECT f.id,f.title_id,f.season,f.episode_start,f.episode_end,
                  f.size_bytes,f.modified_at,t.kind title_kind
           FROM files f
           JOIN titles t ON t.id=f.title_id
           WHERE f.id=?""",
        (file_id,),
    ).fetchone()
    if target_row is None:
        raise DeepIdentityError("Deep correlation target file does not exist.")
    if str(target_row["title_kind"] or "") != "tv":
        raise DeepIdentityError("Deep episode correlation requires a TV title.")
    if target_row["season"] is None or target_row["episode_start"] is None:
        raise DeepIdentityError(
            "Deep correlation requires an episode-season coordinate."
        )
    target = _file_snapshot(target_row)

    if target.season == 0:
        season_clause = "f.season=0"
        season_parameters: tuple[int, ...] = ()
    else:
        minimum_season = max(1, target.season - policy.season_radius)
        maximum_season = target.season + policy.season_radius
        season_clause = "f.season BETWEEN ? AND ? AND f.season<>0"
        season_parameters = (minimum_season, maximum_season)

    rows = conn.execute(
        f"""SELECT f.id,f.title_id,f.season,f.episode_start,f.episode_end,
                   f.size_bytes,f.modified_at
            FROM files f
            WHERE f.title_id=? AND f.id<>?
              AND f.season IS NOT NULL AND f.episode_start IS NOT NULL
              AND {season_clause}
            ORDER BY
              ABS(f.season-?),
              ABS(f.episode_start-?),
              f.season,f.episode_start,f.id
            LIMIT ?""",
        (
            target.title_id,
            target.file_id,
            *season_parameters,
            target.season,
            target.episode_start,
            policy.max_files - 1,
        ),
    ).fetchall()
    peers = tuple(_file_snapshot(row) for row in rows)
    file_ids = [target.file_id, *(item.file_id for item in peers)]
    pairs = tuple(
        islice(
            combinations(file_ids, 2),
            policy.max_pairwise_comparisons,
        )
    )

    payload = {
        "version": DEEP_ORCHESTRATION_VERSION,
        "kind": "cross-file-correlation",
        "policy": {
            "season_radius": policy.season_radius,
            "max_files": policy.max_files,
            "max_pairwise_comparisons": policy.max_pairwise_comparisons,
        },
        "files": [item.identity_payload() for item in (target, *peers)],
        "comparison_pairs": pairs,
    }
    return DeepCorrelationPlan(
        target=target,
        peers=peers,
        comparison_pairs=pairs,
        policy=policy,
        plan_signature=_digest(payload),
    )


def build_deep_plan_metadata(
    candidate_plan: DeepCandidatePlan,
    correlation_plan: DeepCorrelationPlan,
) -> dict[str, Any]:
    """Return the exact persisted provenance required for a current Deep result."""

    return {
        "algorithm_version": DEEP_ORCHESTRATION_VERSION,
        "candidate_policy": candidate_plan.policy.identity_payload(),
        "candidate_plan_signature": candidate_plan.plan_signature,
        "candidate_keys": [
            candidate.key for candidate in candidate_plan.candidates
        ],
        "correlation_policy": correlation_plan.policy.identity_payload(),
        "correlation_plan_signature": correlation_plan.plan_signature,
        "correlation_file_ids": [
            item.file_id for item in correlation_plan.files
        ],
        "comparison_pairs": [
            [left, right]
            for left, right in correlation_plan.comparison_pairs
        ],
    }


def deep_plan_metadata_is_current(
    conn: sqlite3.Connection,
    *,
    file_id: int,
    title_id: int,
    season: int,
    episode_start: int,
    episode_end: int | None,
    language: str,
    metadata: object,
) -> bool:
    """Rebuild persisted Deep plans and fail closed on any semantic drift."""

    if not isinstance(metadata, Mapping):
        return False
    try:
        if int(metadata.get("algorithm_version") or 0) != DEEP_ORCHESTRATION_VERSION:
            return False
        candidate_policy = DeepCandidatePolicy.from_payload(
            metadata.get("candidate_policy")
        )
        correlation_policy = DeepCorrelationPolicy.from_payload(
            metadata.get("correlation_policy")
        )
        candidate_plan = generate_deep_episode_candidates(
            conn,
            title_id=title_id,
            season=season,
            episode_start=episode_start,
            episode_end=episode_end,
            language=language,
            policy=candidate_policy,
        )
        correlation_plan = plan_deep_correlation(
            conn,
            file_id=file_id,
            policy=correlation_policy,
        )
    except (DeepIdentityError, TypeError, ValueError, sqlite3.Error):
        return False

    expected = build_deep_plan_metadata(
        candidate_plan,
        correlation_plan,
    )
    raw_pairs = metadata.get("comparison_pairs")
    raw_file_ids = metadata.get("correlation_file_ids")
    raw_candidate_keys = metadata.get("candidate_keys")
    if (
        not isinstance(raw_pairs, list)
        or not isinstance(raw_file_ids, list)
        or not isinstance(raw_candidate_keys, list)
    ):
        return False

    persisted_pairs: list[list[int]] = []
    try:
        for pair in raw_pairs:
            if not isinstance(pair, list) or len(pair) != 2:
                return False
            left = _strict_int(pair[0], name="Deep comparison left file id")
            right = _strict_int(pair[1], name="Deep comparison right file id")
            if left <= 0 or right <= 0 or left == right:
                return False
            persisted_pairs.append([left, right])

        persisted_file_ids = []
        for value in raw_file_ids:
            file_value = _strict_int(
                value,
                name="Deep correlation metadata file id",
            )
            if file_value <= 0:
                return False
            persisted_file_ids.append(file_value)

        persisted_candidate_keys = []
        for value in raw_candidate_keys:
            if not isinstance(value, str) or not value:
                return False
            persisted_candidate_keys.append(value)
    except (DeepIdentityError, TypeError, ValueError):
        return False

    return (
        str(metadata.get("candidate_plan_signature") or "")
        == expected["candidate_plan_signature"]
        and persisted_candidate_keys == expected["candidate_keys"]
        and str(metadata.get("correlation_plan_signature") or "")
        == expected["correlation_plan_signature"]
        and persisted_file_ids == expected["correlation_file_ids"]
        and persisted_pairs == expected["comparison_pairs"]
    )

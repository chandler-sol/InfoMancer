from __future__ import annotations

from dataclasses import dataclass
from math import prod
from typing import Any, Iterable, Mapping

from .models import IdentityResultState


CONTENT_CATEGORIES = {
    "subtitle_text",
    "visual_text",
    "speech",
    "fingerprint",
    "external_identity",
}


@dataclass(frozen=True)
class CandidateResolution:
    candidate_key: str
    score: float
    support_strength: float
    conflict_strength: float
    independent_categories: int
    support_groups: int
    content_support: bool
    details: Mapping[str, Any]


@dataclass(frozen=True)
class IdentityResolution:
    state: IdentityResultState
    best_candidate_key: str | None
    candidates: tuple[CandidateResolution, ...]
    margin: float
    claimed_candidate_keys: tuple[str, ...]
    explanation: str


def _bounded(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _union(strengths: Iterable[float]) -> float:
    values = [_bounded(value) for value in strengths if _bounded(value) > 0.0]
    if not values:
        return 0.0
    return 1.0 - prod(1.0 - value for value in values)


def _candidate_details(candidate: Mapping[str, Any]) -> dict[str, Any]:
    value = candidate.get("details")
    return dict(value) if isinstance(value, Mapping) else {}


def _claimed_keys(candidates: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    keys: list[str] = []
    for candidate in candidates:
        origins = set(_candidate_details(candidate).get("origins") or [])
        if "claimed_coordinate" in origins:
            key = str(candidate.get("candidate_key") or candidate.get("key") or "")
            if key and key not in keys:
                keys.append(key)
    return tuple(keys)


def _alternate_order_conflict(
    candidate: Mapping[str, Any],
    claimed_identity: Mapping[str, Any],
) -> bool:
    details = _candidate_details(candidate)
    mappings = details.get("mappings") or []
    if not isinstance(mappings, list):
        return False

    try:
        claimed_season = int(claimed_identity.get("season"))
        claimed_start = int(claimed_identity.get("episode_start"))
        claimed_end = int(claimed_identity.get("episode_end") or claimed_start)
    except (TypeError, ValueError):
        return False

    matching_namespaces: set[str] = set()
    default_coordinates: set[tuple[int, int]] = set()
    for mapping in mappings:
        if not isinstance(mapping, Mapping):
            continue
        season = mapping.get("season")
        episode = mapping.get("episode")
        if season is None or episode is None:
            continue
        try:
            coordinate = (int(season), int(episode))
        except (TypeError, ValueError):
            continue
        namespace = str(mapping.get("order_namespace") or "")
        if coordinate[0] == claimed_season and claimed_start <= coordinate[1] <= claimed_end:
            matching_namespaces.add(namespace)
        if namespace == "default":
            default_coordinates.add(coordinate)

    if not matching_namespaces or not any(name != "default" for name in matching_namespaces):
        return False
    return any(
        season != claimed_season or not (claimed_start <= episode <= claimed_end)
        for season, episode in default_coordinates
    )


def resolve_identity(
    candidates: Iterable[Mapping[str, Any]],
    evidence: Iterable[Mapping[str, Any]],
    claimed_identity: Mapping[str, Any],
) -> IdentityResolution:
    """Resolve one persisted evidence snapshot conservatively.

    Evidence sharing a correlation group contributes at most one supporting and one
    conflicting signal per candidate. Filename/catalog claims are useful priors but
    never count as content proof.
    """
    candidate_rows = list(candidates)
    evidence_rows = list(evidence)
    claimed_keys = _claimed_keys(candidate_rows)

    try:
        start = int(claimed_identity.get("episode_start"))
        end = int(claimed_identity.get("episode_end") or start)
    except (TypeError, ValueError):
        start = end = 0

    resolved: list[CandidateResolution] = []
    by_key: dict[str, Mapping[str, Any]] = {}

    for candidate in candidate_rows:
        key = str(candidate.get("candidate_key") or candidate.get("key") or "")
        if not key:
            continue
        by_key[key] = candidate
        groups: dict[str, dict[str, Any]] = {}
        for item in evidence_rows:
            if str(item.get("candidate_key") or "") != key:
                continue
            relation = str(item.get("relation") or "")
            if relation not in {"supports", "conflicts"}:
                continue
            strength = _bounded(item.get("strength"))
            if strength <= 0:
                continue
            group_key = str(item.get("correlation_group") or "").strip()
            if not group_key:
                continue
            current = groups.setdefault(group_key, {
                "support": 0.0,
                "conflict": 0.0,
                "support_category": "",
                "conflict_category": "",
            })
            category = str(item.get("evidence_category") or item.get("category") or "")
            if relation == "supports" and strength > current["support"]:
                current["support"] = strength
                current["support_category"] = category
            if relation == "conflicts" and strength > current["conflict"]:
                current["conflict"] = strength
                current["conflict_category"] = category

        support_values = [float(group["support"]) for group in groups.values()]
        conflict_values = [float(group["conflict"]) for group in groups.values()]
        support = _union(support_values)
        conflict = _union(conflict_values)
        support_categories = {
            str(group["support_category"])
            for group in groups.values()
            if float(group["support"]) > 0 and group["support_category"]
        }
        support_groups = sum(float(group["support"]) > 0 for group in groups.values())
        content_support = bool(support_categories & CONTENT_CATEGORIES)
        score = support * (1.0 - (0.75 * conflict))
        resolved.append(CandidateResolution(
            candidate_key=key,
            score=round(max(0.0, min(1.0, score)), 6),
            support_strength=round(support, 6),
            conflict_strength=round(conflict, 6),
            independent_categories=len(support_categories),
            support_groups=support_groups,
            content_support=content_support,
            details={
                "support_categories": sorted(support_categories),
                "support_groups": support_groups,
                "content_support": content_support,
                "correlation_groups": sorted(groups),
            },
        ))

    resolved.sort(
        key=lambda item: (
            -item.score,
            -item.support_strength,
            item.conflict_strength,
            item.candidate_key,
        )
    )
    if not resolved:
        return IdentityResolution(
            IdentityResultState.INCONCLUSIVE,
            None,
            (),
            0.0,
            claimed_keys,
            "No candidate-specific evidence was available.",
        )

    best = resolved[0]
    second_score = resolved[1].score if len(resolved) > 1 else 0.0
    margin = round(max(0.0, best.score - second_score), 6)
    best_is_claimed = best.candidate_key in claimed_keys
    best_row = by_key.get(best.candidate_key, {})

    if end > start:
        state = IdentityResultState.INCONCLUSIVE
        explanation = (
            "Fast resolution remains inconclusive for a multi-episode file because "
            "one candidate identity cannot prove the complete episode range."
        )
    elif best_is_claimed and _alternate_order_conflict(best_row, claimed_identity):
        state = IdentityResultState.EPISODE_ORDER_CONFLICT
        explanation = (
            "The strongest content identity matches the claimed coordinate in an "
            "alternate order, while its default order uses a different coordinate."
        )
    elif best_is_claimed:
        if (
            best.content_support
            and best.support_groups >= 3
            and best.support_strength >= 0.72
            and best.conflict_strength <= 0.20
            and margin >= 0.15
        ):
            state = IdentityResultState.VERIFIED
            explanation = (
                "Independent content and metadata evidence strongly support the "
                "episode claimed by the catalog."
            )
        elif (
            best.content_support
            and best.support_groups >= 2
            and best.support_strength >= 0.55
            and best.conflict_strength <= 0.25
            and margin >= 0.10
        ):
            state = IdentityResultState.PROBABLY_CORRECT
            explanation = (
                "Content evidence favors the claimed episode, but the evidence does "
                "not meet the stricter verified threshold."
            )
        else:
            state = IdentityResultState.INCONCLUSIVE
            explanation = (
                "The claimed episode is not contradicted strongly enough to warn, "
                "but there is not enough independent content evidence to verify it."
            )
    else:
        if margin < 0.12:
            state = IdentityResultState.INCONCLUSIVE
            explanation = (
                "The leading candidates are too close to distinguish safely."
            )
        elif (
            not best.content_support
            or best.support_groups < 2
            or best.conflict_strength > 0.25
        ):
            state = IdentityResultState.INCONCLUSIVE
            explanation = (
                "A different candidate leads, but it lacks enough independent "
                "content evidence for a mismatch warning."
            )
        elif (
            best.support_strength >= 0.76
            and margin >= 0.24
            and best.independent_categories >= 2
        ):
            state = IdentityResultState.STRONG_MATCH_OTHER
            explanation = (
                "Multiple independent signals strongly favor a different episode."
            )
        elif best.support_strength >= 0.64 and margin >= 0.18:
            state = IdentityResultState.LIKELY_MISMATCH
            explanation = (
                "Independent evidence favors a different episode with a meaningful "
                "margin over the claimed identity."
            )
        elif best.support_strength >= 0.54 and margin >= 0.14:
            state = IdentityResultState.POSSIBLE_MISMATCH
            explanation = (
                "A different episode is favored, but the evidence is only strong "
                "enough for a cautious mismatch warning."
            )
        else:
            state = IdentityResultState.INCONCLUSIVE
            explanation = (
                "A different candidate leads, but not by enough evidence to warn."
            )

    return IdentityResolution(
        state=state,
        best_candidate_key=best.candidate_key,
        candidates=tuple(resolved),
        margin=margin,
        claimed_candidate_keys=claimed_keys,
        explanation=explanation,
    )

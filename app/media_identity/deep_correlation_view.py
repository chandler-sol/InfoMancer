from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from typing import Any, Mapping

from .versions import (
    DEEP_CORRELATION_ARTIFACT_VERSION,
    DEEP_CORRELATION_INTERPRETATION_VERSION,
    DEEP_ORCHESTRATION_VERSION,
    DEEP_PATTERN_CORRELATION_VERSION,
    DEEP_SEQUENCE_CORRELATION_VERSION,
)


DEEP_CORRELATION_ARTIFACT_KEY = "deep-correlation-analysis"
DEEP_CORRELATION_ARTIFACT_TYPE = "deep_correlation_analysis"
_MAX_VIEW_ITEMS = 64


class DeepCorrelationViewError(ValueError):
    """A persisted J4 artifact is malformed or does not match its target scan."""


def _canonical_json(value: object) -> str:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        serialized.encode("utf-8")
        return serialized
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise DeepCorrelationViewError(
            "Deep correlation analysis cannot be serialized safely."
        ) from exc


def _valid_sha256(value: object) -> bool:
    digest = str(value or "").strip().casefold()
    return (
        len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
    )


def _same_modified_at(first: object, second: object) -> bool:
    if first is None or second is None:
        return first is None and second is None
    try:
        return float(first) == float(second)
    except (TypeError, ValueError):
        return False


def _strict_int(
    value: object,
    *,
    minimum: int = 0,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise DeepCorrelationViewError(
            "Deep correlation analysis contains an invalid integer."
        )
    return value


def _strict_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise DeepCorrelationViewError(
            "Deep correlation analysis contains an invalid boolean."
        )
    return value


def _strict_file_ids(value: object) -> tuple[int, ...]:
    if not isinstance(value, list) or len(value) > _MAX_VIEW_ITEMS:
        raise DeepCorrelationViewError(
            "Deep correlation file-ID list is malformed."
        )
    result = tuple(
        _strict_int(item, minimum=1)
        for item in value
    )
    if len(set(result)) != len(result):
        raise DeepCorrelationViewError(
            "Deep correlation file-ID list contains duplicates."
        )
    return result


def _coverage_view(
    raw: object,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise DeepCorrelationViewError(
            "Deep correlation coverage metadata is malformed."
        )
    modalities = raw.get("complete_modalities")
    if (
        not isinstance(modalities, list)
        or len(modalities) > 2
        or len(set(modalities)) != len(modalities)
        or any(item not in {"video", "audio"} for item in modalities)
    ):
        raise DeepCorrelationViewError(
            "Deep correlation modality coverage is malformed."
        )
    planned_pairs = _strict_int(
        raw.get("planned_pair_count"),
    )
    interpreted_pairs = _strict_int(
        raw.get("interpreted_pair_count"),
    )
    planned_files = _strict_int(
        raw.get("planned_file_count"),
        minimum=1,
    )
    valid_hypotheses = _strict_int(
        raw.get("valid_hypothesis_count"),
    )
    if (
        interpreted_pairs > planned_pairs
        or valid_hypotheses > planned_files
    ):
        raise DeepCorrelationViewError(
            "Deep correlation coverage counts are inconsistent."
        )
    missing = _strict_file_ids(
        raw.get("missing_scan_file_ids")
    )
    invalid = _strict_file_ids(
        raw.get("invalid_scan_file_ids")
    )
    if set(missing) & set(invalid):
        raise DeepCorrelationViewError(
            "Deep correlation peer coverage classifications overlap."
        )
    peer_complete = _strict_bool(
        raw.get("sequence_peer_coverage_complete")
    )
    expected_peer_complete = (
        valid_hypotheses == planned_files
        and not missing
        and not invalid
    )
    if peer_complete != expected_peer_complete:
        raise DeepCorrelationViewError(
            "Deep correlation peer coverage flag is inconsistent."
        )
    fully_multimodal = _strict_bool(
        raw.get("fully_multimodal")
    )
    if fully_multimodal != (set(modalities) == {"video", "audio"}):
        raise DeepCorrelationViewError(
            "Deep correlation multimodal coverage flag is inconsistent."
        )
    return {
        "complete_modalities": tuple(modalities),
        "fully_multimodal": fully_multimodal,
        "planned_pair_count": planned_pairs,
        "interpreted_pair_count": interpreted_pairs,
        "planned_file_count": planned_files,
        "valid_hypothesis_count": valid_hypotheses,
        "missing_scan_file_ids": missing,
        "invalid_scan_file_ids": invalid,
        "sequence_peer_coverage_complete": peer_complete,
    }


def _bounded_mapping_list(
    raw: object,
    *,
    label: str,
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(raw, list) or len(raw) > _MAX_VIEW_ITEMS:
        raise DeepCorrelationViewError(
            f"Deep correlation {label} list is malformed."
        )
    if any(not isinstance(item, Mapping) for item in raw):
        raise DeepCorrelationViewError(
            f"Deep correlation {label} list contains malformed records."
        )
    return tuple(raw)


def _coordinate(value: object) -> tuple[int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 2
    ):
        raise DeepCorrelationViewError(
            "Deep correlation episode coordinate is malformed."
        )
    return (
        _strict_int(value[0]),
        _strict_int(value[1]),
    )


def _target_patterns(
    output: Mapping[str, Any],
    *,
    target_file_id: int,
    target_season: int | None,
) -> dict[str, Any]:
    duplicates: list[dict[str, Any]] = []
    for item in _bounded_mapping_list(
        output.get("duplicates"),
        label="duplicate",
    ):
        left = _strict_int(item.get("left_file_id"), minimum=1)
        right = _strict_int(item.get("right_file_id"), minimum=1)
        if left >= right:
            raise DeepCorrelationViewError(
                "Deep correlation duplicate pair is not normalized."
            )
        strength = str(item.get("strength") or "")
        agreement = str(item.get("agreement") or "")
        if strength not in {
            "strong_multimodal",
            "supported_multimodal",
        } or agreement not in {"both_high", "both_support"}:
            raise DeepCorrelationViewError(
                "Deep correlation duplicate status is malformed."
            )
        if target_file_id in {left, right}:
            duplicates.append({
                "other_file_id": right if left == target_file_id else left,
                "strength": strength,
                "agreement": agreement,
            })

    swaps: list[dict[str, Any]] = []
    for item in _bounded_mapping_list(
        output.get("swaps"),
        label="swap",
    ):
        left = _strict_int(item.get("left_file_id"), minimum=1)
        right = _strict_int(item.get("right_file_id"), minimum=1)
        if left >= right:
            raise DeepCorrelationViewError(
                "Deep correlation swap pair is not normalized."
            )
        status = str(item.get("status") or "")
        if status not in {
            "corroborated_distinct",
            "hypothesis_only",
            "conflicted_similarity",
            "conflicted_modalities",
        }:
            raise DeepCorrelationViewError(
                "Deep correlation swap status is malformed."
            )
        left_claimed = _coordinate(item.get("left_claimed"))
        right_claimed = _coordinate(item.get("right_claimed"))
        left_hypothesis = _coordinate(item.get("left_hypothesis"))
        right_hypothesis = _coordinate(item.get("right_hypothesis"))
        if (
            left_hypothesis != right_claimed
            or right_hypothesis != left_claimed
        ):
            raise DeepCorrelationViewError(
                "Deep correlation swap coordinates are not reciprocal."
            )
        if target_file_id in {left, right}:
            swaps.append({
                "other_file_id": right if left == target_file_id else left,
                "status": status,
                "fingerprint_agreement": (
                    None
                    if item.get("fingerprint_agreement") is None
                    else str(item.get("fingerprint_agreement"))
                ),
                "target_claimed": (
                    left_claimed
                    if left == target_file_id
                    else right_claimed
                ),
                "target_hypothesis": (
                    left_hypothesis
                    if left == target_file_id
                    else right_hypothesis
                ),
            })

    cycles: list[dict[str, Any]] = []
    for item in _bounded_mapping_list(
        output.get("identity_cycles"),
        label="identity-cycle",
    ):
        file_ids = _strict_file_ids(item.get("file_ids"))
        target_ids = _strict_file_ids(item.get("target_file_ids"))
        if (
            len(file_ids) < 3
            or set(file_ids) != set(target_ids)
        ):
            raise DeepCorrelationViewError(
                "Deep correlation identity cycle is malformed."
            )
        if target_file_id in set(file_ids):
            cycles.append({
                "file_ids": file_ids,
                "target_file_ids": target_ids,
            })

    sequence_raw = output.get("sequence")
    if not isinstance(sequence_raw, Mapping):
        raise DeepCorrelationViewError(
            "Deep correlation sequence output is malformed."
        )
    authoritative: list[dict[str, Any]] = []
    for item in _bounded_mapping_list(
        sequence_raw.get("authoritative_observations"),
        label="sequence-observation",
    ):
        season = _strict_int(item.get("season"), minimum=1)
        offset = item.get("offset")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset == 0:
            raise DeepCorrelationViewError(
                "Deep correlation sequence offset is malformed."
            )
        ratio = item.get("support_ratio")
        if (
            isinstance(ratio, bool)
            or not isinstance(ratio, (int, float))
            or not math.isfinite(float(ratio))
            or not 0.0 <= float(ratio) <= 1.0
        ):
            raise DeepCorrelationViewError(
                "Deep correlation sequence support ratio is malformed."
            )
        supporting_file_ids = _strict_file_ids(
            item.get("supporting_file_ids")
        )
        usable_file_ids = _strict_file_ids(
            item.get("usable_file_ids")
        )
        support_count = _strict_int(
            item.get("support_count"),
            minimum=1,
        )
        usable_count = _strict_int(
            item.get("usable_count"),
            minimum=1,
        )
        if (
            support_count > usable_count
            or support_count != len(supporting_file_ids)
            or usable_count != len(usable_file_ids)
            or not set(supporting_file_ids).issubset(
                set(usable_file_ids)
            )
        ):
            raise DeepCorrelationViewError(
                "Deep correlation sequence counts are inconsistent."
            )
        if target_season is None or season == target_season:
            authoritative.append({
                "season": season,
                "offset": offset,
                "supporting_file_ids": supporting_file_ids,
                "usable_file_ids": usable_file_ids,
                "target_supports": (
                    target_file_id in set(supporting_file_ids)
                ),
                "support_count": support_count,
                "usable_count": usable_count,
                "support_ratio": float(ratio),
                "longest_chain": _strict_int(
                    item.get("longest_chain"),
                    minimum=1,
                ),
                "first_claimed_episode": _strict_int(
                    item.get("first_claimed_episode"),
                ),
                "last_claimed_episode": _strict_int(
                    item.get("last_claimed_episode"),
                ),
            })

    ambiguous = _strict_file_ids(
        output.get("ambiguous_claim_file_ids")
    )
    target_claim_ambiguous = target_file_id in set(
        ambiguous
    )
    target_sequence = [
        item for item in authoritative
        if item["target_supports"]
    ]

    review_state = "no_cross_file_pattern"
    review_label = "No target-specific cross-file pattern"
    review_explanation = (
        "Deep completed its cross-file analysis without an authoritative "
        "pattern that directly identifies this file."
    )
    if duplicates:
        review_state = "duplicate_content_identity"
        review_label = "Duplicate content identity"
        review_explanation = (
            "Multimodal fingerprint evidence indicates this file shares "
            "episode content with another claimed file."
        )
    else:
        corroborated_swaps = [
            item for item in swaps
            if item["status"] == "corroborated_distinct"
        ]
        hypothesis_swaps = [
            item for item in swaps
            if item["status"] == "hypothesis_only"
        ]
        similarity_conflicts = [
            item for item in swaps
            if item["status"] == "conflicted_similarity"
        ]
        modality_conflicts = [
            item for item in swaps
            if item["status"] == "conflicted_modalities"
        ]
        if corroborated_swaps:
            review_state = "possible_swapped_episodes"
            review_label = "Possible swapped episodes"
            review_explanation = (
                "Two resolver hypotheses are reciprocal and fingerprint "
                "evidence supports the files being distinct."
            )
        elif cycles:
            review_state = "identity_cycle"
            review_label = "Multi-file identity rotation"
            review_explanation = (
                "Resolver hypotheses form a three-or-more-file identity cycle "
                "that cannot be represented as one pairwise swap."
            )
        elif hypothesis_swaps:
            review_state = "possible_swapped_episodes"
            review_label = "Possible swapped episodes"
            review_explanation = (
                "Two resolver hypotheses are reciprocal, but fingerprint "
                "coverage is not strong enough to corroborate the swap."
            )
        elif similarity_conflicts:
            review_state = "conflicting_swap_similarity"
            review_label = "Swap hypothesis conflicts with similarity"
            review_explanation = (
                "Resolver hypotheses are reciprocal, but fingerprint evidence "
                "also supports the files sharing content."
            )
        elif modality_conflicts:
            review_state = "conflicting_fingerprint_modalities"
            review_label = "Fingerprint modalities disagree"
            review_explanation = (
                "Video and audio fingerprint evidence disagree about a "
                "reciprocal identity hypothesis."
            )
        elif target_sequence:
            review_state = "sequence_offset"
            review_label = "Episode sequence offset"
            offsets = sorted({
                int(item["offset"])
                for item in target_sequence
            })
            offset_text = ", ".join(
                f"{value:+d}" for value in offsets
            )
            review_explanation = (
                "This file participates in an authoritative season-level "
                f"episode offset pattern ({offset_text})."
            )
        elif target_claim_ambiguous:
            review_state = "ambiguous_claim"
            review_label = "Ambiguous claimed episode ownership"
            review_explanation = (
                "More than one file claims this episode coordinate, so Deep "
                "will not infer swap ownership from the claim alone."
            )

    return {
        "duplicates": tuple(duplicates),
        "swaps": tuple(swaps),
        "identity_cycles": tuple(cycles),
        "sequence_observations": tuple(authoritative),
        "target_sequence_observations": tuple(target_sequence),
        "target_claim_ambiguous": target_claim_ambiguous,
        "review_state": review_state,
        "review_label": review_label,
        "review_explanation": review_explanation,
        "review_actionable": False,
    }


def load_current_deep_correlation_view(
    conn: sqlite3.Connection,
    *,
    scan: Mapping[str, Any],
    result_revision: int,
    decision_snapshot_sha256: str,
    target_season: int | None,
) -> dict[str, Any] | None:
    if (
        isinstance(result_revision, bool)
        or not isinstance(result_revision, int)
        or result_revision < 1
        or not _valid_sha256(decision_snapshot_sha256)
    ):
        return None
    try:
        scan_id = _strict_int(scan.get("id"), minimum=1)
        file_id = _strict_int(scan.get("file_id"), minimum=1)
    except DeepCorrelationViewError:
        return None

    row = conn.execute(
        """SELECT *
           FROM media_identity_artifacts
           WHERE file_id=?
             AND artifact_type=?
             AND analyzer_key=?
             AND analyzer_version=?
             AND status='complete'
             AND profile='deep'
             AND source_ref=?
           ORDER BY id DESC
           LIMIT 1""",
        (
            file_id,
            DEEP_CORRELATION_ARTIFACT_TYPE,
            DEEP_CORRELATION_ARTIFACT_KEY,
            str(DEEP_CORRELATION_ARTIFACT_VERSION),
            f"scan:{scan_id}",
        ),
    ).fetchone()
    if row is None:
        return None
    persisted = dict(row)

    try:
        payload = json.loads(
            str(persisted.get("payload_json") or "")
        )
        if not isinstance(payload, Mapping):
            return None
        seal = str(payload.get("artifact_output_sha256") or "").casefold()
        if not _valid_sha256(seal):
            return None
        body = dict(payload)
        body.pop("artifact_output_sha256", None)
        expected_seal = hashlib.sha256(
            _canonical_json(body).encode("utf-8")
        ).hexdigest()
        if seal != expected_seal:
            return None
        if payload.get("analysis_complete") is not True:
            return None

        identity = payload.get("identity")
        output = payload.get("output")
        if not isinstance(identity, Mapping) or not isinstance(output, Mapping):
            return None
        expected_versions = {
            "artifact_version": DEEP_CORRELATION_ARTIFACT_VERSION,
            "deep_orchestration_version": DEEP_ORCHESTRATION_VERSION,
            "interpretation_version": DEEP_CORRELATION_INTERPRETATION_VERSION,
            "sequence_version": DEEP_SEQUENCE_CORRELATION_VERSION,
            "pattern_version": DEEP_PATTERN_CORRELATION_VERSION,
        }
        for key, value in expected_versions.items():
            if identity.get(key) != value:
                return None
        if (
            identity.get("scan_id") != scan_id
            or identity.get("target_file_id") != file_id
            or identity.get("result_revision") != result_revision
            or str(
                identity.get("decision_snapshot_sha256") or ""
            ).casefold() != decision_snapshot_sha256.casefold()
            or str(identity.get("metadata_signature") or "")
            != str(scan.get("metadata_signature") or "")
            or str(identity.get("file_sha256") or "").casefold()
            != str(scan.get("file_sha256") or "").casefold()
            or identity.get("file_size_bytes")
            != int(scan.get("file_size_bytes") or 0)
            or not _same_modified_at(
                identity.get("file_modified_at"),
                scan.get("file_modified_at"),
            )
        ):
            return None
        if (
            str(persisted.get("source_signature") or "")
            != str(identity.get("correlation_plan_signature") or "")
            or int(persisted.get("file_size_bytes") or 0)
            != int(scan.get("file_size_bytes") or 0)
            or not _same_modified_at(
                persisted.get("file_modified_at"),
                scan.get("file_modified_at"),
            )
        ):
            return None

        coverage = _coverage_view(output.get("coverage"))
        patterns = _target_patterns(
            output,
            target_file_id=file_id,
            target_season=target_season,
        )
    except (
        DeepCorrelationViewError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None

    return {
        "artifact_id": int(persisted["id"]),
        "target_current": True,
        "analysis_complete": True,
        "coverage": coverage,
        **patterns,
    }

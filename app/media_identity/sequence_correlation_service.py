from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from types import MappingProxyType
from typing import Any, Mapping

from ..db import Database
from .correlation_interpretation_service import (
    DeepCorrelationInterpretationRun,
)
from .decision_snapshot import result_revision
from .deep import DeepCorrelationPolicy, plan_deep_correlation
from .models import IdentityResultState
from .sequence_correlation import (
    SequenceHypothesis,
    SequenceOffsetAnalysis,
    SequenceOffsetPolicy,
    detect_sequence_offsets,
)
from .service import (
    MediaIdentityDecisionError,
    MediaIdentityDecisionService,
)
from .versions import DEEP_CORRELATION_INTERPRETATION_VERSION


SEQUENCE_SCAN_HISTORY_LIMIT = 8


class DeepSequenceCorrelationError(RuntimeError):
    """Current cohort scans cannot support safe sequence-offset correlation."""


@dataclass(frozen=True)
class DeepSequenceCorrelationRun:
    scan_id: int
    target_file_id: int
    result_revision: int
    correlation_plan_signature: str
    sequence_policy_signature: str
    sequence_policy_identity: Mapping[str, Any]
    planned_file_count: int
    hypothesis_count: int
    missing_scan_file_ids: tuple[int, ...]
    invalid_scan_file_ids: tuple[int, ...]
    hypotheses: tuple[SequenceHypothesis, ...]
    analysis: SequenceOffsetAnalysis

    def __post_init__(self) -> None:
        for label, value in (
            ("scan ID", self.scan_id),
            ("target file ID", self.target_file_id),
            ("result revision", self.result_revision),
            ("planned file count", self.planned_file_count),
            ("hypothesis count", self.hypothesis_count),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
            ):
                raise DeepSequenceCorrelationError(
                    f"Sequence run {label} must be a positive integer."
                )
        for label, digest in (
            (
                "correlation-plan signature",
                self.correlation_plan_signature,
            ),
            (
                "sequence-policy signature",
                self.sequence_policy_signature,
            ),
        ):
            normalized = str(digest or "").strip().casefold()
            if (
                len(normalized) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in normalized
                )
            ):
                raise DeepSequenceCorrelationError(
                    f"Sequence run {label} is invalid."
                )
        if not isinstance(self.sequence_policy_identity, Mapping):
            raise DeepSequenceCorrelationError(
                "Sequence run policy identity is malformed."
            )
        if not isinstance(self.analysis, SequenceOffsetAnalysis):
            raise DeepSequenceCorrelationError(
                "Sequence run analysis is malformed."
            )
        expected_policy_payload = self.analysis.policy.identity_payload()
        if (
            _canonical_json_bytes(self.sequence_policy_identity)
            != _canonical_json_bytes(expected_policy_payload)
        ):
            raise DeepSequenceCorrelationError(
                "Sequence run policy identity does not match its analysis."
            )
        expected_policy_signature = hashlib.sha256(
            _canonical_json_bytes(expected_policy_payload)
        ).hexdigest()
        if expected_policy_signature != self.sequence_policy_signature:
            raise DeepSequenceCorrelationError(
                "Sequence run policy signature does not match its analysis."
            )
        if len(self.hypotheses) != self.hypothesis_count:
            raise DeepSequenceCorrelationError(
                "Sequence run hypothesis count is inconsistent."
            )
        if any(
            not isinstance(item, SequenceHypothesis)
            for item in self.hypotheses
        ):
            raise DeepSequenceCorrelationError(
                "Sequence run hypotheses are malformed."
            )
        hypothesis_ids = tuple(
            item.file_id for item in self.hypotheses
        )
        if len(set(hypothesis_ids)) != len(hypothesis_ids):
            raise DeepSequenceCorrelationError(
                "Sequence run hypotheses contain duplicate file IDs."
            )
        missing = set(self.missing_scan_file_ids)
        invalid = set(self.invalid_scan_file_ids)
        if (
            len(missing) != len(self.missing_scan_file_ids)
            or len(invalid) != len(self.invalid_scan_file_ids)
            or missing & invalid
            or missing & set(hypothesis_ids)
            or invalid & set(hypothesis_ids)
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
                for value in (*missing, *invalid)
            )
        ):
            raise DeepSequenceCorrelationError(
                "Sequence run missing/invalid file classifications overlap."
            )
        if (
            self.hypothesis_count
            + len(self.missing_scan_file_ids)
            + len(self.invalid_scan_file_ids)
            != self.planned_file_count
        ):
            raise DeepSequenceCorrelationError(
                "Sequence run cohort accounting is inconsistent."
            )
        if self.target_file_id not in set(hypothesis_ids):
            raise DeepSequenceCorrelationError(
                "Sequence run is missing its target hypothesis."
            )
        if self.analysis.hypothesis_count != self.hypothesis_count:
            raise DeepSequenceCorrelationError(
                "Sequence run analysis does not match its hypothesis set."
            )

    @property
    def target_hypothesis(self) -> SequenceHypothesis | None:
        return next(
            (
                item for item in self.hypotheses
                if item.file_id == self.target_file_id
            ),
            None,
        )


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise DeepSequenceCorrelationError(
            "Sequence correlation policy cannot be serialized safely."
        ) from exc


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze(item)
            for key, item in value.items()
        })
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _policy_identity(
    policy: SequenceOffsetPolicy,
) -> tuple[Mapping[str, Any], str]:
    payload = policy.identity_payload()
    signature = hashlib.sha256(
        _canonical_json_bytes(payload)
    ).hexdigest()
    frozen = _freeze(payload)
    if not isinstance(frozen, Mapping):
        raise DeepSequenceCorrelationError(
            "Sequence correlation policy identity is malformed."
        )
    return frozen, signature


def _strict_coordinate(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


class DeepSequenceCorrelationService:
    """Build J4.2 sequence hypotheses from sealed current resolver outputs."""

    def __init__(
        self,
        database: Database,
        *,
        correlation_policy: DeepCorrelationPolicy | None = None,
        sequence_policy: SequenceOffsetPolicy | None = None,
    ) -> None:
        self.database = database
        self.correlation_policy = (
            correlation_policy or DeepCorrelationPolicy()
        )
        self.sequence_policy = sequence_policy or SequenceOffsetPolicy()

    @staticmethod
    def _candidate_default_coordinate(
        conn: sqlite3.Connection,
        candidate: Mapping[str, Any],
        *,
        title_id: int,
    ) -> tuple[int, int] | None:
        expected_id = candidate.get("expected_episode_id")
        if (
            not isinstance(expected_id, bool)
            and isinstance(expected_id, int)
            and expected_id > 0
        ):
            row = conn.execute(
                """SELECT season,episode
                   FROM expected_episodes
                   WHERE id=? AND title_id=?""",
                (expected_id, int(title_id)),
            ).fetchone()
            if row is not None:
                season = _strict_coordinate(row["season"])
                episode = _strict_coordinate(row["episode"])
                if season is not None and episode is not None:
                    return season, episode

        details = candidate.get("details")
        mappings = (
            details.get("mappings")
            if isinstance(details, Mapping)
            else None
        )
        default_coordinates: set[tuple[int, int]] = set()
        if isinstance(mappings, list):
            for mapping in mappings:
                if (
                    not isinstance(mapping, Mapping)
                    or str(
                        mapping.get("order_namespace") or ""
                    ).strip().casefold() != "default"
                ):
                    continue
                season = _strict_coordinate(mapping.get("season"))
                episode = _strict_coordinate(mapping.get("episode"))
                if season is not None and episode is not None:
                    default_coordinates.add((season, episode))
        if len(default_coordinates) == 1:
            return next(iter(default_coordinates))
        if len(default_coordinates) > 1:
            return None

        if str(
            candidate.get("order_namespace") or ""
        ).strip().casefold() == "default":
            season = _strict_coordinate(candidate.get("season"))
            episode = _strict_coordinate(candidate.get("episode"))
            if season is not None and episode is not None:
                return season, episode
        return None

    @staticmethod
    def _scan_ids(
        conn: sqlite3.Connection,
        file_id: int,
    ) -> tuple[int, ...]:
        rows = conn.execute(
            """SELECT id
               FROM media_identity_scans
               WHERE file_id=? AND status='complete'
                 AND result_state IS NOT NULL
               ORDER BY
                 CASE completed_profile
                   WHEN 'deep' THEN 0
                   WHEN 'normal' THEN 1
                   WHEN 'fast' THEN 2
                   ELSE 3
                 END,
                 id DESC
               LIMIT ?""",
            (
                int(file_id),
                SEQUENCE_SCAN_HISTORY_LIMIT,
            ),
        ).fetchall()
        return tuple(int(row["id"]) for row in rows)

    @staticmethod
    def _validated_hypothesis(
        conn: sqlite3.Connection,
        scan_id: int,
    ) -> SequenceHypothesis | None:
        try:
            scan, candidates, evidence = (
                MediaIdentityDecisionService._scan_snapshot(
                    conn,
                    int(scan_id),
                )
            )
        except MediaIdentityDecisionError:
            return None
        if (
            scan.get("status") != "complete"
            or scan.get("result_state") is None
            or not str(scan.get("best_candidate_key") or "")
        ):
            return None

        current, file_row = (
            MediaIdentityDecisionService._scan_snapshot_is_current(
                conn,
                scan,
                evidence,
            )
        )
        if not current or not isinstance(file_row, Mapping):
            return None
        try:
            title_id = int(file_row["title_id"])
        except (KeyError, TypeError, ValueError):
            return None

        revision = result_revision(scan)
        claimed = MediaIdentityDecisionService._claimed_identity(scan)
        sealed_revision, sealed_digest = (
            MediaIdentityDecisionService._decision_token(claimed)
        )
        if (
            revision <= 0
            or sealed_revision != revision
            or not sealed_digest
        ):
            return None

        resolution = MediaIdentityDecisionService._resolve_snapshot(
            scan,
            candidates,
            evidence,
        )
        if (
            resolution.state.value
            != str(scan.get("result_state") or "")
            or str(resolution.best_candidate_key or "")
            != str(scan.get("best_candidate_key") or "")
        ):
            return None
        if resolution.best_candidate_key is None:
            return None

        candidate_by_key = {
            str(item.get("candidate_key") or ""): item
            for item in candidates
        }
        candidate = candidate_by_key.get(
            resolution.best_candidate_key
        )
        resolved = next(
            (
                item for item in resolution.candidates
                if item.candidate_key
                == resolution.best_candidate_key
            ),
            None,
        )
        if candidate is None or resolved is None:
            return None

        default_coordinate = (
            DeepSequenceCorrelationService._candidate_default_coordinate(
                conn,
                candidate,
                title_id=title_id,
            )
        )
        if default_coordinate is None:
            return None

        claimed_season = _strict_coordinate(claimed.get("season"))
        claimed_episode = _strict_coordinate(
            claimed.get("episode_start")
        )
        claimed_end = _strict_coordinate(
            claimed.get("episode_end")
        )
        if (
            claimed_season is None
            or claimed_episode is None
        ):
            return None
        if claimed_end is None:
            claimed_end = claimed_episode

        try:
            result_state = IdentityResultState(
                str(scan["result_state"])
            )
        except ValueError:
            return None

        return SequenceHypothesis(
            file_id=int(scan["file_id"]),
            scan_id=int(scan["id"]),
            result_revision=revision,
            claimed_season=claimed_season,
            claimed_episode=claimed_episode,
            claimed_episode_end=claimed_end,
            candidate_key=resolution.best_candidate_key,
            hypothesis_season=default_coordinate[0],
            hypothesis_episode=default_coordinate[1],
            result_state=result_state,
            support_strength=resolved.support_strength,
            conflict_strength=resolved.conflict_strength,
            margin=resolution.margin,
            independent_categories=resolved.independent_categories,
            content_support=resolved.content_support,
        )

    def _best_current_hypothesis(
        self,
        conn: sqlite3.Connection,
        file_id: int,
    ) -> tuple[SequenceHypothesis | None, bool]:
        scan_ids = self._scan_ids(conn, file_id)
        if not scan_ids:
            return None, False
        for scan_id in scan_ids:
            hypothesis = self._validated_hypothesis(
                conn,
                scan_id,
            )
            if hypothesis is not None:
                return hypothesis, True
        return None, True

    def run(
        self,
        scan_id: int,
        interpretation: DeepCorrelationInterpretationRun,
    ) -> DeepSequenceCorrelationRun:
        if not isinstance(
            interpretation,
            DeepCorrelationInterpretationRun,
        ):
            raise DeepSequenceCorrelationError(
                "J4.2 requires a DeepCorrelationInterpretationRun."
            )
        if interpretation.scan_id != int(scan_id):
            raise DeepSequenceCorrelationError(
                "J4.2 interpretation belongs to a different target scan."
            )
        if (
            interpretation.interpretation_version
            != DEEP_CORRELATION_INTERPRETATION_VERSION
        ):
            raise DeepSequenceCorrelationError(
                "J4.2 interpretation semantics are stale."
            )
        if (
            len(str(interpretation.policy_signature or "")) != 64
            or any(
                character not in "0123456789abcdef"
                for character in str(
                    interpretation.policy_signature or ""
                ).casefold()
            )
        ):
            raise DeepSequenceCorrelationError(
                "J4.2 interpretation policy signature is invalid."
            )

        with self.database.connect() as conn:
            try:
                target_scan, target_candidates, target_evidence = (
                    MediaIdentityDecisionService._scan_snapshot(
                        conn,
                        int(scan_id),
                    )
                )
            except MediaIdentityDecisionError as exc:
                raise DeepSequenceCorrelationError(
                    "J4.2 target scan was not found."
                ) from exc
            target_revision = result_revision(target_scan)
            if target_revision != interpretation.result_revision:
                raise DeepSequenceCorrelationError(
                    "J4.2 target publication changed after fingerprint interpretation."
                )
            current, _ = (
                MediaIdentityDecisionService._scan_snapshot_is_current(
                    conn,
                    target_scan,
                    target_evidence,
                )
            )
            if not current:
                raise DeepSequenceCorrelationError(
                    "J4.2 target scan is stale."
                )

            plan = plan_deep_correlation(
                conn,
                file_id=int(target_scan["file_id"]),
                policy=self.correlation_policy,
            )
            if (
                plan.plan_signature
                != interpretation.correlation_plan_signature
                or len(plan.comparison_pairs)
                != interpretation.planned_pair_count
            ):
                raise DeepSequenceCorrelationError(
                    "J4.2 correlation cohort changed after fingerprint interpretation."
                )

            hypotheses: list[SequenceHypothesis] = []
            missing: list[int] = []
            invalid: list[int] = []
            for item in plan.files:
                if item.file_id == int(target_scan["file_id"]):
                    hypothesis = self._validated_hypothesis(
                        conn,
                        int(scan_id),
                    )
                    had_scan = True
                else:
                    hypothesis, had_scan = (
                        self._best_current_hypothesis(
                            conn,
                            item.file_id,
                        )
                    )
                if hypothesis is None:
                    if item.file_id == int(target_scan["file_id"]):
                        raise DeepSequenceCorrelationError(
                            "J4.2 target resolver snapshot is invalid."
                        )
                    if had_scan:
                        invalid.append(item.file_id)
                    else:
                        missing.append(item.file_id)
                    continue
                hypotheses.append(hypothesis)

        analysis = detect_sequence_offsets(
            hypotheses,
            policy=self.sequence_policy,
        )
        policy_identity, policy_signature = _policy_identity(
            self.sequence_policy
        )
        return DeepSequenceCorrelationRun(
            scan_id=int(scan_id),
            target_file_id=int(target_scan["file_id"]),
            result_revision=target_revision,
            correlation_plan_signature=plan.plan_signature,
            sequence_policy_signature=policy_signature,
            sequence_policy_identity=policy_identity,
            planned_file_count=len(plan.files),
            hypothesis_count=len(hypotheses),
            missing_scan_file_ids=tuple(sorted(missing)),
            invalid_scan_file_ids=tuple(sorted(invalid)),
            hypotheses=tuple(sorted(
                hypotheses,
                key=lambda item: (
                    item.claimed_season,
                    item.claimed_episode,
                    item.file_id,
                ),
            )),
            analysis=analysis,
        )

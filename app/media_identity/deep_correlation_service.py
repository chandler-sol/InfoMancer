from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Any, Callable, Mapping

from ..db import Database
from .correlation_interpretation import (
    CorrelationInterpretationError,
    CorrelationInterpretationPolicy,
    ModalityInterpretation,
    PairInterpretation,
)
from .correlation_interpretation_service import (
    DeepCorrelationInterpretationRun,
    interpret_fingerprint_bundle,
)
from .correlation_patterns import (
    CorrelationPatternAnalysis,
    DuplicatePatternObservation,
    IdentityCycleObservation,
    SwapPatternObservation,
)
from .correlation_patterns_service import (
    DeepCorrelationPatternError,
    DeepCorrelationPatternRun,
    correlate_deep_patterns,
)
from .decision_snapshot import result_revision
from .deep import (
    DeepCorrelationPolicy,
    DeepIdentityError,
    plan_deep_correlation,
)
from .fingerprint import FingerprintError, FingerprintMatchPolicy
from .fingerprint_audio_service import (
    DeepAudioFingerprintCorrelationService,
)
from .fingerprint_bundle import (
    DeepFingerprintBundleError,
    DeepFingerprintBundleRun,
    DeepFingerprintBundleService,
)
from .fingerprint_correlation import (
    DeepFingerprintCorrelationError,
    DeepFingerprintCorrelationRun,
    DeepFingerprintCorrelationService,
)
from .fingerprint_service import DeepFingerprintError
from .sequence_correlation import (
    SequenceCorrelationError,
    SequenceHypothesis,
    SequenceOffsetAnalysis,
    SequenceOffsetObservation,
    SequenceOffsetPolicy,
    detect_sequence_offsets,
)
from .sequence_correlation_service import (
    DeepSequenceCorrelationError,
    DeepSequenceCorrelationRun,
    DeepSequenceCorrelationService,
)
from .service import (
    MediaIdentityDecisionError,
    MediaIdentityDecisionService,
)
from .versions import (
    DEEP_CORRELATION_ARTIFACT_VERSION,
    DEEP_CORRELATION_INTERPRETATION_VERSION,
    DEEP_ORCHESTRATION_VERSION,
    DEEP_PATTERN_CORRELATION_VERSION,
    DEEP_SEQUENCE_CORRELATION_VERSION,
)


DEEP_CORRELATION_ARTIFACT_KEY = "deep-correlation-analysis"
DEEP_CORRELATION_ARTIFACT_TYPE = "deep_correlation_analysis"


class DeepCorrelationAnalysisError(RuntimeError):
    """The complete J4 correlation artifact cannot be trusted or published."""


@dataclass(frozen=True)
class DeepCorrelationArtifactRun:
    scan_id: int
    artifact_id: int
    reused: bool
    fingerprints: DeepFingerprintBundleRun
    interpretation: DeepCorrelationInterpretationRun
    sequence: DeepSequenceCorrelationRun
    patterns: DeepCorrelationPatternRun

    def __post_init__(self) -> None:
        if (
            isinstance(self.scan_id, bool)
            or not isinstance(self.scan_id, int)
            or self.scan_id < 1
            or isinstance(self.artifact_id, bool)
            or not isinstance(self.artifact_id, int)
            or self.artifact_id < 1
            or not isinstance(self.reused, bool)
        ):
            raise DeepCorrelationAnalysisError(
                "J4 artifact run identifiers are invalid."
            )
        if (
            self.fingerprints.scan_id != self.scan_id
            or self.interpretation.scan_id != self.scan_id
            or self.sequence.scan_id != self.scan_id
            or self.patterns.scan_id != self.scan_id
        ):
            raise DeepCorrelationAnalysisError(
                "J4 artifact run inputs belong to different scans."
            )


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
        raise DeepCorrelationAnalysisError(
            "J4 correlation metadata could not be serialized safely."
        ) from exc


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _json_ready(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    return value


def _valid_sha256(value: object) -> bool:
    digest = str(value or "").strip().casefold()
    return (
        len(digest) == 64
        and all(
            character in "0123456789abcdef"
            for character in digest
        )
    )


def _same_modified_at(first: object, second: object) -> bool:
    if first is None or second is None:
        return first is None and second is None
    try:
        return float(first) == float(second)
    except (TypeError, ValueError):
        return False


def _modality_payload(
    item: ModalityInterpretation | None,
) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "modality": item.modality,
        "left_file_id": item.left_file_id,
        "right_file_id": item.right_file_id,
        "band": item.band.value,
        "compared_samples": item.compared_samples,
        "coverage": item.coverage,
        "mean_similarity": item.mean_similarity,
        "median_similarity": item.median_similarity,
        "minimum_similarity": item.minimum_similarity,
        "alignment_shift": item.alignment_shift,
        "sufficient": item.sufficient,
    }


def _pair_payload(item: PairInterpretation) -> dict[str, Any]:
    return {
        "left_file_id": item.left_file_id,
        "right_file_id": item.right_file_id,
        "agreement": item.agreement.value,
        "video": _modality_payload(item.video),
        "audio": _modality_payload(item.audio),
    }


def _hypothesis_payload(
    item: SequenceHypothesis,
) -> dict[str, Any]:
    return {
        "file_id": item.file_id,
        "scan_id": item.scan_id,
        "result_revision": item.result_revision,
        "claimed_season": item.claimed_season,
        "claimed_episode": item.claimed_episode,
        "claimed_episode_end": item.claimed_episode_end,
        "candidate_key": item.candidate_key,
        "hypothesis_season": item.hypothesis_season,
        "hypothesis_episode": item.hypothesis_episode,
        "result_state": item.result_state.value,
        "support_strength": item.support_strength,
        "conflict_strength": item.conflict_strength,
        "margin": item.margin,
        "independent_categories": item.independent_categories,
        "content_support": item.content_support,
    }


def _sequence_observation_payload(
    item: SequenceOffsetObservation,
) -> dict[str, Any]:
    return {
        "season": item.season,
        "offset": item.offset,
        "supporting_file_ids": list(
            item.supporting_file_ids
        ),
        "usable_file_ids": list(item.usable_file_ids),
        "support_count": item.support_count,
        "usable_count": item.usable_count,
        "support_ratio": item.support_ratio,
        "longest_chain": item.longest_chain,
        "first_claimed_episode": item.first_claimed_episode,
        "last_claimed_episode": item.last_claimed_episode,
        "conflicted": item.conflicted,
    }


def _duplicate_payload(
    item: DuplicatePatternObservation,
) -> dict[str, Any]:
    return {
        "left_file_id": item.left_file_id,
        "right_file_id": item.right_file_id,
        "strength": item.strength.value,
        "agreement": item.agreement.value,
    }


def _swap_payload(
    item: SwapPatternObservation,
) -> dict[str, Any]:
    return {
        "left_file_id": item.left_file_id,
        "right_file_id": item.right_file_id,
        "left_claimed": list(item.left_claimed),
        "right_claimed": list(item.right_claimed),
        "left_hypothesis": list(item.left_hypothesis),
        "right_hypothesis": list(item.right_hypothesis),
        "status": item.status.value,
        "fingerprint_agreement": (
            None
            if item.fingerprint_agreement is None
            else item.fingerprint_agreement.value
        ),
        "fingerprint_similarity_support": (
            item.fingerprint_similarity_support
        ),
    }


def _cycle_payload(
    item: IdentityCycleObservation,
) -> dict[str, Any]:
    return {
        "file_ids": list(item.file_ids),
        "target_file_ids": list(item.target_file_ids),
        "claimed_coordinates": [
            list(value)
            for value in item.claimed_coordinates
        ],
        "hypothesis_coordinates": [
            list(value)
            for value in item.hypothesis_coordinates
        ],
    }


def _fingerprint_run_identity(
    item: DeepFingerprintCorrelationRun,
) -> dict[str, Any]:
    return {
        "algorithm_key": item.algorithm_key,
        "coverage_complete": item.coverage_complete,
        "manifest_artifact_id": item.manifest_artifact_id,
        "planned_file_count": item.planned_file_count,
        "completed_file_count": item.completed_file_count,
        "planned_pair_count": item.planned_pair_count,
        "completed_pair_count": item.completed_pair_count,
        "missing_file_ids": list(item.missing_file_ids),
        "failures": list(item.failures),
    }


def _output_payload(
    interpretation: DeepCorrelationInterpretationRun,
    sequence: DeepSequenceCorrelationRun,
    patterns: DeepCorrelationPatternRun,
) -> dict[str, Any]:
    pattern_analysis: CorrelationPatternAnalysis = (
        patterns.patterns
    )
    return {
        "coverage": {
            "complete_modalities": list(
                interpretation.complete_modalities
            ),
            "fully_multimodal": interpretation.fully_multimodal,
            "planned_pair_count": interpretation.planned_pair_count,
            "interpreted_pair_count": interpretation.pair_count,
            "planned_file_count": sequence.planned_file_count,
            "valid_hypothesis_count": sequence.hypothesis_count,
            "missing_scan_file_ids": list(
                sequence.missing_scan_file_ids
            ),
            "invalid_scan_file_ids": list(
                sequence.invalid_scan_file_ids
            ),
            "sequence_peer_coverage_complete": (
                sequence.hypothesis_count
                == sequence.planned_file_count
                and not sequence.missing_scan_file_ids
                and not sequence.invalid_scan_file_ids
            ),
        },
        "pair_interpretations": [
            _pair_payload(item)
            for item in interpretation.pairs
        ],
        "sequence": {
            "hypothesis_count": sequence.hypothesis_count,
            "missing_scan_file_ids": list(
                sequence.missing_scan_file_ids
            ),
            "invalid_scan_file_ids": list(
                sequence.invalid_scan_file_ids
            ),
            "observations": [
                _sequence_observation_payload(item)
                for item in sequence.analysis.observations
            ],
            "conflicted_seasons": list(
                sequence.analysis.conflicted_seasons
            ),
            "pattern_conflict_seasons": list(
                patterns.sequence_conflict_seasons
            ),
            "authoritative_observations": [
                _sequence_observation_payload(item)
                for item in (
                    patterns.authoritative_sequence_observations
                )
            ],
        },
        "duplicates": [
            _duplicate_payload(item)
            for item in pattern_analysis.duplicate_observations
        ],
        "swaps": [
            _swap_payload(item)
            for item in pattern_analysis.swap_observations
        ],
        "identity_cycles": [
            _cycle_payload(item)
            for item in pattern_analysis.identity_cycles
        ],
        "ambiguous_claim_file_ids": list(
            pattern_analysis.ambiguous_claim_file_ids
        ),
    }


def _artifact_payload(
    identity: Mapping[str, Any],
    output: Mapping[str, Any],
) -> dict[str, Any]:
    body = {
        "identity": _json_ready(identity),
        "analysis_complete": True,
        "output": _json_ready(output),
    }
    body["artifact_output_sha256"] = hashlib.sha256(
        _canonical_json(body).encode("utf-8")
    ).hexdigest()
    return body


class DeepCorrelationAnalysisService:
    """Orchestrate J3/J4 and publish one sealed non-actionable Deep artifact."""

    def __init__(
        self,
        database: Database,
        *,
        correlation_policy: DeepCorrelationPolicy | None = None,
        match_policy: FingerprintMatchPolicy | None = None,
        interpretation_policy: (
            CorrelationInterpretationPolicy | None
        ) = None,
        sequence_policy: SequenceOffsetPolicy | None = None,
        video_service: DeepFingerprintCorrelationService | None = None,
        audio_service_factory: Callable[
            [str],
            DeepAudioFingerprintCorrelationService,
        ] | None = None,
    ) -> None:
        self.database = database
        self.correlation_policy = (
            correlation_policy or DeepCorrelationPolicy()
        )
        self.match_policy = match_policy or FingerprintMatchPolicy()
        self.interpretation_policy = (
            interpretation_policy
            or CorrelationInterpretationPolicy()
        )
        self.sequence_policy = (
            sequence_policy or SequenceOffsetPolicy()
        )
        self.video_service = (
            video_service
            if video_service is not None
            else DeepFingerprintCorrelationService(
                database,
                correlation_policy=self.correlation_policy,
                match_policy=self.match_policy,
            )
        )
        self.audio_service_factory = audio_service_factory
        self.sequence_service = DeepSequenceCorrelationService(
            database,
            correlation_policy=self.correlation_policy,
            sequence_policy=self.sequence_policy,
        )

    def _audio_service(
        self,
        preferred_language: str,
    ) -> DeepAudioFingerprintCorrelationService:
        if self.audio_service_factory is not None:
            return self.audio_service_factory(
                preferred_language
            )
        return DeepAudioFingerprintCorrelationService(
            self.database,
            preferred_language=preferred_language,
            correlation_policy=self.correlation_policy,
            match_policy=self.match_policy,
        )

    @staticmethod
    def _target_context(
        conn: sqlite3.Connection,
        scan_id: int,
    ) -> tuple[
        dict[str, Any],
        list[dict[str, Any]],
        dict[str, Any],
        int,
        str,
        str,
    ]:
        try:
            scan, _candidates, evidence = (
                MediaIdentityDecisionService._scan_snapshot(
                    conn,
                    int(scan_id),
                )
            )
        except MediaIdentityDecisionError as exc:
            raise DeepCorrelationAnalysisError(
                "J4 target scan was not found."
            ) from exc
        current, file_row = (
            MediaIdentityDecisionService._scan_snapshot_is_current(
                conn,
                scan,
                evidence,
            )
        )
        if not current or file_row is None:
            raise DeepCorrelationAnalysisError(
                "J4 target scan is stale."
            )
        revision = result_revision(scan)
        claimed = MediaIdentityDecisionService._claimed_identity(
            scan
        )
        sealed_revision, decision_digest = (
            MediaIdentityDecisionService._decision_token(
                claimed
            )
        )
        if (
            revision <= 0
            or sealed_revision != revision
            or not _valid_sha256(decision_digest)
        ):
            raise DeepCorrelationAnalysisError(
                "J4 target decision snapshot is not sealed."
            )
        preferred_language = str(
            claimed.get("scan_language") or ""
        ).strip().casefold()
        return (
            scan,
            evidence,
            dict(file_row),
            revision,
            decision_digest,
            preferred_language,
        )

    @staticmethod
    def _identity(
        *,
        scan: Mapping[str, Any],
        file_row: Mapping[str, Any],
        decision_digest: str,
        fingerprints: DeepFingerprintBundleRun,
        interpretation: DeepCorrelationInterpretationRun,
        sequence: DeepSequenceCorrelationRun,
        patterns: DeepCorrelationPatternRun,
    ) -> dict[str, Any]:
        return {
            "artifact_version": DEEP_CORRELATION_ARTIFACT_VERSION,
            "deep_orchestration_version": (
                DEEP_ORCHESTRATION_VERSION
            ),
            "interpretation_version": (
                DEEP_CORRELATION_INTERPRETATION_VERSION
            ),
            "sequence_version": DEEP_SEQUENCE_CORRELATION_VERSION,
            "pattern_version": DEEP_PATTERN_CORRELATION_VERSION,
            "scan_id": int(scan["id"]),
            "target_file_id": int(scan["file_id"]),
            "result_revision": interpretation.result_revision,
            "decision_snapshot_sha256": decision_digest,
            "metadata_signature": str(
                scan.get("metadata_signature") or ""
            ),
            "file_sha256": str(
                scan.get("file_sha256") or ""
            ).strip().casefold(),
            "file_size_bytes": int(
                scan.get("file_size_bytes") or 0
            ),
            "file_modified_at": scan.get(
                "file_modified_at"
            ),
            "correlation_plan_signature": (
                interpretation.correlation_plan_signature
            ),
            "fingerprints": {
                "complete_modalities": list(
                    fingerprints.complete_modalities
                ),
                "video": _fingerprint_run_identity(
                    fingerprints.video
                ),
                "audio": _fingerprint_run_identity(
                    fingerprints.audio
                ),
            },
            "interpretation_policy_signature": (
                interpretation.policy_signature
            ),
            "interpretation_policy": _json_ready(
                interpretation.policy_identity
            ),
            "sequence_policy_signature": (
                sequence.sequence_policy_signature
            ),
            "sequence_policy": _json_ready(
                sequence.sequence_policy_identity
            ),
            "peer_hypotheses": [
                _hypothesis_payload(item)
                for item in sequence.hypotheses
            ],
            "missing_scan_file_ids": list(
                sequence.missing_scan_file_ids
            ),
            "invalid_scan_file_ids": list(
                sequence.invalid_scan_file_ids
            ),
            "sequence_conflict_seasons": list(
                patterns.sequence_conflict_seasons
            ),
        }

    @staticmethod
    def _cache_key(
        identity: Mapping[str, Any],
    ) -> str:
        return hashlib.sha256(
            _canonical_json(
                _json_ready(identity)
            ).encode("utf-8")
        ).hexdigest()

    def _validate_peer_hypotheses(
        self,
        conn: sqlite3.Connection,
        *,
        plan,
        sequence: DeepSequenceCorrelationRun,
        target_file_id: int,
    ) -> bool:
        by_file = {
            item.file_id: item
            for item in sequence.hypotheses
        }
        missing = set(sequence.missing_scan_file_ids)
        invalid = set(sequence.invalid_scan_file_ids)

        for item in plan.files:
            file_id = item.file_id
            expected = by_file.get(file_id)
            if expected is not None:
                if file_id == target_file_id:
                    current = (
                        self.sequence_service._validated_hypothesis(
                            conn,
                            expected.scan_id,
                        )
                    )
                    if current != expected:
                        return False
                else:
                    current, had_scan = (
                        self.sequence_service._best_current_hypothesis(
                            conn,
                            file_id,
                        )
                    )
                    if not had_scan or current != expected:
                        return False
                continue
            if file_id == target_file_id:
                return False
            if file_id in missing:
                if self.sequence_service._scan_ids(
                    conn,
                    file_id,
                ):
                    return False
                continue
            if file_id in invalid:
                current, had_scan = (
                    self.sequence_service._best_current_hypothesis(
                        conn,
                        file_id,
                    )
                )
                if current is not None or not had_scan:
                    return False
                continue
            return False
        return True

    def _validate_modality(
        self,
        conn: sqlite3.Connection,
        *,
        service: DeepFingerprintCorrelationService,
        run: DeepFingerprintCorrelationRun,
        scan_id: int,
        revision: int,
        plan_signature: str,
    ) -> bool:
        if not run.coverage_complete:
            return (
                run.manifest_artifact_id is None
            )
        if run.manifest_artifact_id is None:
            return False
        return service.validate_manifest_artifact(
            conn,
            int(run.manifest_artifact_id),
            scan_id=scan_id,
            result_revision=revision,
            plan_signature=plan_signature,
            expected_comparisons=run.comparisons,
        )

    def _persist(
        self,
        *,
        baseline_scan: Mapping[str, Any],
        baseline_file: Mapping[str, Any],
        decision_digest: str,
        audio_service: DeepAudioFingerprintCorrelationService,
        fingerprints: DeepFingerprintBundleRun,
        interpretation: DeepCorrelationInterpretationRun,
        sequence: DeepSequenceCorrelationRun,
        patterns: DeepCorrelationPatternRun,
    ) -> tuple[int, bool]:
        identity = self._identity(
            scan=baseline_scan,
            file_row=baseline_file,
            decision_digest=decision_digest,
            fingerprints=fingerprints,
            interpretation=interpretation,
            sequence=sequence,
            patterns=patterns,
        )
        output = _output_payload(
            interpretation,
            sequence,
            patterns,
        )
        payload = _artifact_payload(
            identity,
            output,
        )
        cache_key = self._cache_key(identity)

        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            (
                current_scan,
                _evidence,
                current_file,
                current_revision,
                current_digest,
                _language,
            ) = self._target_context(
                conn,
                int(baseline_scan["id"]),
            )
            if (
                current_revision
                != interpretation.result_revision
                or current_digest != decision_digest
                or str(
                    current_scan.get(
                        "metadata_signature"
                    ) or ""
                )
                != str(
                    baseline_scan.get(
                        "metadata_signature"
                    ) or ""
                )
                or str(
                    current_scan.get("file_sha256") or ""
                ).strip().casefold()
                != str(
                    baseline_scan.get("file_sha256") or ""
                ).strip().casefold()
                or int(
                    current_scan.get("file_size_bytes") or 0
                )
                != int(
                    baseline_scan.get("file_size_bytes") or 0
                )
                or not _same_modified_at(
                    current_scan.get("file_modified_at"),
                    baseline_scan.get("file_modified_at"),
                )
            ):
                raise DeepCorrelationAnalysisError(
                    "J4 target publication changed before artifact publication."
                )

            plan = plan_deep_correlation(
                conn,
                file_id=int(current_scan["file_id"]),
                policy=self.correlation_policy,
            )
            if (
                plan.plan_signature
                != interpretation.correlation_plan_signature
            ):
                raise DeepCorrelationAnalysisError(
                    "J4 correlation cohort changed before artifact publication."
                )

            if not self._validate_peer_hypotheses(
                conn,
                plan=plan,
                sequence=sequence,
                target_file_id=int(current_scan["file_id"]),
            ):
                raise DeepCorrelationAnalysisError(
                    "J4 peer resolver inputs changed before artifact publication."
                )

            if not self._validate_modality(
                conn,
                service=self.video_service,
                run=fingerprints.video,
                scan_id=int(current_scan["id"]),
                revision=current_revision,
                plan_signature=plan.plan_signature,
            ):
                raise DeepCorrelationAnalysisError(
                    "J4 video fingerprint matrix failed final validation."
                )
            if not self._validate_modality(
                conn,
                service=audio_service,
                run=fingerprints.audio,
                scan_id=int(current_scan["id"]),
                revision=current_revision,
                plan_signature=plan.plan_signature,
            ):
                raise DeepCorrelationAnalysisError(
                    "J4 audio fingerprint matrix failed final validation."
                )

            recomputed_interpretation = (
                interpret_fingerprint_bundle(
                    fingerprints,
                    policy=self.interpretation_policy,
                )
            )
            if recomputed_interpretation != interpretation:
                raise DeepCorrelationAnalysisError(
                    "J4 fingerprint interpretation changed before publication."
                )
            recomputed_sequence_analysis = detect_sequence_offsets(
                sequence.hypotheses,
                policy=self.sequence_policy,
            )
            if recomputed_sequence_analysis != sequence.analysis:
                raise DeepCorrelationAnalysisError(
                    "J4 sequence interpretation changed before publication."
                )
            recomputed_patterns = correlate_deep_patterns(
                interpretation,
                sequence,
            )
            if recomputed_patterns != patterns:
                raise DeepCorrelationAnalysisError(
                    "J4 cross-file patterns changed before publication."
                )

            current_identity = self._identity(
                scan=current_scan,
                file_row=current_file,
                decision_digest=current_digest,
                fingerprints=fingerprints,
                interpretation=interpretation,
                sequence=sequence,
                patterns=patterns,
            )
            if current_identity != identity:
                raise DeepCorrelationAnalysisError(
                    "J4 artifact identity changed before publication."
                )

            cursor = conn.execute(
                """INSERT OR IGNORE INTO media_identity_artifacts(
                     file_id,artifact_type,analyzer_key,analyzer_version,
                     cache_key,status,profile,source_kind,source_ref,
                     source_signature,file_size_bytes,file_modified_at,
                     payload_json
                   ) VALUES (
                     ?,?,?,?,?,'complete','deep',
                     'episode_identity_correlation',?,?,?,?,?
                   )""",
                (
                    int(current_scan["file_id"]),
                    DEEP_CORRELATION_ARTIFACT_TYPE,
                    DEEP_CORRELATION_ARTIFACT_KEY,
                    str(DEEP_CORRELATION_ARTIFACT_VERSION),
                    cache_key,
                    f"scan:{int(current_scan['id'])}",
                    plan.plan_signature,
                    int(current_scan.get("file_size_bytes") or 0),
                    current_scan.get("file_modified_at"),
                    _canonical_json(payload),
                ),
            )
            inserted = cursor.rowcount == 1
            row = conn.execute(
                """SELECT * FROM media_identity_artifacts
                   WHERE file_id=? AND artifact_type=?
                     AND analyzer_key=? AND analyzer_version=?
                     AND cache_key=?
                   ORDER BY id DESC LIMIT 1""",
                (
                    int(current_scan["file_id"]),
                    DEEP_CORRELATION_ARTIFACT_TYPE,
                    DEEP_CORRELATION_ARTIFACT_KEY,
                    str(DEEP_CORRELATION_ARTIFACT_VERSION),
                    cache_key,
                ),
            ).fetchone()
            if row is None:
                raise DeepCorrelationAnalysisError(
                    "InfoMancer could not persist the J4 correlation artifact."
                )
            persisted = dict(row)
            valid_existing = (
                str(persisted.get("status") or "") == "complete"
                and str(persisted.get("profile") or "") == "deep"
                and str(persisted.get("source_kind") or "")
                == "episode_identity_correlation"
                and str(persisted.get("source_ref") or "")
                == f"scan:{int(current_scan['id'])}"
                and str(persisted.get("source_signature") or "")
                == plan.plan_signature
                and int(
                    persisted.get("file_size_bytes") or 0
                )
                == int(current_scan.get("file_size_bytes") or 0)
                and _same_modified_at(
                    persisted.get("file_modified_at"),
                    current_scan.get("file_modified_at"),
                )
                and str(persisted.get("payload_json") or "")
                == _canonical_json(payload)
            )
            if not valid_existing:
                repair = conn.execute(
                    """UPDATE media_identity_artifacts
                       SET status='complete',profile='deep',
                           source_kind='episode_identity_correlation',
                           source_ref=?,source_signature=?,
                           file_size_bytes=?,file_modified_at=?,
                           payload_json=?,error='',
                           updated_at=CURRENT_TIMESTAMP,
                           last_used_at=CURRENT_TIMESTAMP
                       WHERE id=? AND cache_key=?""",
                    (
                        f"scan:{int(current_scan['id'])}",
                        plan.plan_signature,
                        int(current_scan.get("file_size_bytes") or 0),
                        current_scan.get("file_modified_at"),
                        _canonical_json(payload),
                        int(persisted["id"]),
                        cache_key,
                    ),
                )
                if repair.rowcount != 1:
                    raise DeepCorrelationAnalysisError(
                        "InfoMancer could not repair the J4 artifact safely."
                    )
                inserted = True
            else:
                conn.execute(
                    """UPDATE media_identity_artifacts
                       SET last_used_at=CURRENT_TIMESTAMP
                       WHERE id=?""",
                    (int(persisted["id"]),),
                )
            return int(persisted["id"]), inserted

    def run(
        self,
        scan_id: int,
    ) -> DeepCorrelationArtifactRun:
        try:
            return self._run(int(scan_id))
        except DeepCorrelationAnalysisError:
            raise
        except (
            CorrelationInterpretationError,
            DeepCorrelationPatternError,
            DeepFingerprintBundleError,
            DeepFingerprintCorrelationError,
            DeepFingerprintError,
            DeepIdentityError,
            DeepSequenceCorrelationError,
            FingerprintError,
            SequenceCorrelationError,
            sqlite3.Error,
        ) as exc:
            raise DeepCorrelationAnalysisError(
                "J4 cross-file correlation could not complete safely: "
                f"{exc}"
            ) from exc

    def _run(
        self,
        scan_id: int,
    ) -> DeepCorrelationArtifactRun:
        with self.database.connect() as conn:
            (
                baseline_scan,
                _evidence,
                baseline_file,
                revision,
                decision_digest,
                preferred_language,
            ) = self._target_context(
                conn,
                int(scan_id),
            )

        audio_service = self._audio_service(
            preferred_language
        )
        bundle_service = DeepFingerprintBundleService(
            self.database,
            video_service=self.video_service,
            audio_service=audio_service,
            correlation_policy=self.correlation_policy,
            match_policy=self.match_policy,
        )
        fingerprints = bundle_service.run(
            int(scan_id)
        )
        if fingerprints.result_revision != revision:
            raise DeepCorrelationAnalysisError(
                "J4 target publication changed during fingerprint preparation."
            )

        interpretation = interpret_fingerprint_bundle(
            fingerprints,
            policy=self.interpretation_policy,
        )
        sequence = self.sequence_service.run(
            int(scan_id),
            interpretation,
        )
        patterns = correlate_deep_patterns(
            interpretation,
            sequence,
        )

        artifact_id, inserted = self._persist(
            baseline_scan=baseline_scan,
            baseline_file=baseline_file,
            decision_digest=decision_digest,
            audio_service=audio_service,
            fingerprints=fingerprints,
            interpretation=interpretation,
            sequence=sequence,
            patterns=patterns,
        )
        return DeepCorrelationArtifactRun(
            scan_id=int(scan_id),
            artifact_id=artifact_id,
            reused=not inserted,
            fingerprints=fingerprints,
            interpretation=interpretation,
            sequence=sequence,
            patterns=patterns,
        )

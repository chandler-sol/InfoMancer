from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Any, Mapping

from ..db import Database
from .decision_snapshot import result_revision
from .deep import (
    DeepCorrelationPlan,
    DeepCorrelationPolicy,
    plan_deep_correlation,
)
from .fingerprint import (
    ContentFingerprint,
    FingerprintComparison,
    FingerprintError,
    FingerprintMatchPolicy,
    compare_content_fingerprints,
    fingerprint_comparison_from_payload,
    fingerprint_from_payload,
)
from .fingerprint_service import (
    DEEP_FINGERPRINT_ARTIFACT_VERSION,
    DeepFingerprintArtifactService,
    DeepFingerprintError,
)
from .service import MediaIdentityDecisionService
from .versions import (
    DEEP_FINGERPRINT_CONTRACT_VERSION,
    DEEP_FINGERPRINT_CORRELATION_VERSION,
    DEEP_FINGERPRINT_MATCH_VERSION,
    DEEP_ORCHESTRATION_VERSION,
)


DEEP_FINGERPRINT_MANIFEST_KEY = (
    "deep-fingerprint-correlation:video-dhash64-sequence"
)
DEEP_FINGERPRINT_MANIFEST_VERSION = str(
    DEEP_FINGERPRINT_CORRELATION_VERSION
)


class DeepFingerprintCorrelationError(RuntimeError):
    """The Deep fingerprint cohort changed or could not be proven complete."""


@dataclass(frozen=True)
class DeepFingerprintCorrelationRun:
    scan_id: int
    algorithm_key: str
    correlation_plan_signature: str
    planned_file_count: int
    completed_file_count: int
    planned_pair_count: int
    completed_pair_count: int
    reused_fingerprint_count: int
    generated_fingerprint_count: int
    manifest_artifact_id: int | None
    coverage_complete: bool
    missing_file_ids: tuple[int, ...]
    comparisons: tuple[FingerprintComparison, ...]
    failures: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            isinstance(self.scan_id, bool)
            or not isinstance(self.scan_id, int)
            or self.scan_id < 1
        ):
            raise DeepFingerprintCorrelationError(
                "Fingerprint correlation scan ID must be positive."
            )
        if not isinstance(self.algorithm_key, str) or not self.algorithm_key.strip():
            raise DeepFingerprintCorrelationError(
                "Fingerprint correlation algorithm key is invalid."
            )
        signature = str(self.correlation_plan_signature or "").strip().casefold()
        if (
            len(signature) != 64
            or any(character not in "0123456789abcdef" for character in signature)
        ):
            raise DeepFingerprintCorrelationError(
                "Fingerprint correlation plan signature is invalid."
            )
        for label, value in (
            ("planned file count", self.planned_file_count),
            ("completed file count", self.completed_file_count),
            ("planned pair count", self.planned_pair_count),
            ("completed pair count", self.completed_pair_count),
            ("reused fingerprint count", self.reused_fingerprint_count),
            ("generated fingerprint count", self.generated_fingerprint_count),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise DeepFingerprintCorrelationError(
                    f"Fingerprint correlation {label} is invalid."
                )
        if self.planned_file_count < 1:
            raise DeepFingerprintCorrelationError(
                "Fingerprint correlation requires at least one planned file."
            )
        if self.completed_file_count > self.planned_file_count:
            raise DeepFingerprintCorrelationError(
                "Fingerprint correlation completed-file count exceeds its plan."
            )
        maximum_pairs = (
            self.planned_file_count
            * (self.planned_file_count - 1)
            // 2
        )
        if (
            self.planned_pair_count > maximum_pairs
            or self.completed_pair_count > self.planned_pair_count
            or self.completed_pair_count != len(self.comparisons)
        ):
            raise DeepFingerprintCorrelationError(
                "Fingerprint correlation pair accounting is inconsistent."
            )
        if not isinstance(self.coverage_complete, bool):
            raise DeepFingerprintCorrelationError(
                "Fingerprint correlation coverage flag must be boolean."
            )
        if any(
            not isinstance(item, FingerprintComparison)
            or item.algorithm_key != self.algorithm_key
            for item in self.comparisons
        ):
            raise DeepFingerprintCorrelationError(
                "Fingerprint correlation comparisons are malformed."
            )
        missing = tuple(self.missing_file_ids)
        if (
            len(set(missing)) != len(missing)
            or tuple(sorted(missing)) != missing
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
                for value in missing
            )
        ):
            raise DeepFingerprintCorrelationError(
                "Fingerprint correlation missing-file IDs are invalid."
            )
        if any(
            not isinstance(item, str) or not item
            for item in self.failures
        ):
            raise DeepFingerprintCorrelationError(
                "Fingerprint correlation failures are malformed."
            )
        if self.coverage_complete:
            if (
                isinstance(self.manifest_artifact_id, bool)
                or not isinstance(self.manifest_artifact_id, int)
                or self.manifest_artifact_id < 1
                or self.completed_file_count != self.planned_file_count
                or self.completed_pair_count != self.planned_pair_count
                or self.missing_file_ids
                or self.failures
            ):
                raise DeepFingerprintCorrelationError(
                    "Complete fingerprint correlation lacks sealed full coverage."
                )
        elif self.manifest_artifact_id is not None:
            raise DeepFingerprintCorrelationError(
                "Incomplete fingerprint correlation cannot carry a completion manifest."
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
        raise DeepFingerprintCorrelationError(
            "Fingerprint correlation metadata could not be serialized safely."
        ) from exc


def _same_modified_at(first: object, second: object) -> bool:
    if first is None or second is None:
        return first is None and second is None
    try:
        return float(first) == float(second)
    except (TypeError, ValueError):
        return False


def _comparison_payload(item: FingerprintComparison) -> dict[str, Any]:
    return {
        "left_file_id": item.left_file_id,
        "right_file_id": item.right_file_id,
        "algorithm_key": item.algorithm_key,
        "algorithm_version": item.algorithm_version,
        "compared_samples": item.compared_samples,
        "alignment_shift": item.alignment_shift,
        "coverage": item.coverage,
        "mean_similarity": item.mean_similarity,
        "median_similarity": item.median_similarity,
        "minimum_similarity": item.minimum_similarity,
    }

FINGERPRINT_MANIFEST_OUTPUT_SEAL_FIELD = "manifest_output_sha256"


def _manifest_payload(
    identity: Mapping[str, Any],
    comparisons: tuple[FingerprintComparison, ...],
) -> dict[str, Any]:
    body = {
        "identity": dict(identity),
        "coverage_complete": True,
        "comparisons": [
            _comparison_payload(item)
            for item in comparisons
        ],
    }
    body[FINGERPRINT_MANIFEST_OUTPUT_SEAL_FIELD] = hashlib.sha256(
        _canonical_json(body).encode("utf-8")
    ).hexdigest()
    return body




class DeepFingerprintCorrelationService:
    """Build a complete, candidate-neutral pairwise fingerprint matrix for J4."""

    def __init__(
        self,
        database: Database,
        *,
        artifact_service: DeepFingerprintArtifactService | None = None,
        correlation_policy: DeepCorrelationPolicy | None = None,
        match_policy: FingerprintMatchPolicy | None = None,
    ) -> None:
        self.database = database
        self.artifact_service = (
            artifact_service
            if artifact_service is not None
            else DeepFingerprintArtifactService(database)
        )
        self.correlation_policy = (
            correlation_policy or DeepCorrelationPolicy()
        )
        self.match_policy = match_policy or FingerprintMatchPolicy()
        self.algorithm = self.artifact_service.algorithm
        self.manifest_key = (
            f"deep-fingerprint-correlation:{self.algorithm.key}"
        )
        self.manifest_version = DEEP_FINGERPRINT_MANIFEST_VERSION
        self.manifest_source_kind = (
            f"deep_fingerprint:{self.algorithm.key}"
        )

    @staticmethod
    def _scan_context(
        conn: sqlite3.Connection,
        scan_id: int,
    ) -> tuple[dict[str, Any], int]:
        row = conn.execute(
            "SELECT * FROM media_identity_scans WHERE id=?",
            (int(scan_id),),
        ).fetchone()
        if row is None:
            raise DeepFingerprintCorrelationError(
                "Episode Identity scan was not found."
            )
        scan = dict(row)
        evidence = [
            dict(item)
            for item in conn.execute(
                """SELECT * FROM media_identity_evidence
                   WHERE scan_id=? ORDER BY id""",
                (int(scan_id),),
            ).fetchall()
        ]
        current, _ = MediaIdentityDecisionService._scan_snapshot_is_current(
            conn,
            scan,
            evidence,
        )
        revision = result_revision(scan)
        if (
            not current
            or scan["status"] != "complete"
            or revision <= 0
        ):
            raise DeepFingerprintCorrelationError(
                "Deep fingerprint correlation requires a current sealed scan."
            )
        return scan, revision

    def _plan(
        self,
        conn: sqlite3.Connection,
        file_id: int,
    ) -> DeepCorrelationPlan:
        return plan_deep_correlation(
            conn,
            file_id=int(file_id),
            policy=self.correlation_policy,
        )

    def _child_is_current(
        self,
        conn: sqlite3.Connection,
        *,
        artifact_id: int,
        expected: ContentFingerprint,
    ) -> bool:
        row = conn.execute(
            """SELECT * FROM media_identity_artifacts WHERE id=?""",
            (int(artifact_id),),
        ).fetchone()
        if row is None:
            return False
        item = dict(row)
        if (
            int(item.get("file_id") or 0) != expected.file_id
            or str(item.get("artifact_type") or "")
            != "content_fingerprint"
            or str(item.get("analyzer_key") or "")
            != self.artifact_service.analyzer_key
            or str(item.get("analyzer_version") or "")
            != self.artifact_service.analyzer_version
            or str(item.get("status") or "") != "complete"
            or str(item.get("profile") or "") != "deep"
            or str(item.get("source_kind") or "")
            != expected.source_kind
            or str(item.get("source_signature") or "")
            != expected.source_signature
            or str(item.get("cache_key") or "")
            != expected.cache_key()
        ):
            return False
        try:
            payload = json.loads(str(item.get("payload_json") or "{}"))
            persisted = fingerprint_from_payload(payload)
        except (TypeError, ValueError, json.JSONDecodeError, FingerprintError):
            return False
        if persisted != expected:
            return False

        try:
            snapshot = self.artifact_service._file_snapshot(
                conn,
                expected.file_id,
            )
        except DeepFingerprintError:
            return False
        return (
            self.artifact_service._fingerprint_matches_snapshot(
                expected,
                snapshot,
            )
            and str(snapshot["current_sha256"]) == expected.file_sha256
            and int(snapshot["runtime_ms"]) == expected.runtime_ms
            and int(snapshot["size_bytes"] or 0)
            == int(item.get("file_size_bytes") or 0)
            and _same_modified_at(
                snapshot["modified_at"],
                item.get("file_modified_at"),
            )
        )

    def _manifest_identity(
        self,
        *,
        scan: Mapping[str, Any],
        revision: int,
        plan: DeepCorrelationPlan,
        children: Mapping[int, tuple[int, ContentFingerprint]],
    ) -> dict[str, Any]:
        return {
            "deep_orchestration_version": DEEP_ORCHESTRATION_VERSION,
            "fingerprint_contract_version": DEEP_FINGERPRINT_CONTRACT_VERSION,
            "fingerprint_algorithm": self.algorithm.identity_payload(),
            "fingerprint_match_version": DEEP_FINGERPRINT_MATCH_VERSION,
            "fingerprint_correlation_version": (
                DEEP_FINGERPRINT_CORRELATION_VERSION
            ),
            "scan_id": int(scan["id"]),
            "result_revision": int(revision),
            "target_file_id": int(scan["file_id"]),
            "correlation_policy": self.correlation_policy.identity_payload(),
            "correlation_plan_signature": plan.plan_signature,
            "correlation_file_ids": [
                item.file_id for item in plan.files
            ],
            "comparison_pairs": [
                [left, right]
                for left, right in plan.comparison_pairs
            ],
            "match_policy": {
                "max_candidates": self.match_policy.max_candidates,
                "max_samples_per_fingerprint": (
                    self.match_policy.max_samples_per_fingerprint
                ),
                "max_alignment_shift": (
                    self.match_policy.max_alignment_shift
                ),
                "min_compared_samples": (
                    self.match_policy.min_compared_samples
                ),
            },
            "fingerprints": [
                {
                    "file_id": file_id,
                    "artifact_id": artifact_id,
                    "cache_key": fingerprint.cache_key(),
                    "output_sha256": fingerprint.output_seal(),
                    "file_sha256": fingerprint.file_sha256,
                    "source_signature": fingerprint.source_signature,
                }
                for file_id, (artifact_id, fingerprint)
                in sorted(children.items())
            ],
        }

    @staticmethod
    def _cache_key(identity: Mapping[str, Any]) -> str:
        return hashlib.sha256(
            _canonical_json(dict(identity)).encode("utf-8")
        ).hexdigest()

    def _validate_manifest(
        self,
        row: Mapping[str, Any],
        *,
        scan: Mapping[str, Any],
        identity: Mapping[str, Any],
        comparisons: tuple[FingerprintComparison, ...],
    ) -> bool:
        if (
            int(row.get("file_id") or 0) != int(scan["file_id"])
            or str(row.get("artifact_type") or "")
            != "deep_fingerprint_manifest"
            or str(row.get("analyzer_key") or "")
            != self.manifest_key
            or str(row.get("analyzer_version") or "")
            != self.manifest_version
            or str(row.get("status") or "") != "complete"
            or str(row.get("profile") or "") != "deep"
            or str(row.get("source_kind") or "")
            != self.manifest_source_kind
            or str(row.get("source_ref") or "")
            != f"scan:{int(scan['id'])}"
            or str(row.get("source_signature") or "")
            != str(identity["correlation_plan_signature"])
            or str(row.get("cache_key") or "")
            != self._cache_key(identity)
        ):
            return False
        try:
            payload = json.loads(str(row.get("payload_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        expected_payload = _manifest_payload(
            identity,
            comparisons,
        )
        return payload == expected_payload

    def validate_manifest_artifact(
        self,
        conn: sqlite3.Connection,
        artifact_id: int,
        *,
        scan_id: int,
        result_revision: int,
        plan_signature: str,
        expected_comparisons: tuple[
            FingerprintComparison, ...
        ] | None = None,
    ) -> bool:
        """Revalidate one sealed J3 matrix inside the caller's transaction."""

        row = conn.execute(
            """SELECT * FROM media_identity_artifacts WHERE id=?""",
            (int(artifact_id),),
        ).fetchone()
        if row is None:
            return False
        persisted = dict(row)

        try:
            current_scan, current_revision = self._scan_context(
                conn,
                int(scan_id),
            )
        except DeepFingerprintCorrelationError:
            return False
        if current_revision != int(result_revision):
            return False

        current_plan = self._plan(
            conn,
            int(current_scan["file_id"]),
        )
        if (
            current_plan.plan_signature != str(plan_signature or "")
            or str(persisted.get("source_signature") or "")
            != current_plan.plan_signature
        ):
            return False

        try:
            payload = json.loads(
                str(persisted.get("payload_json") or "{}")
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        if not isinstance(payload, Mapping):
            return False
        identity = payload.get("identity")
        raw_comparisons = payload.get("comparisons")
        if (
            not isinstance(identity, Mapping)
            or not isinstance(raw_comparisons, list)
            or payload.get("coverage_complete") is not True
        ):
            return False

        raw_children = identity.get("fingerprints")
        if not isinstance(raw_children, list):
            return False

        children: dict[int, tuple[int, ContentFingerprint]] = {}
        for raw_child in raw_children:
            if not isinstance(raw_child, Mapping):
                return False
            try:
                child_id = int(raw_child["artifact_id"])
                file_id = int(raw_child["file_id"])
            except (KeyError, TypeError, ValueError):
                return False
            if (
                child_id < 1
                or file_id < 1
                or file_id in children
            ):
                return False
            child_row = conn.execute(
                """SELECT * FROM media_identity_artifacts WHERE id=?""",
                (child_id,),
            ).fetchone()
            if child_row is None:
                return False
            child = dict(child_row)
            try:
                child_payload = json.loads(
                    str(child.get("payload_json") or "{}")
                )
                fingerprint = fingerprint_from_payload(
                    child_payload
                )
            except (
                TypeError,
                ValueError,
                json.JSONDecodeError,
                FingerprintError,
            ):
                return False
            if (
                fingerprint.file_id != file_id
                or str(raw_child.get("cache_key") or "")
                != fingerprint.cache_key()
                or str(raw_child.get("output_sha256") or "")
                != fingerprint.output_seal()
                or str(raw_child.get("file_sha256") or "")
                != fingerprint.file_sha256
                or str(raw_child.get("source_signature") or "")
                != fingerprint.source_signature
                or not self._child_is_current(
                    conn,
                    artifact_id=child_id,
                    expected=fingerprint,
                )
            ):
                return False
            children[file_id] = (child_id, fingerprint)

        expected_identity = self._manifest_identity(
            scan=current_scan,
            revision=current_revision,
            plan=current_plan,
            children=children,
        )
        if dict(identity) != expected_identity:
            return False

        if len(raw_comparisons) != len(
            current_plan.comparison_pairs
        ):
            return False
        comparisons: list[FingerprintComparison] = []
        try:
            for pair, raw in zip(
                current_plan.comparison_pairs,
                raw_comparisons,
            ):
                comparison = fingerprint_comparison_from_payload(
                    raw
                )
                if (
                    (
                        comparison.left_file_id,
                        comparison.right_file_id,
                    )
                    != tuple(pair)
                    or comparison.algorithm_key
                    != self.algorithm.key
                    or comparison.algorithm_version
                    != self.algorithm.version
                    or abs(comparison.alignment_shift)
                    > self.match_policy.max_alignment_shift
                    or comparison.compared_samples
                    < self.match_policy.min_compared_samples
                ):
                    return False
                comparisons.append(comparison)
        except FingerprintError:
            return False

        comparison_tuple = tuple(comparisons)
        if (
            expected_comparisons is not None
            and comparison_tuple != expected_comparisons
        ):
            return False
        return self._validate_manifest(
            persisted,
            scan=current_scan,
            identity=expected_identity,
            comparisons=comparison_tuple,
        )

    def _load_manifest(
        self,
        *,
        baseline_scan: Mapping[str, Any],
        revision: int,
        plan: DeepCorrelationPlan,
        children: Mapping[int, tuple[int, ContentFingerprint]],
    ) -> tuple[int, tuple[FingerprintComparison, ...]] | None:
        identity = self._manifest_identity(
            scan=baseline_scan,
            revision=revision,
            plan=plan,
            children=children,
        )
        cache_key = self._cache_key(identity)
        with self.database.connect() as conn:
            conn.execute("BEGIN")
            current_scan, current_revision = self._scan_context(
                conn,
                int(baseline_scan["id"]),
            )
            if (
                current_revision != int(revision)
                or str(current_scan["metadata_signature"] or "")
                != str(baseline_scan["metadata_signature"] or "")
                or str(current_scan["file_sha256"] or "")
                != str(baseline_scan["file_sha256"] or "")
            ):
                raise DeepFingerprintCorrelationError(
                    "Episode Identity publication changed before cached "
                    "fingerprint correlation reuse."
                )
            current_plan = self._plan(
                conn,
                int(current_scan["file_id"]),
            )
            if current_plan.plan_signature != plan.plan_signature:
                raise DeepFingerprintCorrelationError(
                    "Deep correlation cohort changed before cached matrix reuse."
                )
            for artifact_id, fingerprint in children.values():
                if not self._child_is_current(
                    conn,
                    artifact_id=artifact_id,
                    expected=fingerprint,
                ):
                    return None

            row = conn.execute(
                """SELECT * FROM media_identity_artifacts
                   WHERE file_id=? AND artifact_type='deep_fingerprint_manifest'
                     AND analyzer_key=? AND analyzer_version=? AND cache_key=?
                   ORDER BY id DESC LIMIT 1""",
                (
                    int(current_scan["file_id"]),
                    self.manifest_key,
                    self.manifest_version,
                    cache_key,
                ),
            ).fetchone()
            if row is None:
                return None
            persisted = dict(row)
            try:
                payload = json.loads(
                    str(persisted.get("payload_json") or "{}")
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
            raw_comparisons = (
                payload.get("comparisons")
                if isinstance(payload, Mapping)
                else None
            )
            if (
                not isinstance(raw_comparisons, list)
                or len(raw_comparisons) != len(plan.comparison_pairs)
            ):
                return None

            comparisons: list[FingerprintComparison] = []
            try:
                for pair, raw in zip(
                    plan.comparison_pairs,
                    raw_comparisons,
                ):
                    comparison = fingerprint_comparison_from_payload(raw)
                    if (
                        (comparison.left_file_id, comparison.right_file_id)
                        != tuple(pair)
                        or comparison.algorithm_key != self.algorithm.key
                        or comparison.algorithm_version
                        != self.algorithm.version
                        or abs(comparison.alignment_shift)
                        > self.match_policy.max_alignment_shift
                        or comparison.compared_samples
                        < self.match_policy.min_compared_samples
                    ):
                        return None
                    comparisons.append(comparison)
            except FingerprintError:
                return None

            result = tuple(comparisons)
            if not self._validate_manifest(
                persisted,
                scan=current_scan,
                identity=identity,
                comparisons=result,
            ):
                return None
            return int(persisted["id"]), result

    def _persist_manifest(
        self,
        *,
        baseline_scan: Mapping[str, Any],
        revision: int,
        plan: DeepCorrelationPlan,
        children: Mapping[int, tuple[int, ContentFingerprint]],
        comparisons: tuple[FingerprintComparison, ...],
    ) -> tuple[int, bool]:
        identity = self._manifest_identity(
            scan=baseline_scan,
            revision=revision,
            plan=plan,
            children=children,
        )
        payload = _manifest_payload(
            identity,
            comparisons,
        )
        cache_key = self._cache_key(identity)

        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current_scan, current_revision = self._scan_context(
                conn,
                int(baseline_scan["id"]),
            )
            if (
                current_revision != int(revision)
                or str(current_scan["metadata_signature"] or "")
                != str(baseline_scan["metadata_signature"] or "")
                or str(current_scan["file_sha256"] or "")
                != str(baseline_scan["file_sha256"] or "")
            ):
                raise DeepFingerprintCorrelationError(
                    "Episode Identity publication changed before fingerprint "
                    "correlation publication."
                )
            current_plan = self._plan(
                conn,
                int(current_scan["file_id"]),
            )
            if current_plan.plan_signature != plan.plan_signature:
                raise DeepFingerprintCorrelationError(
                    "Deep correlation cohort changed before fingerprint publication."
                )
            for artifact_id, fingerprint in children.values():
                if not self._child_is_current(
                    conn,
                    artifact_id=artifact_id,
                    expected=fingerprint,
                ):
                    raise DeepFingerprintCorrelationError(
                        "A fingerprint child changed before correlation publication."
                    )

            target_snapshot = self.artifact_service._file_snapshot(
                conn,
                int(current_scan["file_id"]),
            )
            cursor = conn.execute(
                """INSERT OR IGNORE INTO media_identity_artifacts(
                     file_id,artifact_type,analyzer_key,analyzer_version,
                     cache_key,status,profile,source_kind,source_ref,
                     source_signature,file_size_bytes,file_modified_at,
                     payload_json
                   ) VALUES (
                     ?,'deep_fingerprint_manifest',?,?,?,'complete','deep',
                     ?,?,?,?,?,?
                   )""",
                (
                    int(current_scan["file_id"]),
                    self.manifest_key,
                    self.manifest_version,
                    cache_key,
                    self.manifest_source_kind,
                    f"scan:{int(current_scan['id'])}",
                    plan.plan_signature,
                    int(target_snapshot["size_bytes"] or 0),
                    target_snapshot["modified_at"],
                    _canonical_json(payload),
                ),
            )
            inserted = cursor.rowcount == 1
            row = conn.execute(
                """SELECT * FROM media_identity_artifacts
                   WHERE file_id=? AND artifact_type='deep_fingerprint_manifest'
                     AND analyzer_key=? AND analyzer_version=? AND cache_key=?
                   ORDER BY id DESC LIMIT 1""",
                (
                    int(current_scan["file_id"]),
                    self.manifest_key,
                    self.manifest_version,
                    cache_key,
                ),
            ).fetchone()
            if row is None:
                raise DeepFingerprintCorrelationError(
                    "InfoMancer could not persist the fingerprint correlation manifest."
                )
            persisted = dict(row)
            if not self._validate_manifest(
                persisted,
                scan=current_scan,
                identity=identity,
                comparisons=comparisons,
            ):
                repair = conn.execute(
                    """UPDATE media_identity_artifacts
                       SET status='complete',profile='deep',
                           source_kind=?,source_ref=?,
                           source_signature=?,file_size_bytes=?,
                           file_modified_at=?,payload_json=?,error='',
                           updated_at=CURRENT_TIMESTAMP,
                           last_used_at=CURRENT_TIMESTAMP
                       WHERE id=? AND cache_key=?""",
                    (
                        self.manifest_source_kind,
                        f"scan:{int(current_scan['id'])}",
                        plan.plan_signature,
                        int(target_snapshot["size_bytes"] or 0),
                        target_snapshot["modified_at"],
                        _canonical_json(payload),
                        int(persisted["id"]),
                        cache_key,
                    ),
                )
                if repair.rowcount != 1:
                    raise DeepFingerprintCorrelationError(
                        "InfoMancer could not repair the fingerprint manifest safely."
                    )
                inserted = True
            else:
                conn.execute(
                    """UPDATE media_identity_artifacts
                       SET last_used_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (int(persisted["id"]),),
                )
            return int(persisted["id"]), inserted

    def run(self, scan_id: int) -> DeepFingerprintCorrelationRun:
        with self.database.connect() as conn:
            scan, revision = self._scan_context(conn, int(scan_id))
            initial_plan = self._plan(conn, int(scan["file_id"]))

        children: dict[int, tuple[int, ContentFingerprint]] = {}
        failures: list[str] = []
        missing: list[int] = []
        reused = 0
        generated = 0

        for item in initial_plan.files:
            is_target = item.file_id == int(scan["file_id"])
            try:
                result = self.artifact_service.ensure_file(
                    item.file_id,
                    scan_id=(int(scan_id) if is_target else None),
                    expected_revision=(revision if is_target else None),
                )
            except DeepFingerprintError as exc:
                if is_target:
                    raise
                missing.append(item.file_id)
                failures.append(
                    "fingerprint-unavailable:"
                    f"file:{item.file_id}:{type(exc).__name__}:{exc}"
                )
                continue
            if (
                result.artifact_id is None
                or result.fingerprint is None
                or result.failure
            ):
                missing.append(item.file_id)
                failures.append(
                    result.failure
                    or f"fingerprint-unavailable:file:{item.file_id}"
                )
                continue
            children[item.file_id] = (
                int(result.artifact_id),
                result.fingerprint,
            )
            if result.reused:
                reused += 1
            else:
                generated += 1

        with self.database.connect() as conn:
            current_scan, current_revision = self._scan_context(
                conn,
                int(scan_id),
            )
            current_plan = self._plan(
                conn,
                int(current_scan["file_id"]),
            )
        if (
            current_revision != revision
            or tuple(item.file_id for item in current_plan.files)
            != tuple(item.file_id for item in initial_plan.files)
        ):
            raise DeepFingerprintCorrelationError(
                "Deep correlation cohort changed while fingerprints were prepared."
            )
        plan = current_plan

        if not missing:
            cached_manifest = self._load_manifest(
                baseline_scan=current_scan,
                revision=current_revision,
                plan=plan,
                children=children,
            )
            if cached_manifest is not None:
                manifest_id, cached_comparisons = cached_manifest
                return DeepFingerprintCorrelationRun(
                    scan_id=int(scan_id),
                    algorithm_key=self.algorithm.key,
                    correlation_plan_signature=plan.plan_signature,
                    planned_file_count=len(plan.files),
                    completed_file_count=len(children),
                    planned_pair_count=len(plan.comparison_pairs),
                    completed_pair_count=len(cached_comparisons),
                    reused_fingerprint_count=reused,
                    generated_fingerprint_count=generated,
                    manifest_artifact_id=manifest_id,
                    coverage_complete=True,
                    missing_file_ids=(),
                    comparisons=cached_comparisons,
                    failures=(),
                )

        comparisons: list[FingerprintComparison] = []
        if not missing:
            for left_id, right_id in plan.comparison_pairs:
                left = children[left_id][1]
                right = children[right_id][1]
                comparison = compare_content_fingerprints(
                    left,
                    right,
                    policy=self.match_policy,
                )
                if comparison is None:
                    failures.append(
                        f"fingerprint-incomparable:{left_id}:{right_id}"
                    )
                    break
                comparisons.append(comparison)

        coverage_complete = (
            not missing
            and not failures
            and len(children) == len(plan.files)
            and len(comparisons) == len(plan.comparison_pairs)
        )
        manifest_id = None
        if coverage_complete:
            manifest_id, _ = self._persist_manifest(
                baseline_scan=current_scan,
                revision=current_revision,
                plan=plan,
                children=children,
                comparisons=tuple(comparisons),
            )

        return DeepFingerprintCorrelationRun(
            scan_id=int(scan_id),
            algorithm_key=self.algorithm.key,
            correlation_plan_signature=plan.plan_signature,
            planned_file_count=len(plan.files),
            completed_file_count=len(children),
            planned_pair_count=len(plan.comparison_pairs),
            completed_pair_count=len(comparisons),
            reused_fingerprint_count=reused,
            generated_fingerprint_count=generated,
            manifest_artifact_id=manifest_id,
            coverage_complete=coverage_complete,
            missing_file_ids=tuple(missing),
            comparisons=tuple(comparisons),
            failures=tuple(failures),
        )

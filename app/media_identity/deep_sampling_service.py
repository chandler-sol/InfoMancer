from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Any, Callable, Mapping

from ..db import Database
from .deep import (
    DeepCandidatePolicy,
    DeepCorrelationPolicy,
    build_deep_plan_metadata,
    generate_deep_episode_candidates,
    plan_deep_correlation,
)
from .deep_sampling import (
    DeepSamplingPolicy,
    DeepVisualPlan,
    DeepVisualSample,
    build_deep_visual_plan,
)
from .decision_snapshot import result_revision
from .external import ExternalAnalysisError, ExternalPreviewUnavailable, ExternalSourceFailure
from .local_frames import LOCAL_FRAME_SOURCE_KEY, LocalFfmpegFrameSource
from .models import AnalyzerContext, IdentityProfile, IdentityReference, MediaIdentityFile
from .normal import OcrEngine, OcrTextResult, ocr_preview_cache_key
from .normal_service import NORMAL_OCR_ARTIFACT_KEY, NORMAL_OCR_ARTIFACT_VERSION
from .service import MediaIdentityDecisionService
from .visual_budget import VisualAttemptBudget, VisualBudgetExceeded, visual_budget_scope
from .versions import (
    DEEP_ORCHESTRATION_VERSION,
    DEEP_SAMPLING_ALGORITHM_VERSION,
)


DEEP_SAMPLING_MANIFEST_KEY = "deep-visual-sampling"
DEEP_SAMPLING_MANIFEST_VERSION = str(DEEP_SAMPLING_ALGORITHM_VERSION)


class DeepSamplingScanError(RuntimeError):
    """Raised when Deep sampling cannot safely continue or publish a manifest."""


@dataclass(frozen=True)
class DeepVisualObservation:
    sample: DeepVisualSample
    artifact_id: int
    cache_key: str
    text: str
    confidence: float | None
    preview_sha256: str
    artifact_digest: str
    reused: bool


@dataclass(frozen=True)
class DeepSamplingRun:
    scan_id: int
    plan_signature: str
    candidate_plan_signature: str
    correlation_plan_signature: str
    planned_frame_count: int
    completed_frame_count: int
    reused_artifact_count: int
    manifest_artifact_id: int | None
    coverage_complete: bool
    failures: tuple[str, ...]
    budget_exhausted: bool
    frame_attempt_count: int
    source_bytes: int
    image_bytes: int
    text_chars: int


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DeepSamplingScanError(
            "Deep sampling metadata could not be serialized safely."
        ) from exc


def _json_object(value: Any) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _same_modified_at(first: Any, second: Any) -> bool:
    if first is None or second is None:
        return first is None and second is None
    return float(first) == float(second)


def _bounded_failure(prefix: str, exc: BaseException) -> str:
    detail = " ".join(str(exc).split())
    if len(detail) > 300:
        detail = detail[:297] + "..."
    name = type(exc).__name__
    return f"{prefix}:{name}" + (f":{detail}" if detail else "")


def _valid_sha256(value: object) -> bool:
    text = str(value or "").strip().casefold()
    return (
        len(text) == 64
        and all(character in "0123456789abcdef" for character in text)
    )


class DeepSamplingService:
    """Produce resumable, candidate-neutral Deep OCR observations.

    J2 deliberately persists derived observations and a completion manifest only.
    It does not mutate the scan, add candidate evidence, change scoring, or mark a
    scan Deep. Later J stages can consume this exact manifest without turning a
    partial Deep run into an actionable conclusion.
    """

    def __init__(
        self,
        database: Database,
        engine: OcrEngine,
        *,
        policy: DeepSamplingPolicy | None = None,
        candidate_policy: DeepCandidatePolicy | None = None,
        correlation_policy: DeepCorrelationPolicy | None = None,
        frame_source_factory: Callable[..., LocalFfmpegFrameSource] = (
            LocalFfmpegFrameSource
        ),
    ) -> None:
        self.database = database
        self.engine = engine
        self.policy = policy or DeepSamplingPolicy()
        self.candidate_policy = candidate_policy or DeepCandidatePolicy()
        self.correlation_policy = correlation_policy or DeepCorrelationPolicy()
        self.frame_source_factory = frame_source_factory

    @staticmethod
    def _scan_rows(
        conn: sqlite3.Connection,
        scan_id: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        scan_row = conn.execute(
            "SELECT * FROM media_identity_scans WHERE id=?",
            (int(scan_id),),
        ).fetchone()
        if not scan_row:
            raise DeepSamplingScanError("Episode Identity scan was not found.")
        scan = dict(scan_row)
        evidence = [
            dict(row)
            for row in conn.execute(
                """SELECT * FROM media_identity_evidence
                   WHERE scan_id=? ORDER BY id""",
                (int(scan_id),),
            ).fetchall()
        ]
        file_row = conn.execute(
            """SELECT f.*,t.kind title_kind
               FROM files f
               JOIN titles t ON t.id=f.title_id
               WHERE f.id=?""",
            (int(scan["file_id"]),),
        ).fetchone()
        if file_row is None:
            raise DeepSamplingScanError(
                "Episode Identity media disappeared before Deep sampling."
            )
        return scan, evidence, dict(file_row)

    @staticmethod
    def _context(
        scan: Mapping[str, Any],
        file_row: Mapping[str, Any],
    ) -> AnalyzerContext:
        claimed = _json_object(scan.get("claimed_identity_json"))
        try:
            season = int(claimed["season"])
            episode = int(claimed["episode_start"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DeepSamplingScanError(
                "The identity scan does not contain a usable claimed episode."
            ) from exc
        return AnalyzerContext(
            media=MediaIdentityFile(
                file_id=int(scan["file_id"]),
                title_id=int(file_row["title_id"]),
                path=str(file_row["path"]),
                size_bytes=int(scan["file_size_bytes"] or 0),
                modified_at=scan["file_modified_at"],
                sha256=str(scan["file_sha256"] or "") or None,
            ),
            claimed_identity=IdentityReference(
                identity_kind="episode",
                season=season,
                episode=episode,
            ),
            profile=IdentityProfile.DEEP,
            metadata_signature=str(scan["metadata_signature"] or ""),
        )

    def _require_current_baseline(
        self,
        conn: sqlite3.Connection,
        baseline: Mapping[str, Any],
        *,
        expected_revision: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        current, evidence, file_row = self._scan_rows(
            conn,
            int(baseline["id"]),
        )
        if current["status"] != "complete":
            raise DeepSamplingScanError(
                "Episode Identity scan state changed during Deep sampling."
            )
        snapshot_current, _ = MediaIdentityDecisionService._scan_snapshot_is_current(
            conn,
            current,
            evidence,
        )
        if not snapshot_current:
            raise DeepSamplingScanError(
                "Episode Identity inputs changed during Deep sampling."
            )
        if (
            int(current["file_id"]) != int(baseline["file_id"])
            or str(current["metadata_signature"] or "")
            != str(baseline.get("metadata_signature") or "")
            or int(current["file_size_bytes"] or 0)
            != int(baseline.get("file_size_bytes") or 0)
            or not _same_modified_at(
                current["file_modified_at"],
                baseline.get("file_modified_at"),
            )
            or str(current["file_sha256"] or "")
            != str(baseline.get("file_sha256") or "")
            or result_revision(current) != int(expected_revision)
        ):
            raise DeepSamplingScanError(
                "Episode Identity publication changed during Deep sampling."
            )
        return current, evidence, file_row

    def _engine_identity(self) -> dict[str, Any]:
        raw_key = getattr(self.engine, "key", "")
        raw_version = getattr(self.engine, "version", "")
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise DeepSamplingScanError("OCR engine key is unavailable.")
        if not isinstance(raw_version, str) or not raw_version.strip():
            raise DeepSamplingScanError("OCR engine version is unavailable.")
        try:
            identity = dict(self.engine.cache_identity())
        except (AttributeError, TypeError, ValueError) as exc:
            raise DeepSamplingScanError(
                "OCR engine cache identity is unavailable."
            ) from exc
        if not identity:
            raise DeepSamplingScanError(
                "OCR engine cache identity is unavailable."
            )
        _canonical_json(identity)
        return {
            "key": raw_key.strip().casefold(),
            "version": raw_version.strip(),
            "identity": identity,
        }

    @staticmethod
    def _cache_parameters(scan: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "file_id": int(scan["file_id"]),
            "size_bytes": int(scan["file_size_bytes"] or 0),
            "modified_at": scan["file_modified_at"],
            "sha256": str(scan["file_sha256"] or ""),
        }

    def _artifact_from_row(
        self,
        row: Mapping[str, Any],
        *,
        scan: Mapping[str, Any],
        sample: DeepVisualSample,
        cache_parameters: Mapping[str, Any],
        reused: bool,
    ) -> DeepVisualObservation | None:
        payload = _json_object(row.get("payload_json"))
        details = payload.get("details")
        if not isinstance(details, Mapping):
            return None
        preview_sha256 = str(
            details.get("preview_sha256") or ""
        ).strip().casefold()
        if not _valid_sha256(preview_sha256):
            return None
        try:
            expected_cache_key = ocr_preview_cache_key(
                sample.frame,
                self.engine,
                parameters=cache_parameters,
                preview_sha256=preview_sha256,
            )
        except (TypeError, ValueError):
            return None
        if (
            int(row.get("file_id") or 0) != int(scan["file_id"])
            or str(row.get("artifact_type") or "") != "visual_text"
            or str(row.get("analyzer_key") or "") != NORMAL_OCR_ARTIFACT_KEY
            or str(row.get("analyzer_version") or "")
            != NORMAL_OCR_ARTIFACT_VERSION
            or str(row.get("status") or "") != "complete"
            or str(row.get("profile") or "") not in {
                IdentityProfile.NORMAL.value,
                IdentityProfile.DEEP.value,
            }
            or str(row.get("source_kind") or "") != str(sample.frame.source_key)
            or str(row.get("source_ref") or "") != str(sample.frame.asset_ref)
            or str(row.get("source_signature") or "")
            != str(sample.frame.source_signature)
            or int(row.get("file_size_bytes") or 0)
            != int(scan["file_size_bytes"] or 0)
            or not _same_modified_at(
                row.get("file_modified_at"),
                scan.get("file_modified_at"),
            )
            or (
                row.get("start_ms") is None
                or int(row["start_ms"]) != int(sample.frame.timestamp_ms)
            )
            or (
                row.get("end_ms") is None
                or int(row["end_ms"]) != int(sample.frame.timestamp_ms)
            )
            or str(row.get("cache_key") or "") != expected_cache_key
            or str(payload.get("item_id") or "") != str(sample.frame.item_id)
            or str(payload.get("engine_key") or "").strip().casefold()
            != str(getattr(self.engine, "key", "") or "").strip().casefold()
            or str(payload.get("engine_version") or "")
            != str(getattr(self.engine, "version", "") or "")
        ):
            return None

        artifact_profile = str(row.get("profile") or "")
        if artifact_profile == IdentityProfile.DEEP.value:
            output_sha256 = str(
                payload.get("artifact_output_sha256") or ""
            ).strip().casefold()
            digest_payload = dict(payload)
            digest_payload.pop("artifact_output_sha256", None)
            expected_output_sha256 = hashlib.sha256(
                _canonical_json({
                    "text_value": str(row.get("text_value") or ""),
                    "payload": digest_payload,
                }).encode("utf-8")
            ).hexdigest()
            if (
                not _valid_sha256(output_sha256)
                or output_sha256 != expected_output_sha256
            ):
                return None

        confidence = payload.get("confidence")
        try:
            normalized_confidence = (
                None if confidence is None else float(confidence)
            )
        except (TypeError, ValueError):
            return None
        if (
            normalized_confidence is not None
            and not 0.0 <= normalized_confidence <= 1.0
        ):
            return None
        artifact_digest = hashlib.sha256(
            _canonical_json({
                "text_value": str(row.get("text_value") or ""),
                "payload": payload,
            }).encode("utf-8")
        ).hexdigest()
        return DeepVisualObservation(
            sample=sample,
            artifact_id=int(row["id"]),
            cache_key=expected_cache_key,
            text=str(row.get("text_value") or ""),
            confidence=normalized_confidence,
            preview_sha256=preview_sha256,
            artifact_digest=artifact_digest,
            reused=reused,
        )

    def _cached_observation(
        self,
        scan: Mapping[str, Any],
        sample: DeepVisualSample,
        cache_parameters: Mapping[str, Any],
    ) -> DeepVisualObservation | None:
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT id,file_id,artifact_type,analyzer_key,analyzer_version,
                          cache_key,status,profile,source_kind,source_ref,
                          source_signature,file_size_bytes,file_modified_at,
                          start_ms,end_ms,text_value,payload_json
                   FROM media_identity_artifacts
                   WHERE file_id=? AND artifact_type='visual_text'
                     AND analyzer_key=? AND analyzer_version=?
                     AND source_kind=? AND source_ref=? AND source_signature=?
                     AND start_ms=? AND end_ms=? AND status='complete'
                   ORDER BY id DESC LIMIT 16""",
                (
                    int(scan["file_id"]),
                    NORMAL_OCR_ARTIFACT_KEY,
                    NORMAL_OCR_ARTIFACT_VERSION,
                    str(sample.frame.source_key),
                    str(sample.frame.asset_ref),
                    str(sample.frame.source_signature),
                    int(sample.frame.timestamp_ms),
                    int(sample.frame.timestamp_ms),
                ),
            ).fetchall()
            for raw_row in rows:
                row = dict(raw_row)
                observation = self._artifact_from_row(
                    row,
                    scan=scan,
                    sample=sample,
                    cache_parameters=cache_parameters,
                    reused=True,
                )
                if observation is None:
                    continue
                conn.execute(
                    """UPDATE media_identity_artifacts
                       SET last_used_at=CURRENT_TIMESTAMP
                       WHERE id=?""",
                    (observation.artifact_id,),
                )
                return observation
        return None

    def _persist_observation(
        self,
        baseline: Mapping[str, Any],
        *,
        expected_revision: int,
        sample: DeepVisualSample,
        cache_parameters: Mapping[str, Any],
        cache_key: str,
        preview_sha256: str,
        image_bytes: int,
        result: OcrTextResult,
    ) -> DeepVisualObservation:
        payload = {
            "confidence": result.confidence,
            "stage": 3,
            "ordinal": int(sample.ordinal),
            "image_bytes": int(image_bytes),
            "reused": False,
            "item_id": str(sample.frame.item_id),
            "engine_key": str(self.engine.key),
            "engine_version": str(self.engine.version),
            "details": {
                **dict(result.details),
                "preview_sha256": str(preview_sha256),
            },
        }
        payload["artifact_output_sha256"] = hashlib.sha256(
            _canonical_json({
                "text_value": str(result.text or ""),
                "payload": payload,
            }).encode("utf-8")
        ).hexdigest()
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_current_baseline(
                conn,
                baseline,
                expected_revision=expected_revision,
            )
            conn.execute(
                """INSERT OR IGNORE INTO media_identity_artifacts(
                     file_id,artifact_type,analyzer_key,analyzer_version,
                     cache_key,status,profile,source_kind,source_ref,
                     source_signature,file_size_bytes,file_modified_at,
                     start_ms,end_ms,text_value,payload_json
                   ) VALUES (
                     ?,'visual_text',?,?,?,'complete','deep',?,?,?,?,?,?,?,?,?
                   )""",
                (
                    int(baseline["file_id"]),
                    NORMAL_OCR_ARTIFACT_KEY,
                    NORMAL_OCR_ARTIFACT_VERSION,
                    str(cache_key),
                    str(sample.frame.source_key),
                    str(sample.frame.asset_ref),
                    str(sample.frame.source_signature),
                    int(baseline["file_size_bytes"] or 0),
                    baseline["file_modified_at"],
                    int(sample.frame.timestamp_ms),
                    int(sample.frame.timestamp_ms),
                    str(result.text or ""),
                    _canonical_json(payload),
                ),
            )
            row = conn.execute(
                """SELECT id,file_id,artifact_type,analyzer_key,analyzer_version,
                          cache_key,status,profile,source_kind,source_ref,
                          source_signature,file_size_bytes,file_modified_at,
                          start_ms,end_ms,text_value,payload_json
                   FROM media_identity_artifacts
                   WHERE file_id=? AND artifact_type='visual_text'
                     AND analyzer_key=? AND analyzer_version=? AND cache_key=?
                   ORDER BY id DESC LIMIT 1""",
                (
                    int(baseline["file_id"]),
                    NORMAL_OCR_ARTIFACT_KEY,
                    NORMAL_OCR_ARTIFACT_VERSION,
                    str(cache_key),
                ),
            ).fetchone()
            if row is None:
                raise DeepSamplingScanError(
                    "InfoMancer could not persist a Deep OCR observation."
                )
            observation = self._artifact_from_row(
                dict(row),
                scan=baseline,
                sample=sample,
                cache_parameters=cache_parameters,
                reused=False,
            )
            if observation is None:
                raise DeepSamplingScanError(
                    "The persisted Deep OCR observation failed provenance validation."
                )
            return observation

    def _manifest_identity(
        self,
        scan: Mapping[str, Any],
        *,
        visual_plan: DeepVisualPlan,
        deep_identity: Mapping[str, Any],
        engine_identity: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "deep_orchestration_version": DEEP_ORCHESTRATION_VERSION,
            "sampling_algorithm_version": DEEP_SAMPLING_ALGORITHM_VERSION,
            "file_id": int(scan["file_id"]),
            "file_size_bytes": int(scan["file_size_bytes"] or 0),
            "file_modified_at": scan["file_modified_at"],
            "file_sha256": str(scan["file_sha256"] or ""),
            "metadata_signature": str(scan["metadata_signature"] or ""),
            "sampling_policy": visual_plan.policy.identity_payload(),
            "visual_plan_signature": visual_plan.plan_signature,
            "candidate_plan_signature": str(
                deep_identity.get("candidate_plan_signature") or ""
            ),
            "correlation_plan_signature": str(
                deep_identity.get("correlation_plan_signature") or ""
            ),
            "engine": dict(engine_identity),
        }

    @staticmethod
    def _manifest_cache_key(identity: Mapping[str, Any]) -> str:
        return hashlib.sha256(
            _canonical_json(dict(identity)).encode("utf-8")
        ).hexdigest()

    def _manifest_observations(
        self,
        conn: sqlite3.Connection,
        *,
        baseline: Mapping[str, Any],
        visual_plan: DeepVisualPlan,
        cache_parameters: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> tuple[DeepVisualObservation, ...] | None:
        raw = payload.get("observations")
        if not isinstance(raw, list) or len(raw) != len(visual_plan.samples):
            return None
        observations: list[DeepVisualObservation] = []
        for sample, item in zip(visual_plan.samples, raw):
            if not isinstance(item, Mapping):
                return None
            try:
                artifact_id = int(item["artifact_id"])
            except (KeyError, TypeError, ValueError):
                return None
            if (
                artifact_id <= 0
                or str(item.get("work_key") or "") != sample.work_key
                or int(item.get("ordinal") or 0) != sample.ordinal
            ):
                return None
            row = conn.execute(
                """SELECT id,file_id,artifact_type,analyzer_key,analyzer_version,
                          cache_key,status,profile,source_kind,source_ref,
                          source_signature,file_size_bytes,file_modified_at,
                          start_ms,end_ms,text_value,payload_json
                   FROM media_identity_artifacts WHERE id=?""",
                (artifact_id,),
            ).fetchone()
            if row is None:
                return None
            observation = self._artifact_from_row(
                dict(row),
                scan=baseline,
                sample=sample,
                cache_parameters=cache_parameters,
                reused=True,
            )
            if (
                observation is None
                or observation.cache_key != str(item.get("cache_key") or "")
                or observation.preview_sha256
                != str(item.get("preview_sha256") or "")
                or observation.artifact_digest
                != str(item.get("artifact_digest") or "")
            ):
                return None
            observations.append(observation)
        return tuple(observations)

    def _load_complete_manifest(
        self,
        baseline: Mapping[str, Any],
        *,
        visual_plan: DeepVisualPlan,
        identity: Mapping[str, Any],
        cache_parameters: Mapping[str, Any],
    ) -> tuple[int, tuple[DeepVisualObservation, ...]] | None:
        cache_key = self._manifest_cache_key(identity)
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT id,file_id,artifact_type,analyzer_key,analyzer_version,
                          cache_key,status,profile,source_kind,source_ref,
                          source_signature,file_size_bytes,file_modified_at,
                          payload_json
                   FROM media_identity_artifacts
                   WHERE file_id=? AND artifact_type='deep_sampling_manifest'
                     AND analyzer_key=? AND analyzer_version=? AND cache_key=?
                   ORDER BY id DESC LIMIT 1""",
                (
                    int(baseline["file_id"]),
                    DEEP_SAMPLING_MANIFEST_KEY,
                    DEEP_SAMPLING_MANIFEST_VERSION,
                    cache_key,
                ),
            ).fetchone()
            if row is None:
                return None
            payload = _json_object(row["payload_json"])
            if (
                int(row["file_id"]) != int(baseline["file_id"])
                or str(row["status"] or "") != "complete"
                or str(row["profile"] or "") != IdentityProfile.DEEP.value
                or str(row["source_kind"] or "") != LOCAL_FRAME_SOURCE_KEY
                or str(row["source_signature"] or "") != visual_plan.plan_signature
                or int(row["file_size_bytes"] or 0)
                != int(baseline["file_size_bytes"] or 0)
                or not _same_modified_at(
                    row["file_modified_at"],
                    baseline["file_modified_at"],
                )
                or payload.get("identity") != dict(identity)
                or payload.get("coverage_complete") is not True
            ):
                raise DeepSamplingScanError(
                    "The persisted Deep sampling manifest failed provenance validation."
                )
            observations = self._manifest_observations(
                conn,
                baseline=baseline,
                visual_plan=visual_plan,
                cache_parameters=cache_parameters,
                payload=payload,
            )
            if observations is None:
                raise DeepSamplingScanError(
                    "The persisted Deep sampling manifest failed artifact integrity validation."
                )
            conn.execute(
                """UPDATE media_identity_artifacts
                   SET last_used_at=CURRENT_TIMESTAMP WHERE id=?""",
                (int(row["id"]),),
            )
            return int(row["id"]), observations

    def _persist_manifest(
        self,
        baseline: Mapping[str, Any],
        *,
        expected_revision: int,
        visual_plan: DeepVisualPlan,
        identity: Mapping[str, Any],
        observations: tuple[DeepVisualObservation, ...],
        deep_identity: Mapping[str, Any],
    ) -> int:
        cache_key = self._manifest_cache_key(identity)
        payload = {
            "identity": dict(identity),
            "deep_identity": dict(deep_identity),
            "coverage_complete": True,
            "observations": [
                {
                    "ordinal": item.sample.ordinal,
                    "work_key": item.sample.work_key,
                    "artifact_id": item.artifact_id,
                    "cache_key": item.cache_key,
                    "preview_sha256": item.preview_sha256,
                    "artifact_digest": item.artifact_digest,
                    "timestamp_ms": int(item.sample.frame.timestamp_ms),
                    "inherited_normal": bool(item.sample.inherited_normal),
                }
                for item in observations
            ],
        }
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current, _, file_row = self._require_current_baseline(
                conn,
                baseline,
                expected_revision=expected_revision,
            )
            claimed = _json_object(current["claimed_identity_json"])
            language = str(
                claimed.get("scan_language") or "eng"
            ).strip().casefold() or "eng"
            current_candidates = generate_deep_episode_candidates(
                conn,
                title_id=int(file_row["title_id"]),
                season=int(file_row["season"]),
                episode_start=int(file_row["episode_start"]),
                episode_end=int(
                    file_row["episode_end"] or file_row["episode_start"]
                ),
                language=language,
                policy=self.candidate_policy,
            )
            current_correlation = plan_deep_correlation(
                conn,
                file_id=int(current["file_id"]),
                policy=self.correlation_policy,
            )
            current_deep_identity = build_deep_plan_metadata(
                current_candidates,
                current_correlation,
            )
            if dict(current_deep_identity) != dict(deep_identity):
                raise DeepSamplingScanError(
                    "Deep candidate or correlation inputs changed during sampling."
                )

            conn.execute(
                """INSERT OR IGNORE INTO media_identity_artifacts(
                     file_id,artifact_type,analyzer_key,analyzer_version,
                     cache_key,status,profile,source_kind,source_ref,
                     source_signature,file_size_bytes,file_modified_at,payload_json
                   ) VALUES (
                     ?,'deep_sampling_manifest',?,?,?,'complete','deep',
                     ?,?,?,?, ?,?
                   )""",
                (
                    int(baseline["file_id"]),
                    DEEP_SAMPLING_MANIFEST_KEY,
                    DEEP_SAMPLING_MANIFEST_VERSION,
                    cache_key,
                    LOCAL_FRAME_SOURCE_KEY,
                    f"scan:{int(baseline['id'])}",
                    visual_plan.plan_signature,
                    int(baseline["file_size_bytes"] or 0),
                    baseline["file_modified_at"],
                    _canonical_json(payload),
                ),
            )
            row = conn.execute(
                """SELECT id,status,profile,source_kind,source_ref,source_signature,
                          file_size_bytes,file_modified_at,payload_json
                   FROM media_identity_artifacts
                   WHERE file_id=? AND artifact_type='deep_sampling_manifest'
                     AND analyzer_key=? AND analyzer_version=? AND cache_key=?
                   ORDER BY id DESC LIMIT 1""",
                (
                    int(baseline["file_id"]),
                    DEEP_SAMPLING_MANIFEST_KEY,
                    DEEP_SAMPLING_MANIFEST_VERSION,
                    cache_key,
                ),
            ).fetchone()
            if row is None:
                raise DeepSamplingScanError(
                    "InfoMancer could not persist the Deep sampling manifest."
                )
            manifest_id = int(row["id"])
            persisted_payload = _json_object(row["payload_json"])
            if (
                str(row["status"] or "") != "complete"
                or str(row["profile"] or "") != IdentityProfile.DEEP.value
                or str(row["source_kind"] or "") != LOCAL_FRAME_SOURCE_KEY
                or str(row["source_ref"] or "")
                != f"scan:{int(baseline['id'])}"
                or str(row["source_signature"] or "")
                != visual_plan.plan_signature
                or int(row["file_size_bytes"] or 0)
                != int(baseline["file_size_bytes"] or 0)
                or not _same_modified_at(
                    row["file_modified_at"],
                    baseline["file_modified_at"],
                )
                or persisted_payload != payload
            ):
                raise DeepSamplingScanError(
                    "A conflicting Deep sampling manifest already exists."
                )
            conn.execute(
                """UPDATE media_identity_artifacts
                   SET last_used_at=CURRENT_TIMESTAMP WHERE id=?""",
                (manifest_id,),
            )
            return manifest_id

    def run(self, scan_id: int) -> DeepSamplingRun:
        with self.database.connect() as conn:
            scan, evidence, file_row = self._scan_rows(conn, int(scan_id))
            if scan["status"] != "complete":
                raise DeepSamplingScanError(
                    "Deep sampling requires a complete Episode Identity scan."
                )
            if str(file_row.get("title_kind") or "") != "tv":
                raise DeepSamplingScanError(
                    "Deep episode sampling requires a TV title."
                )
            snapshot_current, _ = MediaIdentityDecisionService._scan_snapshot_is_current(
                conn,
                scan,
                evidence,
            )
            if not snapshot_current:
                raise DeepSamplingScanError(
                    "The Episode Identity snapshot is stale. Verify the file again."
                )
            baseline_revision = result_revision(scan)
            if baseline_revision <= 0:
                raise DeepSamplingScanError(
                    "Deep sampling requires a sealed Episode Identity result."
                )
            if not _valid_sha256(scan.get("file_sha256")):
                raise DeepSamplingScanError(
                    "Deep local sampling requires an exact media SHA-256 snapshot."
                )
            claimed = _json_object(scan["claimed_identity_json"])
            language = str(
                claimed.get("scan_language") or "eng"
            ).strip().casefold() or "eng"
            candidate_plan = generate_deep_episode_candidates(
                conn,
                title_id=int(file_row["title_id"]),
                season=int(file_row["season"]),
                episode_start=int(file_row["episode_start"]),
                episode_end=int(
                    file_row["episode_end"] or file_row["episode_start"]
                ),
                language=language,
                policy=self.candidate_policy,
            )
            correlation_plan = plan_deep_correlation(
                conn,
                file_id=int(scan["file_id"]),
                policy=self.correlation_policy,
            )
            deep_identity = build_deep_plan_metadata(
                candidate_plan,
                correlation_plan,
            )
            runtime_seconds = file_row.get("runtime_seconds")
            context = self._context(scan, file_row)

        try:
            available = bool(self.engine.available())
        except Exception as exc:
            raise DeepSamplingScanError(
                _bounded_failure("deep-ocr-engine", exc)
            ) from exc
        if not available:
            return DeepSamplingRun(
                scan_id=int(scan_id),
                plan_signature="",
                candidate_plan_signature=candidate_plan.plan_signature,
                correlation_plan_signature=correlation_plan.plan_signature,
                planned_frame_count=0,
                completed_frame_count=0,
                reused_artifact_count=0,
                manifest_artifact_id=None,
                coverage_complete=False,
                failures=("ocr-engine-unavailable",),
                budget_exhausted=False,
                frame_attempt_count=0,
                source_bytes=0,
                image_bytes=0,
                text_chars=0,
            )
        engine_identity = self._engine_identity()

        source = self.frame_source_factory(
            context,
            runtime_seconds,
        )
        try:
            status = source.status()
            if not status.available:
                return DeepSamplingRun(
                    scan_id=int(scan_id),
                    plan_signature="",
                    candidate_plan_signature=candidate_plan.plan_signature,
                    correlation_plan_signature=correlation_plan.plan_signature,
                    planned_frame_count=0,
                    completed_frame_count=0,
                    reused_artifact_count=0,
                    manifest_artifact_id=None,
                    coverage_complete=False,
                    failures=(
                        f"{LOCAL_FRAME_SOURCE_KEY}:unavailable:{status.detail}",
                    ),
                    budget_exhausted=False,
                    frame_attempt_count=0,
                    source_bytes=0,
                    image_bytes=0,
                    text_chars=0,
                )
            media = source.resolve_media(context)
            if media is None:
                raise DeepSamplingScanError(
                    "Local Deep frame source could not resolve the verified media."
                )
            frames = tuple(source.preview_frames(media))
            visual_plan = build_deep_visual_plan(
                frames,
                policy=self.policy,
            )
            cache_parameters = self._cache_parameters(scan)
            identity = self._manifest_identity(
                scan,
                visual_plan=visual_plan,
                deep_identity=deep_identity,
                engine_identity=engine_identity,
            )
            if not visual_plan.samples:
                return DeepSamplingRun(
                    scan_id=int(scan_id),
                    plan_signature=visual_plan.plan_signature,
                    candidate_plan_signature=candidate_plan.plan_signature,
                    correlation_plan_signature=correlation_plan.plan_signature,
                    planned_frame_count=0,
                    completed_frame_count=0,
                    reused_artifact_count=0,
                    manifest_artifact_id=None,
                    coverage_complete=False,
                    failures=("deep-visual-no-frames",),
                    budget_exhausted=False,
                    frame_attempt_count=0,
                    source_bytes=0,
                    image_bytes=0,
                    text_chars=0,
                )

            existing = self._load_complete_manifest(
                scan,
                visual_plan=visual_plan,
                identity=identity,
                cache_parameters=cache_parameters,
            )
            if existing is not None:
                manifest_id, observations = existing
                return DeepSamplingRun(
                    scan_id=int(scan_id),
                    plan_signature=visual_plan.plan_signature,
                    candidate_plan_signature=candidate_plan.plan_signature,
                    correlation_plan_signature=correlation_plan.plan_signature,
                    planned_frame_count=len(visual_plan.samples),
                    completed_frame_count=len(observations),
                    reused_artifact_count=len(observations),
                    manifest_artifact_id=manifest_id,
                    coverage_complete=True,
                    failures=(),
                    budget_exhausted=False,
                    frame_attempt_count=0,
                    source_bytes=0,
                    image_bytes=0,
                    text_chars=sum(len(item.text) for item in observations),
                )

            budget = VisualAttemptBudget(
                max_frame_attempts=max(1, int(self.policy.visual_frame_count)),
                max_source_bytes=int(self.policy.max_source_bytes_total),
                max_image_bytes=int(self.policy.max_preview_bytes_total),
                max_text_chars=int(self.policy.max_ocr_text_chars),
            )
            observations: list[DeepVisualObservation] = []
            failures: list[str] = []

            for sample in visual_plan.samples:
                cached = self._cached_observation(
                    scan,
                    sample,
                    cache_parameters,
                )
                if cached is not None:
                    try:
                        budget.reserve_text_chars(len(cached.text))
                    except VisualBudgetExceeded as exc:
                        failures.append(
                            _bounded_failure(
                                f"deep-visual:{sample.ordinal}:cached-text-budget",
                                exc,
                            )
                        )
                        break
                    observations.append(cached)
                    continue

                try:
                    budget.reserve_frame_attempt()
                    source_before = budget.source_bytes
                    with visual_budget_scope(budget):
                        preview = bytes(source.read_preview(sample.frame))
                    if budget.source_bytes == source_before:
                        budget.reserve_source_bytes(len(preview))
                    if (
                        len(preview) <= 0
                        or len(preview)
                        > int(self.policy.max_preview_bytes_per_frame)
                    ):
                        raise DeepSamplingScanError(
                            "Deep preview frame exceeded its bounded byte contract."
                        )
                    budget.reserve_image_bytes(len(preview))
                    preview_sha256 = hashlib.sha256(preview).hexdigest()
                    cache_key = ocr_preview_cache_key(
                        sample.frame,
                        self.engine,
                        parameters=cache_parameters,
                        preview_sha256=preview_sha256,
                    )

                    cached_after_read = self._cached_observation(
                        scan,
                        sample,
                        cache_parameters,
                    )
                    if (
                        cached_after_read is not None
                        and cached_after_read.cache_key == cache_key
                    ):
                        observations.append(cached_after_read)
                        continue

                    result = self.engine.recognize(preview)
                    if not isinstance(result, OcrTextResult):
                        raise DeepSamplingScanError(
                            "OCR engines must return OcrTextResult values."
                        )
                    text = str(result.text or "")
                    budget.reserve_text_chars(len(text))
                    observation = self._persist_observation(
                        scan,
                        expected_revision=baseline_revision,
                        sample=sample,
                        cache_parameters=cache_parameters,
                        cache_key=cache_key,
                        preview_sha256=preview_sha256,
                        image_bytes=len(preview),
                        result=result,
                    )
                    observations.append(observation)
                except VisualBudgetExceeded as exc:
                    budget.mark_blocked()
                    failures.append(
                        _bounded_failure(
                            f"deep-visual:{sample.ordinal}:budget",
                            exc,
                        )
                    )
                    break
                except (ExternalPreviewUnavailable, ExternalSourceFailure, ExternalAnalysisError) as exc:
                    failures.append(
                        _bounded_failure(
                            f"deep-visual:{sample.ordinal}:source",
                            exc,
                        )
                    )
                    continue
                except DeepSamplingScanError:
                    raise
                except Exception as exc:
                    failures.append(
                        _bounded_failure(
                            f"deep-visual:{sample.ordinal}:ocr",
                            exc,
                        )
                    )
                    continue

            coverage_complete = (
                len(observations) == len(visual_plan.samples)
                and not failures
                and not budget.exhausted
            )
            manifest_id = None
            if coverage_complete:
                manifest_id = self._persist_manifest(
                    scan,
                    expected_revision=baseline_revision,
                    visual_plan=visual_plan,
                    identity=identity,
                    observations=tuple(observations),
                    deep_identity=deep_identity,
                )

            return DeepSamplingRun(
                scan_id=int(scan_id),
                plan_signature=visual_plan.plan_signature,
                candidate_plan_signature=candidate_plan.plan_signature,
                correlation_plan_signature=correlation_plan.plan_signature,
                planned_frame_count=len(visual_plan.samples),
                completed_frame_count=len(observations),
                reused_artifact_count=sum(
                    1 for item in observations if item.reused
                ),
                manifest_artifact_id=manifest_id,
                coverage_complete=coverage_complete,
                failures=tuple(failures),
                budget_exhausted=budget.exhausted,
                frame_attempt_count=budget.frame_attempts,
                source_bytes=budget.source_bytes,
                image_bytes=budget.image_bytes,
                text_chars=budget.text_chars,
            )
        finally:
            close = getattr(source, "close", None)
            if callable(close):
                close()

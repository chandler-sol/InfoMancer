from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Any, Callable, Mapping

from ..db import Database
from .external import ExternalSourceRegistry, PreviewFrameRef
from .external_config import external_source_config_signature
from .decision_snapshot import result_revision, seal_decision_snapshot
from .local_frames import LOCAL_FRAME_SOURCE_KEY, LocalFfmpegFrameSource
from .models import (
    AnalyzerContext,
    EvidenceCategory,
    EvidenceRelation,
    IdentityProfile,
    IdentityReference,
    MediaIdentityFile,
)
from .normal import (
    NormalPreviewOcrExecutor,
    NormalPreviewOcrRun,
    PreviewOcrObservation,
    NormalResourceLimits,
    NormalSamplingStage,
    OcrEngine,
    OcrTextResult,
)
from .service import MediaIdentityDecisionService
from .speech import SpeechEngine, SpeechModelIdentity
from .speech_audio import (
    LocalFfmpegSpeechAudioExtractor,
    normalize_speech_language,
)
from .speech_service import (
    NormalSpeechRun,
    NormalSpeechService,
    NormalSpeechStaleError,
)
from .fast import TEXT_SUPPORT_THRESHOLD
from .text import TextCorpus, synopsis_similarity_from_corpus, text_corpus
from .visual_budget import VisualAttemptBudget
from .versions import (
    NORMAL_EVIDENCE_ALGORITHM_VERSION,
    NORMAL_SPEECH_EVIDENCE_ALGORITHM_VERSION,
    NORMAL_SPEECH_ORCHESTRATION_VERSION,
)


NORMAL_OCR_ARTIFACT_KEY = "external-preview-ocr"
NORMAL_OCR_ARTIFACT_VERSION = "1"
NORMAL_OCR_EVIDENCE_KEY = "preview-ocr-synopsis"
NORMAL_OCR_EVIDENCE_VERSION = str(NORMAL_EVIDENCE_ALGORITHM_VERSION)
NORMAL_OCR_SUPPORT_THRESHOLD = 0.30
NORMAL_INITIAL_STOP_SIMILARITY = 0.55
NORMAL_INITIAL_STOP_MARGIN = 0.18
NORMAL_EXPANDED_STOP_SIMILARITY = 0.45
NORMAL_EXPANDED_STOP_MARGIN = 0.14
NORMAL_EARLY_STOP_MIN_OCR_CONFIDENCE = 0.60
NORMAL_UNCALIBRATED_OCR_QUALITY = 0.35
NORMAL_FALLBACK_MIN_SIMILARITY = 0.35
NORMAL_FALLBACK_MIN_MARGIN = 0.08
NORMAL_SPEECH_EVIDENCE_KEY = "speech-synopsis"
NORMAL_SPEECH_EVIDENCE_VERSION = str(NORMAL_SPEECH_EVIDENCE_ALGORITHM_VERSION)


class NormalIdentityScanError(RuntimeError):
    """Raised when a Normal OCR scan cannot be persisted safely."""


@dataclass(frozen=True)
class NormalScanResult:
    scan_id: int
    completed_profile: IdentityProfile
    source_key: str
    observation_count: int
    text_observation_count: int
    reused_artifact_count: int
    evidence_count: int
    highest_observed_stage: NormalSamplingStage | None
    failures: tuple[str, ...]
    budget_exhausted: bool
    visual_frame_attempt_count: int = 0
    visual_source_bytes: int = 0
    visual_image_bytes: int = 0
    visual_text_chars: int = 0
    speech_escalated: bool = False
    speech_planned_window_count: int = 0
    speech_transcript_count: int = 0
    speech_text_transcript_count: int = 0
    speech_reused_artifact_count: int = 0
    speech_failures: tuple[str, ...] = ()
    speech_budget_exhausted: bool = False


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


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


class NormalIdentityService:
    """Extend one fresh Fast scan with bounded external-preview OCR evidence."""

    @staticmethod
    def _persisted_winner_result(
        scan: Mapping[str, Any],
        evidence: list[dict[str, Any]],
    ) -> NormalScanResult:
        claimed = _json_object(scan.get("claimed_identity_json"))
        normal_ocr = claimed.get("normal_ocr")
        if not isinstance(normal_ocr, Mapping):
            normal_ocr = {}
        normal_speech = claimed.get("normal_speech")
        if not isinstance(normal_speech, Mapping):
            normal_speech = {}

        def _count(mapping: Mapping[str, Any], key: str) -> int:
            try:
                return max(0, int(mapping.get(key) or 0))
            except (TypeError, ValueError):
                return 0

        highest_stage = None
        try:
            raw_stage = int(normal_ocr.get("highest_observed_stage") or 0)
            if raw_stage:
                highest_stage = NormalSamplingStage(raw_stage)
        except (TypeError, ValueError):
            highest_stage = None

        ocr_failures = normal_ocr.get("failures")
        speech_failures = normal_speech.get("failures")
        completed = (
            IdentityProfile.NORMAL
            if str(scan.get("completed_profile") or "") == IdentityProfile.NORMAL.value
            else IdentityProfile.FAST
        )
        return NormalScanResult(
            scan_id=int(scan["id"]),
            completed_profile=completed,
            source_key=str(normal_ocr.get("source_key") or ""),
            observation_count=_count(normal_ocr, "observation_count"),
            text_observation_count=_count(normal_ocr, "text_observation_count"),
            reused_artifact_count=_count(normal_ocr, "reused_artifact_count"),
            evidence_count=sum(
                str(item.get("analyzer_key") or "")
                in {NORMAL_OCR_EVIDENCE_KEY, NORMAL_SPEECH_EVIDENCE_KEY}
                for item in evidence
            ),
            highest_observed_stage=highest_stage,
            failures=(
                tuple(str(item) for item in ocr_failures)
                if isinstance(ocr_failures, list)
                else ()
            ),
            budget_exhausted=bool(normal_ocr.get("budget_exhausted")),
            visual_frame_attempt_count=_count(
                normal_ocr, "frame_attempt_count"
            ),
            visual_source_bytes=_count(normal_ocr, "source_bytes"),
            visual_image_bytes=_count(normal_ocr, "image_bytes"),
            visual_text_chars=_count(normal_ocr, "text_chars"),
            speech_escalated=bool(normal_speech.get("escalated")),
            speech_planned_window_count=_count(normal_speech, "planned_windows"),
            speech_transcript_count=_count(normal_speech, "transcript_count"),
            speech_text_transcript_count=_count(
                normal_speech, "text_transcript_count"
            ),
            speech_reused_artifact_count=_count(
                normal_speech, "reused_artifact_count"
            ),
            speech_failures=(
                tuple(str(item) for item in speech_failures)
                if isinstance(speech_failures, list)
                else ()
            ),
            speech_budget_exhausted=bool(
                normal_speech.get("budget_exhausted")
            ),
        )

    def __init__(
        self,
        database: Database,
        registry: ExternalSourceRegistry,
        engine: OcrEngine,
        *,
        limits: NormalResourceLimits | None = None,
        speech_engine: SpeechEngine | None = None,
        speech_model: SpeechModelIdentity | None = None,
        speech_extractor_factory: Callable[..., LocalFfmpegSpeechAudioExtractor] = (
            LocalFfmpegSpeechAudioExtractor
        ),
    ) -> None:
        if (speech_engine is None) != (speech_model is None):
            raise NormalIdentityScanError(
                "Normal speech requires both a speech engine and model identity."
            )
        self.database = database
        self.registry = registry
        self.engine = engine
        self.limits = limits or NormalResourceLimits()
        self.speech_engine = speech_engine
        self.speech_model = speech_model
        self.speech_extractor_factory = speech_extractor_factory

    @staticmethod
    def _scan_rows(
        conn: sqlite3.Connection,
        scan_id: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        scan_row = conn.execute(
            "SELECT * FROM media_identity_scans WHERE id=?",
            (int(scan_id),),
        ).fetchone()
        if not scan_row:
            raise NormalIdentityScanError("Episode Identity scan was not found.")
        scan = dict(scan_row)
        candidates = [
            dict(row)
            for row in conn.execute(
                """SELECT * FROM media_identity_candidates
                   WHERE scan_id=? ORDER BY rank,candidate_key""",
                (int(scan_id),),
            ).fetchall()
        ]
        evidence = [
            dict(row)
            for row in conn.execute(
                """SELECT * FROM media_identity_evidence
                   WHERE scan_id=? ORDER BY id""",
                (int(scan_id),),
            ).fetchall()
        ]
        return scan, candidates, evidence

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
            raise NormalIdentityScanError(
                "The Fast scan does not contain a usable claimed episode identity."
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
            profile=IdentityProfile.NORMAL,
            metadata_signature=str(scan["metadata_signature"] or ""),
        )

    def _cached_ocr(
        self,
        scan: Mapping[str, Any],
        frame: PreviewFrameRef,
        cache_key: str,
    ) -> OcrTextResult | None:
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT text_value,payload_json,source_signature,
                          file_size_bytes,file_modified_at
                   FROM media_identity_artifacts
                   WHERE file_id=? AND artifact_type='visual_text'
                     AND analyzer_key=? AND analyzer_version=?
                     AND cache_key=? AND status='complete'
                   ORDER BY id DESC LIMIT 1""",
                (
                    int(scan["file_id"]),
                    NORMAL_OCR_ARTIFACT_KEY,
                    NORMAL_OCR_ARTIFACT_VERSION,
                    str(cache_key),
                ),
            ).fetchone()
        if not row:
            return None
        if str(row["source_signature"] or "") != str(frame.source_signature or ""):
            return None
        if int(row["file_size_bytes"] or 0) != int(scan["file_size_bytes"] or 0):
            return None
        if not _same_modified_at(
            row["file_modified_at"],
            scan["file_modified_at"],
        ):
            return None

        payload = _json_object(row["payload_json"])
        confidence = payload.get("confidence")
        try:
            normalized_confidence = (
                None if confidence is None else float(confidence)
            )
        except (TypeError, ValueError):
            normalized_confidence = None
        details = payload.get("details")
        return OcrTextResult(
            text=str(row["text_value"] or ""),
            confidence=normalized_confidence,
            details=details if isinstance(details, Mapping) else {},
        )

    @staticmethod
    def _aggregate_cache_key(run: NormalPreviewOcrRun) -> str:
        payload = {
            "source_key": run.source_key,
            "observations": [
                {
                    "cache_key": item.cache_key,
                    "source_signature": item.source_signature,
                    "timestamp_ms": item.timestamp_ms,
                }
                for item in run.observations
            ],
        }
        return hashlib.sha256(
            _canonical_json(payload).encode("utf-8")
        ).hexdigest()

    def _persist_artifacts(
        self,
        conn: sqlite3.Connection,
        scan: Mapping[str, Any],
        run: NormalPreviewOcrRun,
    ) -> tuple[NormalPreviewOcrRun, list[int]]:
        artifact_ids: list[int] = []
        persisted_observations: list[PreviewOcrObservation] = []
        for item in run.observations:
            payload = {
                "confidence": item.confidence,
                "stage": int(item.stage),
                "ordinal": int(item.ordinal),
                "image_bytes": int(item.image_bytes),
                "reused": bool(item.reused),
                "item_id": item.item_id,
                "engine_key": str(self.engine.key),
                "engine_version": str(self.engine.version),
                "details": dict(item.details),
            }
            cursor = conn.execute(
                """INSERT OR IGNORE INTO media_identity_artifacts(
                     file_id,artifact_type,analyzer_key,analyzer_version,
                     cache_key,status,profile,source_kind,source_ref,
                     source_signature,file_size_bytes,file_modified_at,
                     start_ms,end_ms,text_value,payload_json
                   ) VALUES (
                     ?,'visual_text',?,? ,?,'complete','normal',?,?,?,
                     ?,?,?,?,?,?
                   )""",
                (
                    int(scan["file_id"]),
                    NORMAL_OCR_ARTIFACT_KEY,
                    NORMAL_OCR_ARTIFACT_VERSION,
                    item.cache_key,
                    item.source_key,
                    item.asset_ref,
                    item.source_signature,
                    int(scan["file_size_bytes"] or 0),
                    scan["file_modified_at"],
                    int(item.timestamp_ms),
                    int(item.timestamp_ms),
                    item.text,
                    _canonical_json(payload),
                ),
            )
            inserted = cursor.rowcount == 1
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
                    int(scan["file_id"]),
                    NORMAL_OCR_ARTIFACT_KEY,
                    NORMAL_OCR_ARTIFACT_VERSION,
                    item.cache_key,
                ),
            ).fetchone()
            if not row:
                raise NormalIdentityScanError(
                    "InfoMancer could not persist the OCR artifact safely."
                )

            persisted_payload = _json_object(row["payload_json"])
            details = persisted_payload.get("details")
            confidence = persisted_payload.get("confidence")
            try:
                normalized_confidence = (
                    None if confidence is None else float(confidence)
                )
            except (TypeError, ValueError) as exc:
                raise NormalIdentityScanError(
                    "The persisted OCR winner has invalid confidence metadata."
                ) from exc
            if (
                normalized_confidence is not None
                and not 0.0 <= normalized_confidence <= 1.0
            ):
                raise NormalIdentityScanError(
                    "The persisted OCR winner has invalid confidence metadata."
                )
            try:
                persisted_start = int(row["start_ms"])
                persisted_end = int(row["end_ms"])
                image_bytes = int(persisted_payload.get("image_bytes") or 0)
            except (TypeError, ValueError) as exc:
                raise NormalIdentityScanError(
                    "The persisted OCR winner has invalid frame provenance."
                ) from exc

            if (
                int(row["file_id"]) != int(scan["file_id"])
                or str(row["artifact_type"] or "") != "visual_text"
                or str(row["analyzer_key"] or "") != NORMAL_OCR_ARTIFACT_KEY
                or str(row["analyzer_version"] or "") != NORMAL_OCR_ARTIFACT_VERSION
                or str(row["cache_key"] or "") != str(item.cache_key)
                or str(row["status"] or "") != "complete"
                or str(row["profile"] or "") != IdentityProfile.NORMAL.value
                or str(row["source_kind"] or "") != str(item.source_key)
                or str(row["source_ref"] or "") != str(item.asset_ref)
                or str(row["source_signature"] or "") != str(item.source_signature)
                or int(row["file_size_bytes"] or 0)
                != int(scan["file_size_bytes"] or 0)
                or not _same_modified_at(
                    row["file_modified_at"],
                    scan["file_modified_at"],
                )
                or persisted_start != int(item.timestamp_ms)
                or persisted_end != int(item.timestamp_ms)
                or str(persisted_payload.get("item_id") or "") != str(item.item_id)
                or str(persisted_payload.get("engine_key") or "")
                != str(self.engine.key)
                or str(persisted_payload.get("engine_version") or "")
                != str(self.engine.version)
                or not isinstance(details, Mapping)
                or image_bytes < 0
            ):
                raise NormalIdentityScanError(
                    "The persisted OCR winner does not match the current frame "
                    "and engine provenance."
                )

            artifact_id = int(row["id"])
            artifact_ids.append(artifact_id)
            conn.execute(
                """UPDATE media_identity_artifacts
                   SET last_used_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (artifact_id,),
            )
            persisted_observations.append(
                PreviewOcrObservation(
                    source_key=str(row["source_kind"] or ""),
                    item_id=str(persisted_payload["item_id"]),
                    timestamp_ms=persisted_start,
                    stage=item.stage,
                    ordinal=item.ordinal,
                    cache_key=str(row["cache_key"] or ""),
                    source_signature=str(row["source_signature"] or ""),
                    asset_ref=str(row["source_ref"] or ""),
                    text=str(row["text_value"] or ""),
                    confidence=normalized_confidence,
                    image_bytes=image_bytes,
                    reused=(bool(item.reused) or not inserted),
                    details=dict(details),
                )
            )

        persisted_run = NormalPreviewOcrRun(
            source_key=run.source_key,
            observations=tuple(persisted_observations),
            failures=run.failures,
            total_image_bytes=run.total_image_bytes,
            total_source_bytes=run.total_source_bytes,
            total_text_chars=run.total_text_chars,
            total_frame_attempts=run.total_frame_attempts,
            budget_exhausted=run.budget_exhausted,
            planned_frame_count=run.planned_frame_count,
            completed_frame_count=run.completed_frame_count,
            coverage_complete=run.coverage_complete,
        )
        return persisted_run, artifact_ids

    @staticmethod
    def _visual_run_metrics(
        candidates: list[dict[str, Any]],
        run: NormalPreviewOcrRun,
    ) -> dict[str, Any]:
        usable = [item for item in run.observations if item.text.strip()]
        if not usable:
            return {
                "has_text": False,
                "fully_calibrated": False,
                "quality": 0.0,
                "best_similarity": 0.0,
                "margin": 0.0,
                "evidence_score": 0.0,
            }

        qualities = [
            (
                float(item.confidence)
                if item.confidence is not None
                else NORMAL_UNCALIBRATED_OCR_QUALITY
            )
            for item in usable
        ]
        quality = sum(qualities) / len(qualities)
        fully_calibrated = all(
            item.confidence is not None for item in usable
        )

        corpus = text_corpus("\n".join(item.text for item in usable))
        scores: list[float] = []
        for candidate in candidates:
            details = _json_object(candidate.get("details_json"))
            overview = str(details.get("overview") or "").strip()
            if overview:
                scores.append(
                    synopsis_similarity_from_corpus(corpus, overview)
                )
        scores.sort(reverse=True)
        best = scores[0] if scores else 0.0
        second = scores[1] if len(scores) > 1 else 0.0
        margin = max(0.0, best - second)
        return {
            "has_text": True,
            "fully_calibrated": fully_calibrated,
            "quality": quality,
            "best_similarity": best,
            "margin": margin,
            "evidence_score": best * quality,
        }

    @classmethod
    def _visual_stage_sufficient(
        cls,
        candidates: list[dict[str, Any]],
        run: NormalPreviewOcrRun,
        stage: NormalSamplingStage,
    ) -> bool:
        if stage >= NormalSamplingStage.FINAL or not run.coverage_complete:
            return False
        metrics = cls._visual_run_metrics(candidates, run)
        if (
            not metrics["has_text"]
            or not metrics["fully_calibrated"]
            or metrics["quality"] < NORMAL_EARLY_STOP_MIN_OCR_CONFIDENCE
        ):
            return False

        best = float(metrics["best_similarity"])
        margin = float(metrics["margin"])
        if stage == NormalSamplingStage.INITIAL:
            return (
                best >= NORMAL_INITIAL_STOP_SIMILARITY
                and margin >= NORMAL_INITIAL_STOP_MARGIN
            )
        return (
            best >= NORMAL_EXPANDED_STOP_SIMILARITY
            and margin >= NORMAL_EXPANDED_STOP_MARGIN
        )

    @classmethod
    def _visual_source_sufficient(
        cls,
        candidates: list[dict[str, Any]],
        run: NormalPreviewOcrRun,
    ) -> bool:
        metrics = cls._visual_run_metrics(candidates, run)
        return bool(
            run.coverage_complete
            and metrics["has_text"]
            and metrics["fully_calibrated"]
            and metrics["quality"] >= NORMAL_EARLY_STOP_MIN_OCR_CONFIDENCE
            and metrics["best_similarity"] >= NORMAL_FALLBACK_MIN_SIMILARITY
            and metrics["margin"] >= NORMAL_FALLBACK_MIN_MARGIN
        )

    @staticmethod
    def _subtitle_evidence_sufficient(
        evidence: list[dict[str, Any]],
    ) -> bool:
        """Return whether existing Fast subtitle evidence already separates candidates.

        Fast persists sub-threshold subtitle matches as neutral evidence with
        strength zero, while retaining the raw synopsis similarity in details.
        Escalation must use that raw similarity or a close runner-up can be
        hidden by thresholding and speech may be skipped incorrectly.
        """
        scores: list[float] = []
        for item in evidence:
            if (
                str(item.get("analyzer_key") or "") != "subtitle-synopsis"
                or not str(item.get("candidate_key") or "")
            ):
                continue
            details = _json_object(item.get("details_json"))
            raw_similarity = details.get("similarity")
            try:
                score = (
                    float(raw_similarity)
                    if raw_similarity is not None
                    else float(item.get("strength") or 0.0)
                )
            except (TypeError, ValueError):
                continue
            scores.append(max(0.0, min(1.0, score)))

        scores.sort(reverse=True)
        if not scores:
            return False
        best = scores[0]
        second = scores[1] if len(scores) > 1 else 0.0
        return (
            best >= NORMAL_FALLBACK_MIN_SIMILARITY
            and best - second >= NORMAL_FALLBACK_MIN_MARGIN
        )

    @classmethod
    def _prefer_visual_run(
        cls,
        candidates: list[dict[str, Any]],
        current: NormalPreviewOcrRun,
        candidate: NormalPreviewOcrRun,
    ) -> NormalPreviewOcrRun:
        current_metrics = cls._visual_run_metrics(candidates, current)
        candidate_metrics = cls._visual_run_metrics(candidates, candidate)
        current_key = (
            int(bool(current.coverage_complete)),
            float(current_metrics["evidence_score"]),
            float(current_metrics["margin"]),
            int(bool(current_metrics["fully_calibrated"])),
            float(current_metrics["best_similarity"]),
        )
        candidate_key = (
            int(bool(candidate.coverage_complete)),
            float(candidate_metrics["evidence_score"]),
            float(candidate_metrics["margin"]),
            int(bool(candidate_metrics["fully_calibrated"])),
            float(candidate_metrics["best_similarity"]),
        )
        return candidate if candidate_key > current_key else current

    @staticmethod
    def _speech_aggregate_cache_key(run: NormalSpeechRun) -> str:
        payload = {
            "observations": [
                {
                    "cache_key": item.cache_key,
                    "source_signature": item.source_signature,
                    "start_ms": int(item.window.start_ms),
                    "end_ms": int(item.window.end_ms),
                    "transcript_sha256": hashlib.sha256(
                        item.transcript.text.encode("utf-8")
                    ).hexdigest(),
                }
                for item in run.observations
                if item.transcript.text.strip()
            ],
        }
        return hashlib.sha256(
            _canonical_json(payload).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _speech_text_corpus(
        run: NormalSpeechRun,
        synopsis_language: str,
    ) -> tuple[TextCorpus, list[Any], list[Any]]:
        target_language = normalize_speech_language(synopsis_language)
        text_observations = [
            item for item in run.observations
            if item.transcript.text.strip()
        ]
        usable = [
            item for item in text_observations
            if normalize_speech_language(item.transcript.language)
            == target_language
        ]
        excluded = [
            item for item in text_observations
            if item not in usable
        ]
        tokens: set[str] = set()
        bigrams: set[tuple[str, str]] = set()
        for item in usable:
            corpus = text_corpus(item.transcript.text)
            tokens.update(corpus.tokens)
            bigrams.update(corpus.bigrams)
        return (
            TextCorpus(
                tokens=frozenset(tokens),
                bigrams=frozenset(bigrams),
            ),
            usable,
            excluded,
        )

    def _persist_speech_evidence(
        self,
        conn: sqlite3.Connection,
        scan: Mapping[str, Any],
        candidates: list[dict[str, Any]],
        run: NormalSpeechRun,
        *,
        escalated: bool,
        synopsis_language: str,
    ) -> int:
        conn.execute(
            """DELETE FROM media_identity_evidence
               WHERE scan_id=? AND analyzer_key=?""",
            (int(scan["id"]), NORMAL_SPEECH_EVIDENCE_KEY),
        )
        if not escalated:
            return 0

        correlation = f"subtitle-dialogue:{int(scan['file_id'])}"
        corpus, usable, language_mismatched = self._speech_text_corpus(
            run,
            synopsis_language,
        )
        aggregate_cache_key = (
            self._speech_aggregate_cache_key(run) if usable else ""
        )
        evidence_rows: list[tuple[Any, ...]] = []

        if usable:
            artifact_ids = [int(item.artifact_id) for item in usable]
            windows = [
                {
                    "artifact_id": int(item.artifact_id),
                    "start_ms": int(item.window.start_ms),
                    "end_ms": int(item.window.end_ms),
                    "cache_key": item.cache_key,
                }
                for item in usable
            ]
            transcript_excerpt = "\n".join(
                item.transcript.text.strip() for item in usable
            )[:1200]

        if usable and corpus.tokens:
            comparable = 0
            for candidate in candidates:
                details = _json_object(candidate.get("details_json"))
                overview = str(details.get("overview") or "").strip()
                if not overview:
                    continue
                comparable += 1
                similarity = synopsis_similarity_from_corpus(corpus, overview)
                relation = (
                    EvidenceRelation.SUPPORTS.value
                    if similarity >= TEXT_SUPPORT_THRESHOLD
                    else EvidenceRelation.NEUTRAL.value
                )
                evidence_rows.append((
                    int(scan["id"]),
                    str(candidate["candidate_key"]),
                    NORMAL_SPEECH_EVIDENCE_KEY,
                    NORMAL_SPEECH_EVIDENCE_VERSION,
                    EvidenceCategory.SPEECH.value,
                    correlation,
                    relation,
                    (
                        similarity
                        if relation == EvidenceRelation.SUPPORTS.value
                        else 0.0
                    ),
                    "local_speech_transcript",
                    f"file:{int(scan['file_id'])}:targeted-windows",
                    None,
                    f"targeted speech synopsis similarity {similarity:.3f}",
                    _canonical_json({
                        "similarity": similarity,
                        "support_threshold": TEXT_SUPPORT_THRESHOLD,
                        "artifact_ids": artifact_ids,
                        "windows": windows,
                        "transcript_count": len(usable),
                        "transcript_excerpt": transcript_excerpt,
                        "correlated_with": ["subtitle-synopsis"],
                        "synopsis_language": normalize_speech_language(
                            synopsis_language
                        ),
                        "transcript_languages": sorted({
                            normalize_speech_language(item.transcript.language)
                            for item in usable
                        }),
                    }),
                    aggregate_cache_key,
                    IdentityProfile.NORMAL.value,
                ))

            if comparable == 0:
                evidence_rows.append((
                    int(scan["id"]),
                    "",
                    NORMAL_SPEECH_EVIDENCE_KEY,
                    NORMAL_SPEECH_EVIDENCE_VERSION,
                    EvidenceCategory.SPEECH.value,
                    correlation,
                    EvidenceRelation.NEUTRAL.value,
                    0.0,
                    "local_speech_transcript",
                    f"file:{int(scan['file_id'])}:targeted-windows",
                    None,
                    "Targeted speech was available, but candidate synopses were unavailable.",
                    _canonical_json({
                        "artifact_ids": artifact_ids,
                        "windows": windows,
                        "transcript_count": len(usable),
                        "transcript_excerpt": transcript_excerpt,
                        "correlated_with": ["subtitle-synopsis"],
                        "synopsis_language": normalize_speech_language(
                            synopsis_language
                        ),
                        "transcript_languages": sorted({
                            normalize_speech_language(item.transcript.language)
                            for item in usable
                        }),
                    }),
                    aggregate_cache_key,
                    IdentityProfile.NORMAL.value,
                ))
        elif language_mismatched:
            evidence_rows.append((
                int(scan["id"]),
                "",
                NORMAL_SPEECH_EVIDENCE_KEY,
                NORMAL_SPEECH_EVIDENCE_VERSION,
                EvidenceCategory.SPEECH.value,
                correlation,
                EvidenceRelation.NEUTRAL.value,
                0.0,
                "local_speech_transcript",
                f"file:{int(scan['file_id'])}:targeted-windows",
                None,
                "Targeted speech language did not align with the candidate synopsis language.",
                _canonical_json({
                    "artifact_ids": [
                        int(item.artifact_id) for item in language_mismatched
                    ],
                    "transcript_count": len(language_mismatched),
                    "synopsis_language": normalize_speech_language(
                        synopsis_language
                    ),
                    "transcript_languages": sorted({
                        normalize_speech_language(item.transcript.language)
                        for item in language_mismatched
                    }),
                    "correlated_with": ["subtitle-synopsis"],
                }),
                "",
                IdentityProfile.NORMAL.value,
            ))
        elif usable:
            evidence_rows.append((
                int(scan["id"]),
                "",
                NORMAL_SPEECH_EVIDENCE_KEY,
                NORMAL_SPEECH_EVIDENCE_VERSION,
                EvidenceCategory.SPEECH.value,
                correlation,
                EvidenceRelation.NEUTRAL.value,
                0.0,
                "local_speech_transcript",
                f"file:{int(scan['file_id'])}:targeted-windows",
                None,
                "Targeted speech contained no lexical tokens usable for synopsis comparison.",
                _canonical_json({
                    "artifact_ids": artifact_ids,
                    "windows": windows,
                    "transcript_count": len(usable),
                    "transcript_excerpt": transcript_excerpt,
                    "correlated_with": ["subtitle-synopsis"],
                    "synopsis_language": normalize_speech_language(
                        synopsis_language
                    ),
                    "transcript_languages": sorted({
                        normalize_speech_language(item.transcript.language)
                        for item in usable
                    }),
                }),
                aggregate_cache_key,
                IdentityProfile.NORMAL.value,
            ))
        else:
            evidence_rows.append((
                int(scan["id"]),
                "",
                NORMAL_SPEECH_EVIDENCE_KEY,
                NORMAL_SPEECH_EVIDENCE_VERSION,
                EvidenceCategory.SPEECH.value,
                correlation,
                EvidenceRelation.NEUTRAL.value,
                0.0,
                "local_speech_transcript",
                f"file:{int(scan['file_id'])}:targeted-windows",
                None,
                "Speech escalation produced no usable transcript text.",
                _canonical_json({
                    "planned_windows": len(run.planned_windows),
                    "transcript_count": run.transcript_count,
                    "failures": list(run.failures),
                    "budget_exhausted": bool(run.budget_exhausted),
                    "correlated_with": ["subtitle-synopsis"],
                }),
                "",
                IdentityProfile.NORMAL.value,
            ))

        conn.executemany(
            """INSERT INTO media_identity_evidence(
                 scan_id,candidate_key,analyzer_key,analyzer_version,
                 evidence_category,correlation_group,relation,strength,
                 source_kind,source_ref,timestamp_ms,value_text,details_json,
                 cache_key,profile
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            evidence_rows,
        )
        return len(evidence_rows)

    def _persist_visual_evidence(
        self,
        conn: sqlite3.Connection,
        scan: Mapping[str, Any],
        candidates: list[dict[str, Any]],
        run: NormalPreviewOcrRun,
        artifact_ids: list[int],
    ) -> int:
        conn.execute(
            """DELETE FROM media_identity_evidence
               WHERE scan_id=? AND analyzer_key=?""",
            (int(scan["id"]), NORMAL_OCR_EVIDENCE_KEY),
        )

        correlation = (
            f"visual-text:{int(scan['file_id'])}:{run.source_key or 'unavailable'}"
        )
        usable = [item for item in run.observations if item.text.strip()]
        combined_text = "\n".join(item.text for item in usable)
        aggregate_cache_key = (
            self._aggregate_cache_key(run) if run.observations else ""
        )
        evidence_rows: list[tuple[Any, ...]] = []

        if usable:
            corpus = text_corpus(combined_text)
            observation_qualities = [
                (
                    float(item.confidence)
                    if item.confidence is not None
                    else NORMAL_UNCALIBRATED_OCR_QUALITY
                )
                for item in usable
            ]
            quality = (
                sum(observation_qualities) / len(observation_qualities)
                if observation_qualities
                else 0.0
            )
            comparable = 0
            for candidate in candidates:
                details = _json_object(candidate.get("details_json"))
                overview = str(details.get("overview") or "").strip()
                if not overview:
                    continue
                comparable += 1
                similarity = synopsis_similarity_from_corpus(corpus, overview)
                strength = round(
                    max(0.0, min(1.0, similarity * quality)),
                    6,
                )
                relation = (
                    EvidenceRelation.SUPPORTS.value
                    if similarity >= NORMAL_OCR_SUPPORT_THRESHOLD
                    else EvidenceRelation.NEUTRAL.value
                )
                evidence_rows.append((
                    int(scan["id"]),
                    str(candidate["candidate_key"]),
                    NORMAL_OCR_EVIDENCE_KEY,
                    NORMAL_OCR_EVIDENCE_VERSION,
                    EvidenceCategory.VISUAL_TEXT.value,
                    correlation,
                    relation,
                    strength if relation == EvidenceRelation.SUPPORTS.value else 0.0,
                    (
                        "generated_preview_ocr"
                        if run.source_key == LOCAL_FRAME_SOURCE_KEY
                        else "external_preview_ocr"
                    ),
                    f"{run.source_key}:{usable[0].item_id}",
                    None,
                    f"synopsis similarity {similarity:.3f}",
                    _canonical_json({
                        "similarity": similarity,
                        "support_threshold": NORMAL_OCR_SUPPORT_THRESHOLD,
                        "ocr_quality": round(quality, 6),
                        "calibrated_observations": sum(
                            item.confidence is not None for item in usable
                        ),
                        "uncalibrated_observations": sum(
                            item.confidence is None for item in usable
                        ),
                        "artifact_ids": artifact_ids,
                        "timestamps_ms": [item.timestamp_ms for item in usable],
                        "source_signatures": sorted({
                            item.source_signature for item in usable
                        }),
                        "engine_key": str(self.engine.key),
                        "engine_version": str(self.engine.version),
                        "text_excerpt": combined_text[:4000],
                    }),
                    aggregate_cache_key,
                    IdentityProfile.NORMAL.value,
                ))

            if comparable == 0:
                evidence_rows.append((
                    int(scan["id"]),
                    "",
                    NORMAL_OCR_EVIDENCE_KEY,
                    NORMAL_OCR_EVIDENCE_VERSION,
                    EvidenceCategory.VISUAL_TEXT.value,
                    correlation,
                    EvidenceRelation.NEUTRAL.value,
                    0.0,
                    (
                        "generated_preview_ocr"
                        if run.source_key == LOCAL_FRAME_SOURCE_KEY
                        else "external_preview_ocr"
                    ),
                    f"{run.source_key}:{usable[0].item_id}",
                    None,
                    "OCR text was available, but candidate synopses were unavailable.",
                    _canonical_json({
                        "artifact_ids": artifact_ids,
                        "engine_key": str(self.engine.key),
                        "engine_version": str(self.engine.version),
                    }),
                    aggregate_cache_key,
                    IdentityProfile.NORMAL.value,
                ))
        else:
            evidence_rows.append((
                int(scan["id"]),
                "",
                NORMAL_OCR_EVIDENCE_KEY,
                NORMAL_OCR_EVIDENCE_VERSION,
                EvidenceCategory.VISUAL_TEXT.value,
                correlation,
                EvidenceRelation.NEUTRAL.value,
                0.0,
                (
                    "generated_preview_ocr"
                    if run.source_key == LOCAL_FRAME_SOURCE_KEY
                    else "external_preview_ocr"
                ),
                run.source_key,
                None,
                "No usable visual text was produced by Normal OCR.",
                _canonical_json({
                    "failures": list(run.failures),
                    "observation_count": len(run.observations),
                    "engine_key": str(self.engine.key),
                    "engine_version": str(self.engine.version),
                }),
                aggregate_cache_key,
                IdentityProfile.NORMAL.value,
            ))

        conn.executemany(
            """INSERT INTO media_identity_evidence(
                 scan_id,candidate_key,analyzer_key,analyzer_version,
                 evidence_category,correlation_group,relation,strength,
                 source_kind,source_ref,timestamp_ms,value_text,details_json,
                 cache_key,profile
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            evidence_rows,
        )
        return len(evidence_rows)

    def run_scan(
        self,
        scan_id: int,
        *,
        max_stage: NormalSamplingStage = NormalSamplingStage.FINAL,
    ) -> NormalScanResult:
        with self.database.connect() as conn:
            scan, candidates, evidence = self._scan_rows(conn, int(scan_id))
            if scan["status"] != "complete":
                raise NormalIdentityScanError(
                    "Normal OCR requires a complete Fast identity scan."
                )
            current, file_row = MediaIdentityDecisionService._scan_snapshot_is_current(
                conn,
                scan,
                evidence,
            )
            if not current or file_row is None:
                raise NormalIdentityScanError(
                    "The Fast identity snapshot is stale. Verify the file again before Normal OCR."
                )
            context = self._context(scan, file_row)
            runtime_row = conn.execute(
                "SELECT runtime_seconds FROM files WHERE id=?",
                (int(scan["file_id"]),),
            ).fetchone()
            runtime_seconds = (
                runtime_row["runtime_seconds"]
                if runtime_row is not None
                else None
            )
            streams = [
                dict(row)
                for row in conn.execute(
                    """SELECT stream_index,stream_type,codec,language,title,channels,
                              channel_layout,sample_rate,default_flag,forced_flag,
                              hearing_impaired,visual_impaired,commentary,disposition_json
                       FROM media_streams
                       WHERE file_id=? ORDER BY stream_index""",
                    (int(scan["file_id"]),),
                ).fetchall()
            ]
            claimed_before_normal = _json_object(scan["claimed_identity_json"])
            external_config_signatures = {
                key: external_source_config_signature(conn, key) or "unconfigured"
                for key in ("plex", "jellyfin")
            }
            base_result_revision = result_revision(claimed_before_normal)
            if base_result_revision <= 0:
                raise NormalIdentityScanError(
                    "The Fast result does not have a sealed publication revision. "
                    "Run Fast verification again before Normal analysis."
                )
            speech_language = str(
                claimed_before_normal.get("scan_language") or "eng"
            ).strip().casefold() or "eng"

        cache_lookup = lambda frame, cache_key: self._cached_ocr(
            scan,
            frame,
            cache_key,
        )
        visual_budget = VisualAttemptBudget(
            max_frame_attempts=int(self.limits.max_preview_frames),
            max_source_bytes=int(self.limits.max_source_bytes_total),
            max_image_bytes=int(self.limits.max_preview_bytes_total),
            max_text_chars=int(self.limits.max_ocr_text_chars),
        )
        executor = NormalPreviewOcrExecutor(
            self.registry,
            self.engine,
            limits=self.limits,
            cache_lookup=cache_lookup,
        )
        stage_sufficient = lambda current_run, stage: self._visual_stage_sufficient(
            candidates,
            current_run,
            stage,
        )
        source_sufficient = lambda current_run: self._visual_source_sufficient(
            candidates,
            current_run,
        )
        source_preference = lambda current_run, candidate_run: self._prefer_visual_run(
            candidates,
            current_run,
            candidate_run,
        )
        run = executor.run(
            context,
            max_stage=max_stage,
            stage_sufficient=stage_sufficient,
            source_sufficient=source_sufficient,
            source_preference=source_preference,
            budget=visual_budget,
        )

        if (
            not self._visual_source_sufficient(candidates, run)
            and not run.budget_exhausted
            and "ocr-engine-unavailable" not in set(run.failures)
        ):
            local_source = LocalFfmpegFrameSource(
                context,
                runtime_seconds,
            )
            try:
                local_status = local_source.status()
                if not local_status.available:
                    local_run = NormalPreviewOcrRun(
                        failures=(
                            f"{LOCAL_FRAME_SOURCE_KEY}:unavailable:{local_status.detail}",
                        ),
                        total_image_bytes=visual_budget.image_bytes,
                        total_source_bytes=visual_budget.source_bytes,
                        total_text_chars=visual_budget.text_chars,
                        total_frame_attempts=visual_budget.frame_attempts,
                        budget_exhausted=False,
                    )
                else:
                    local_executor = NormalPreviewOcrExecutor(
                        ExternalSourceRegistry([local_source]),
                        self.engine,
                        limits=self.limits,
                        cache_lookup=cache_lookup,
                    )
                    local_run = local_executor.run(
                        context,
                        max_stage=max_stage,
                        stage_sufficient=stage_sufficient,
                        budget=visual_budget,
                    )
            finally:
                close = getattr(local_source, "close", None)
                if callable(close):
                    close()

            external_run = run
            combined_failures = (
                tuple(external_run.failures)
                + tuple(local_run.failures)
            )
            preferred = self._prefer_visual_run(
                candidates,
                external_run,
                local_run,
            )
            run = NormalPreviewOcrRun(
                source_key=preferred.source_key,
                observations=preferred.observations,
                failures=combined_failures,
                total_image_bytes=visual_budget.image_bytes,
                total_source_bytes=visual_budget.source_bytes,
                total_text_chars=visual_budget.text_chars,
                total_frame_attempts=visual_budget.frame_attempts,
                budget_exhausted=local_run.budget_exhausted,
                planned_frame_count=preferred.planned_frame_count,
                completed_frame_count=preferred.completed_frame_count,
                coverage_complete=(
                    preferred.coverage_complete
                    and not local_run.budget_exhausted
                ),
            )

        speech_run = NormalSpeechRun()
        speech_escalated = False
        cheaper_evidence_sufficient = (
            self._subtitle_evidence_sufficient(evidence)
            or self._visual_source_sufficient(candidates, run)
        )
        if (
            not cheaper_evidence_sufficient
            and self.speech_engine is not None
            and self.speech_model is not None
        ):
            speech_escalated = True
            speech_service = NormalSpeechService(
                self.database,
                self.speech_engine,
                self.speech_model,
                extractor_factory=self.speech_extractor_factory,
                language=speech_language,
                synopsis_language=speech_language,
                preferred_audio_language=speech_language,
                translation_target_language=(
                    "eng"
                    if normalize_speech_language(speech_language) == "eng"
                    else ""
                ),
            )
            try:
                speech_run = speech_service.run(
                    int(scan_id),
                    scan,
                    context.media,
                    runtime_seconds,
                    streams,
                )
            except NormalSpeechStaleError as exc:
                raise NormalIdentityScanError(
                    "Episode Identity inputs changed during Normal speech analysis. "
                    "Retry verification."
                ) from exc

        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current_scan, candidates, evidence = self._scan_rows(conn, int(scan_id))
            current, _ = MediaIdentityDecisionService._scan_snapshot_is_current(
                conn,
                current_scan,
                evidence,
            )
            if not current:
                raise NormalIdentityScanError(
                    "Episode Identity inputs changed during Normal OCR. Retry verification."
                )
            if str(current_scan["metadata_signature"] or "") != str(
                scan["metadata_signature"] or ""
            ):
                raise NormalIdentityScanError(
                    "Episode Identity metadata changed during Normal OCR. Retry verification."
                )

            if result_revision(current_scan) != base_result_revision:
                return self._persisted_winner_result(
                    current_scan,
                    evidence,
                )

            selected_external_source = str(run.source_key or "").strip().casefold()
            if selected_external_source in external_config_signatures:
                current_config_signature = (
                    external_source_config_signature(
                        conn,
                        selected_external_source,
                    )
                    or "unconfigured"
                )
                if (
                    current_config_signature
                    != external_config_signatures[selected_external_source]
                ):
                    raise NormalIdentityScanError(
                        "External preview-source configuration changed during "
                        "Normal OCR. Retry verification."
                    )

            current_claimed = _json_object(current_scan["claimed_identity_json"])
            current_previous_normal_evidence = any(
                str(item.get("analyzer_key") or "") == NORMAL_OCR_EVIDENCE_KEY
                for item in evidence
            )
            current_previous_completed_normal = (
                str(current_scan.get("completed_profile") or "")
                == IdentityProfile.NORMAL.value
            )
            current_previous_ocr_metadata = current_claimed.get("normal_ocr")
            current_previous_speech_metadata = current_claimed.get("normal_speech")
            previous_ocr_coverage_complete = bool(
                isinstance(current_previous_ocr_metadata, Mapping)
                and current_previous_ocr_metadata.get("coverage_complete") is True
            )
            previous_speech_coverage_complete = bool(
                isinstance(current_previous_speech_metadata, Mapping)
                and current_previous_speech_metadata.get("coverage_complete") is True
            )
            try:
                current_previous_speech_count = (
                    int(
                        current_previous_speech_metadata.get("transcript_count")
                        or 0
                    )
                    if isinstance(current_previous_speech_metadata, Mapping)
                    else 0
                )
            except (TypeError, ValueError):
                current_previous_speech_count = 0
            previous_complete_normal_coverage = bool(
                current_previous_completed_normal
                and (
                    (
                        current_previous_normal_evidence
                        and previous_ocr_coverage_complete
                    )
                    or (
                        current_previous_speech_count > 0
                        and previous_speech_coverage_complete
                    )
                )
            )

            if (
                previous_complete_normal_coverage
                and run.observations
                and not run.coverage_complete
            ):
                raise NormalIdentityScanError(
                    "Normal rerun only completed part of its visual coverage. "
                    "The existing completed Normal evidence was retained."
                )

            if (
                previous_complete_normal_coverage
                and speech_escalated
                and not speech_run.coverage_complete
            ):
                raise NormalIdentityScanError(
                    "Normal rerun only completed part of the speech coverage still "
                    "needed by this scan. The existing completed Normal evidence "
                    "was retained."
                )

            if (
                not run.observations
                and not speech_run.observations
                and current_previous_completed_normal
                and current_previous_normal_evidence
            ):
                raise NormalIdentityScanError(
                    "Normal rerun produced no replacement observations. "
                    "The existing completed Normal evidence was retained."
                )

            retain_previous_visual = (
                not run.observations
                and str(current_scan.get("completed_profile") or "")
                == IdentityProfile.NORMAL.value
                and current_previous_normal_evidence
            )
            if retain_previous_visual:
                evidence_count = 0
            else:
                run, artifact_ids = self._persist_artifacts(
                    conn,
                    current_scan,
                    run,
                )
                evidence_count = self._persist_visual_evidence(
                    conn,
                    current_scan,
                    candidates,
                    run,
                    artifact_ids,
                )

            completed_normal = bool(
                run.observations or speech_run.observations
            )
            speech_evidence_count = self._persist_speech_evidence(
                conn,
                current_scan,
                candidates,
                speech_run,
                escalated=(speech_escalated and completed_normal),
                synopsis_language=speech_language,
            )
            evidence_count += speech_evidence_count

            claimed = _json_object(current_scan["claimed_identity_json"])
            if not retain_previous_visual:
                claimed["normal_ocr"] = {
                    "version": 1,
                    "algorithm_version": NORMAL_EVIDENCE_ALGORITHM_VERSION,
                    "source_key": run.source_key,
                    "source_config_signature": (
                        external_config_signatures.get(
                            str(run.source_key or "").strip().casefold(),
                            "",
                        )
                    ),
                    "engine_key": str(self.engine.key),
                    "engine_version": str(self.engine.version),
                    "max_stage": int(max_stage),
                    "highest_observed_stage": max(
                        (int(item.stage) for item in run.observations),
                        default=0,
                    ),
                    "observation_cache_keys": [
                        item.cache_key for item in run.observations
                    ],
                    "observation_count": len(run.observations),
                    "text_observation_count": sum(
                        1 for item in run.observations if item.text.strip()
                    ),
                    "planned_frame_count": int(run.planned_frame_count),
                    "completed_frame_count": int(run.completed_frame_count),
                    "coverage_complete": bool(run.coverage_complete),
                    "reused_artifact_count": sum(
                        1 for item in run.observations if item.reused
                    ),
                    "frame_attempt_count": int(run.total_frame_attempts),
                    "source_bytes": int(run.total_source_bytes),
                    "image_bytes": int(run.total_image_bytes),
                    "text_chars": int(run.total_text_chars),
                    "failures": list(run.failures),
                    "budget_exhausted": bool(run.budget_exhausted),
                }
            claimed["normal_speech"] = {
                "version": 1,
                "algorithm_version": NORMAL_SPEECH_ORCHESTRATION_VERSION,
                "evidence_algorithm_version": NORMAL_SPEECH_EVIDENCE_ALGORITHM_VERSION,
                "escalated": bool(speech_escalated),
                "planned_windows": len(speech_run.planned_windows),
                "transcript_count": speech_run.transcript_count,
                "text_transcript_count": speech_run.text_transcript_count,
                "coverage_complete": bool(speech_run.coverage_complete),
                "reused_artifact_count": speech_run.reused_artifact_count,
                "artifact_ids": [
                    item.artifact_id for item in speech_run.observations
                ],
                "cache_keys": [
                    item.cache_key for item in speech_run.observations
                ],
                "failures": list(speech_run.failures),
                "budget_exhausted": bool(speech_run.budget_exhausted),
                "synopsis_language": speech_run.synopsis_language,
                "preferred_audio_language": speech_run.preferred_audio_language,
                "selected_audio_language": speech_run.selected_audio_language,
                "transcription_input_language": (
                    speech_run.transcription_input_language
                ),
                "translation_target_language": (
                    speech_run.translation_target_language
                ),
            }
            conn.execute(
                """UPDATE media_identity_scans
                   SET requested_profile='normal',
                       completed_profile=?,
                       stage=?,
                       claimed_identity_json=?,
                       result_state=NULL,
                       best_candidate_key=NULL,
                       completed_at=CURRENT_TIMESTAMP,
                       error=''
                   WHERE id=?""",
                (
                    (
                        IdentityProfile.NORMAL.value
                        if completed_normal
                        else IdentityProfile.FAST.value
                    ),
                    (
                        "normal_speech_complete"
                        if speech_run.observations
                        else "normal_ocr_complete"
                        if run.observations
                        else "normal_ocr_unavailable"
                    ),
                    _canonical_json(claimed),
                    int(scan_id),
                ),
            )
            seal_decision_snapshot(
                conn,
                int(scan_id),
                revision=base_result_revision + 1,
            )

        return NormalScanResult(
            scan_id=int(scan_id),
            completed_profile=(
                IdentityProfile.NORMAL
                if completed_normal
                else IdentityProfile.FAST
            ),
            source_key=run.source_key,
            observation_count=len(run.observations),
            text_observation_count=sum(
                1 for item in run.observations if item.text.strip()
            ),
            reused_artifact_count=sum(
                1 for item in run.observations if item.reused
            ),
            evidence_count=evidence_count,
            highest_observed_stage=max(
                (item.stage for item in run.observations),
                default=None,
            ),
            failures=run.failures,
            budget_exhausted=run.budget_exhausted,
            visual_frame_attempt_count=int(run.total_frame_attempts),
            visual_source_bytes=int(run.total_source_bytes),
            visual_image_bytes=int(run.total_image_bytes),
            visual_text_chars=int(run.total_text_chars),
            speech_escalated=speech_escalated,
            speech_planned_window_count=len(speech_run.planned_windows),
            speech_transcript_count=speech_run.transcript_count,
            speech_text_transcript_count=speech_run.text_transcript_count,
            speech_reused_artifact_count=speech_run.reused_artifact_count,
            speech_failures=speech_run.failures,
            speech_budget_exhausted=speech_run.budget_exhausted,
        )

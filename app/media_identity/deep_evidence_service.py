from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import sqlite3
from typing import Any, Mapping

from ..db import Database
from .deep import (
    DeepCandidatePolicy,
    DeepCorrelationPolicy,
    build_deep_plan_metadata,
    generate_deep_episode_candidates,
    plan_deep_correlation,
)
from .deep_evidence import (
    DEEP_EVIDENCE_VERSION,
    DEEP_SPEECH_EVIDENCE_KEY,
    DEEP_VISUAL_EVIDENCE_KEY,
)
from .deep_sampling_service import (
    DEEP_SAMPLING_MANIFEST_KEY,
    DEEP_SAMPLING_MANIFEST_VERSION,
    DeepSamplingRun,
)
from .deep_speech_service import (
    DEEP_SPEECH_MANIFEST_KEY,
    DEEP_SPEECH_MANIFEST_VERSION,
    DeepSpeechSamplingRun,
)
from .decision_snapshot import result_revision, seal_decision_snapshot
from .fast import TEXT_SUPPORT_THRESHOLD
from .models import EvidenceCategory, EvidenceRelation, IdentityProfile
from .normal import ocr_artifact_output_is_sealed
from .normal_service import (
    NORMAL_OCR_ARTIFACT_KEY,
    NORMAL_OCR_ARTIFACT_VERSION,
    NORMAL_OCR_EVIDENCE_KEY,
    NORMAL_OCR_SUPPORT_THRESHOLD,
    NORMAL_UNCALIBRATED_OCR_QUALITY,
)
from .service import MediaIdentityDecisionService
from .speech_audio import normalize_speech_language
from .speech_service import (
    NORMAL_SPEECH_ARTIFACT_KEY,
    NORMAL_SPEECH_ARTIFACT_VERSION,
    _transcript_output_is_sealed,
)
from .text import synopsis_similarity_from_corpus, text_corpus
from .versions import (
    DEEP_EVIDENCE_PROMOTION_VERSION,
    DEEP_ORCHESTRATION_VERSION,
)


_MAX_PROMOTED_TEXT_CHARS = 512_000
_MAX_MANIFEST_ITEMS = 64


class DeepEvidencePromotionError(RuntimeError):
    """Deep derived artifacts cannot be promoted into resolver evidence safely."""


@dataclass(frozen=True)
class DeepEvidencePromotionRun:
    scan_id: int
    baseline_revision: int
    staged_revision: int
    candidate_plan_signature: str
    correlation_plan_signature: str
    candidate_count: int
    added_candidate_count: int
    visual_manifest_artifact_id: int | None
    speech_manifest_artifact_id: int | None
    visual_evidence_count: int
    speech_evidence_count: int

    @property
    def evidence_count(self) -> int:
        return self.visual_evidence_count + self.speech_evidence_count


@dataclass(frozen=True)
class _VisualCorpus:
    manifest_id: int
    texts: tuple[str, ...]
    confidences: tuple[float | None, ...]
    artifact_ids: tuple[int, ...]
    timestamps_ms: tuple[int, ...]
    plan_signature: str


@dataclass(frozen=True)
class _SpeechCorpus:
    manifest_id: int
    texts: tuple[str, ...]
    languages: tuple[str, ...]
    artifact_ids: tuple[int, ...]
    windows: tuple[tuple[int, int], ...]
    plan_signature: str
    synopsis_language: str


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
        raise DeepEvidencePromotionError(
            "Deep evidence metadata could not be serialized safely."
        ) from exc


def _json_object(value: object) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _same_modified_at(first: object, second: object) -> bool:
    if first is None or second is None:
        return first is None and second is None
    try:
        return float(first) == float(second)
    except (TypeError, ValueError):
        return False


def _valid_sha256(value: object) -> bool:
    text = str(value or "").strip().casefold()
    return (
        len(text) == 64
        and all(character in "0123456789abcdef" for character in text)
    )


def _strict_positive_id(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DeepEvidencePromotionError(
            f"Deep evidence {label} must be a positive integer."
        )
    return value


class DeepEvidencePromotionService:
    """Stage complete J2 Deep artifacts as resolver candidates/evidence.

    This service deliberately leaves completed_profile at its prior Normal value.
    A later J5 orchestration step resolves the staged evidence, runs J4 against the
    new sealed revision, and only then may publish Deep completion.
    """

    def __init__(
        self,
        database: Database,
        *,
        candidate_policy: DeepCandidatePolicy | None = None,
        correlation_policy: DeepCorrelationPolicy | None = None,
    ) -> None:
        self.database = database
        self.candidate_policy = candidate_policy or DeepCandidatePolicy()
        self.correlation_policy = (
            correlation_policy or DeepCorrelationPolicy()
        )

    @staticmethod
    def _file_row(
        conn: sqlite3.Connection,
        file_id: int,
    ) -> dict[str, Any]:
        row = conn.execute(
            """SELECT f.*,t.kind title_kind
               FROM files f
               JOIN titles t ON t.id=f.title_id
               WHERE f.id=?""",
            (int(file_id),),
        ).fetchone()
        if row is None:
            raise DeepEvidencePromotionError(
                "Deep evidence media file was not found."
            )
        return dict(row)

    def _deep_plan(
        self,
        conn: sqlite3.Connection,
        scan: Mapping[str, Any],
        file_row: Mapping[str, Any],
    ):
        claimed = _json_object(scan.get("claimed_identity_json"))
        language = str(
            claimed.get("scan_language") or "eng"
        ).strip().casefold() or "eng"
        candidate_plan = generate_deep_episode_candidates(
            conn,
            title_id=int(file_row["title_id"]),
            season=int(file_row["season"]),
            episode_start=int(file_row["episode_start"]),
            episode_end=int(
                file_row["episode_end"]
                or file_row["episode_start"]
            ),
            language=language,
            policy=self.candidate_policy,
        )
        correlation_plan = plan_deep_correlation(
            conn,
            file_id=int(scan["file_id"]),
            policy=self.correlation_policy,
        )
        return (
            candidate_plan,
            correlation_plan,
            build_deep_plan_metadata(
                candidate_plan,
                correlation_plan,
            ),
        )

    @staticmethod
    def _manifest_row(
        conn: sqlite3.Connection,
        manifest_id: int,
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM media_identity_artifacts WHERE id=?",
            (int(manifest_id),),
        ).fetchone()
        if row is None:
            raise DeepEvidencePromotionError(
                "Deep evidence manifest disappeared before promotion."
            )
        return dict(row)

    def _validate_visual_manifest(
        self,
        conn: sqlite3.Connection,
        *,
        scan: Mapping[str, Any],
        run: DeepSamplingRun,
        deep_identity: Mapping[str, Any],
    ) -> _VisualCorpus:
        if (
            run.scan_id != int(scan["id"])
            or not run.coverage_complete
            or run.budget_exhausted
            or run.failures
            or run.manifest_artifact_id is None
            or run.completed_frame_count != run.planned_frame_count
            or run.planned_frame_count < 1
            or run.planned_frame_count > _MAX_MANIFEST_ITEMS
            or run.candidate_plan_signature
            != str(deep_identity["candidate_plan_signature"])
            or run.correlation_plan_signature
            != str(deep_identity["correlation_plan_signature"])
        ):
            raise DeepEvidencePromotionError(
                "Deep visual evidence requires one complete current J2 manifest."
            )
        manifest_id = _strict_positive_id(
            run.manifest_artifact_id,
            "visual manifest ID",
        )
        row = self._manifest_row(conn, manifest_id)
        payload = _json_object(row.get("payload_json"))
        identity = payload.get("identity")
        if (
            str(row.get("artifact_type") or "")
            != "deep_sampling_manifest"
            or str(row.get("analyzer_key") or "")
            != DEEP_SAMPLING_MANIFEST_KEY
            or str(row.get("analyzer_version") or "")
            != DEEP_SAMPLING_MANIFEST_VERSION
            or str(row.get("status") or "") != "complete"
            or str(row.get("profile") or "") != "deep"
            or str(row.get("source_ref") or "")
            != f"scan:{int(scan['id'])}"
            or str(row.get("source_signature") or "")
            != run.plan_signature
            or int(row.get("file_id") or 0)
            != int(scan["file_id"])
            or int(row.get("file_size_bytes") or 0)
            != int(scan.get("file_size_bytes") or 0)
            or not _same_modified_at(
                row.get("file_modified_at"),
                scan.get("file_modified_at"),
            )
            or not isinstance(identity, Mapping)
            or identity.get("deep_orchestration_version")
            != DEEP_ORCHESTRATION_VERSION
            or identity.get("file_id") != int(scan["file_id"])
            or str(identity.get("file_sha256") or "").casefold()
            != str(scan.get("file_sha256") or "").casefold()
            or str(identity.get("metadata_signature") or "")
            != str(scan.get("metadata_signature") or "")
            or str(identity.get("visual_plan_signature") or "")
            != run.plan_signature
            or str(identity.get("candidate_plan_signature") or "")
            != run.candidate_plan_signature
            or str(identity.get("correlation_plan_signature") or "")
            != run.correlation_plan_signature
            or payload.get("deep_identity") != dict(deep_identity)
            or payload.get("coverage_complete") is not True
        ):
            raise DeepEvidencePromotionError(
                "Deep visual manifest provenance is invalid."
            )
        raw_items = payload.get("observations")
        if (
            not isinstance(raw_items, list)
            or len(raw_items) != run.planned_frame_count
        ):
            raise DeepEvidencePromotionError(
                "Deep visual manifest coverage is incomplete."
            )

        texts: list[str] = []
        confidences: list[float | None] = []
        artifact_ids: list[int] = []
        timestamps: list[int] = []
        seen_ids: set[int] = set()
        total_text_chars = 0
        for item in raw_items:
            if not isinstance(item, Mapping):
                raise DeepEvidencePromotionError(
                    "Deep visual manifest observation is malformed."
                )
            artifact_id = _strict_positive_id(
                item.get("artifact_id"),
                "visual child artifact ID",
            )
            if artifact_id in seen_ids:
                raise DeepEvidencePromotionError(
                    "Deep visual manifest contains a duplicate child."
                )
            seen_ids.add(artifact_id)
            child = self._manifest_row(conn, artifact_id)
            child_payload = _json_object(child.get("payload_json"))
            details = child_payload.get("details")
            if not isinstance(details, Mapping):
                raise DeepEvidencePromotionError(
                    "Deep visual child provenance is incomplete."
                )
            try:
                timestamp_ms = int(item["timestamp_ms"])
                child_start = int(child["start_ms"])
                child_end = int(child["end_ms"])
            except (KeyError, TypeError, ValueError) as exc:
                raise DeepEvidencePromotionError(
                    "Deep visual child timing is malformed."
                ) from exc
            if (
                timestamp_ms < 0
                or child_start != timestamp_ms
                or child_end != timestamp_ms
                or str(child.get("artifact_type") or "") != "visual_text"
                or str(child.get("analyzer_key") or "")
                != NORMAL_OCR_ARTIFACT_KEY
                or str(child.get("analyzer_version") or "")
                != NORMAL_OCR_ARTIFACT_VERSION
                or str(child.get("status") or "") != "complete"
                or str(child.get("profile") or "") not in {"normal", "deep"}
                or int(child.get("file_id") or 0) != int(scan["file_id"])
                or int(child.get("file_size_bytes") or 0)
                != int(scan.get("file_size_bytes") or 0)
                or not _same_modified_at(
                    child.get("file_modified_at"),
                    scan.get("file_modified_at"),
                )
                or str(child.get("cache_key") or "")
                != str(item.get("cache_key") or "")
                or str(details.get("preview_sha256") or "")
                != str(item.get("preview_sha256") or "")
            ):
                raise DeepEvidencePromotionError(
                    "Deep visual child no longer matches its manifest."
                )
            if (
                str(child.get("profile") or "") == "deep"
                and not ocr_artifact_output_is_sealed(
                    child.get("text_value"),
                    child_payload,
                )
            ):
                raise DeepEvidencePromotionError(
                    "Deep visual child output seal is invalid."
                )
            artifact_digest = hashlib.sha256(
                _canonical_json({
                    "text_value": str(
                        child.get("text_value") or ""
                    ),
                    "payload": child_payload,
                }).encode("utf-8")
            ).hexdigest()
            if artifact_digest != str(
                item.get("artifact_digest") or ""
            ):
                raise DeepEvidencePromotionError(
                    "Deep visual child digest changed after J2 publication."
                )

            raw_confidence = child_payload.get("confidence")
            if raw_confidence is None:
                confidence = None
            else:
                try:
                    confidence = float(raw_confidence)
                except (TypeError, ValueError) as exc:
                    raise DeepEvidencePromotionError(
                        "Deep visual confidence is malformed."
                    ) from exc
                if (
                    not math.isfinite(confidence)
                    or not 0.0 <= confidence <= 1.0
                ):
                    raise DeepEvidencePromotionError(
                        "Deep visual confidence is outside the supported range."
                    )
            text = str(child.get("text_value") or "")
            total_text_chars += len(text)
            if total_text_chars > _MAX_PROMOTED_TEXT_CHARS:
                raise DeepEvidencePromotionError(
                    "Deep visual text exceeds the promotion bound."
                )
            artifact_ids.append(artifact_id)
            timestamps.append(timestamp_ms)
            texts.append(text)
            confidences.append(confidence)

        return _VisualCorpus(
            manifest_id=manifest_id,
            texts=tuple(texts),
            confidences=tuple(confidences),
            artifact_ids=tuple(artifact_ids),
            timestamps_ms=tuple(timestamps),
            plan_signature=run.plan_signature,
        )

    def _validate_speech_manifest(
        self,
        conn: sqlite3.Connection,
        *,
        scan: Mapping[str, Any],
        baseline_revision: int,
        run: DeepSpeechSamplingRun,
        deep_identity: Mapping[str, Any],
    ) -> _SpeechCorpus:
        if (
            run.scan_id != int(scan["id"])
            or not run.coverage_complete
            or run.budget_exhausted
            or run.failures
            or run.manifest_artifact_id is None
            or run.transcript_count != run.planned_window_count
            or run.planned_window_count < 1
            or run.planned_window_count > _MAX_MANIFEST_ITEMS
            or run.candidate_plan_signature
            != str(deep_identity["candidate_plan_signature"])
            or run.correlation_plan_signature
            != str(deep_identity["correlation_plan_signature"])
        ):
            raise DeepEvidencePromotionError(
                "Deep speech evidence requires one complete current J2 manifest."
            )
        manifest_id = _strict_positive_id(
            run.manifest_artifact_id,
            "speech manifest ID",
        )
        row = self._manifest_row(conn, manifest_id)
        payload = _json_object(row.get("payload_json"))
        identity = payload.get("identity")
        if (
            str(row.get("artifact_type") or "")
            != "deep_speech_manifest"
            or str(row.get("analyzer_key") or "")
            != DEEP_SPEECH_MANIFEST_KEY
            or str(row.get("analyzer_version") or "")
            != DEEP_SPEECH_MANIFEST_VERSION
            or str(row.get("status") or "") != "complete"
            or str(row.get("profile") or "") != "deep"
            or str(row.get("source_kind") or "") != "local_speech"
            or str(row.get("source_ref") or "")
            != f"scan:{int(scan['id'])}"
            or str(row.get("source_signature") or "")
            != run.plan_signature
            or int(row.get("file_id") or 0)
            != int(scan["file_id"])
            or int(row.get("file_size_bytes") or 0)
            != int(scan.get("file_size_bytes") or 0)
            or not _same_modified_at(
                row.get("file_modified_at"),
                scan.get("file_modified_at"),
            )
            or not isinstance(identity, Mapping)
            or identity.get("deep_orchestration_version")
            != DEEP_ORCHESTRATION_VERSION
            or identity.get("scan_id") != int(scan["id"])
            or identity.get("result_revision") != baseline_revision
            or identity.get("file_id") != int(scan["file_id"])
            or str(identity.get("file_sha256") or "").casefold()
            != str(scan.get("file_sha256") or "").casefold()
            or str(identity.get("metadata_signature") or "")
            != str(scan.get("metadata_signature") or "")
            or str(identity.get("speech_plan_signature") or "")
            != run.plan_signature
            or str(identity.get("candidate_plan_signature") or "")
            != run.candidate_plan_signature
            or str(identity.get("correlation_plan_signature") or "")
            != run.correlation_plan_signature
            or payload.get("deep_identity") != dict(deep_identity)
            or payload.get("coverage_complete") is not True
        ):
            raise DeepEvidencePromotionError(
                "Deep speech manifest provenance is invalid."
            )
        configuration = identity.get("configuration")
        if not isinstance(configuration, Mapping):
            raise DeepEvidencePromotionError(
                "Deep speech manifest configuration is missing."
            )
        synopsis_language = normalize_speech_language(
            str(configuration.get("synopsis_language") or "eng")
        )
        raw_items = payload.get("observations")
        if (
            not isinstance(raw_items, list)
            or len(raw_items) != run.planned_window_count
        ):
            raise DeepEvidencePromotionError(
                "Deep speech manifest coverage is incomplete."
            )

        texts: list[str] = []
        languages: list[str] = []
        artifact_ids: list[int] = []
        windows: list[tuple[int, int]] = []
        seen_ids: set[int] = set()
        total_text_chars = 0
        for item in raw_items:
            if not isinstance(item, Mapping):
                raise DeepEvidencePromotionError(
                    "Deep speech manifest observation is malformed."
                )
            artifact_id = _strict_positive_id(
                item.get("artifact_id"),
                "speech child artifact ID",
            )
            if artifact_id in seen_ids:
                raise DeepEvidencePromotionError(
                    "Deep speech manifest contains a duplicate child."
                )
            seen_ids.add(artifact_id)
            child = self._manifest_row(conn, artifact_id)
            child_payload = _json_object(child.get("payload_json"))
            transcript = child_payload.get("transcript")
            audio = child_payload.get("audio_identity")
            if (
                not isinstance(transcript, Mapping)
                or not isinstance(audio, Mapping)
                or not _transcript_output_is_sealed(
                    child.get("text_value"),
                    child_payload,
                )
            ):
                raise DeepEvidencePromotionError(
                    "Deep speech child output seal is invalid."
                )
            try:
                start_ms = int(item["start_ms"])
                end_ms = int(item["end_ms"])
                child_start = int(child["start_ms"])
                child_end = int(child["end_ms"])
            except (KeyError, TypeError, ValueError) as exc:
                raise DeepEvidencePromotionError(
                    "Deep speech child timing is malformed."
                ) from exc
            if (
                start_ms < 0
                or end_ms <= start_ms
                or child_start != start_ms
                or child_end != end_ms
                or str(child.get("artifact_type") or "")
                != "speech_transcript"
                or str(child.get("analyzer_key") or "")
                != NORMAL_SPEECH_ARTIFACT_KEY
                or str(child.get("analyzer_version") or "")
                != NORMAL_SPEECH_ARTIFACT_VERSION
                or str(child.get("status") or "") != "complete"
                or str(child.get("profile") or "") not in {"normal", "deep"}
                or int(child.get("file_id") or 0) != int(scan["file_id"])
                or int(child.get("file_size_bytes") or 0)
                != int(scan.get("file_size_bytes") or 0)
                or not _same_modified_at(
                    child.get("file_modified_at"),
                    scan.get("file_modified_at"),
                )
                or str(child.get("cache_key") or "")
                != str(item.get("cache_key") or "")
                or str(
                    child_payload.get("transcript_output_sha256")
                    or ""
                ) != str(item.get("transcript_output_sha256") or "")
                or str(audio.get("sha256") or "")
                != str(item.get("audio_sha256") or "")
            ):
                raise DeepEvidencePromotionError(
                    "Deep speech child no longer matches its manifest."
                )
            text = str(child.get("text_value") or "")
            total_text_chars += len(text)
            if total_text_chars > _MAX_PROMOTED_TEXT_CHARS:
                raise DeepEvidencePromotionError(
                    "Deep speech text exceeds the promotion bound."
                )
            language = normalize_speech_language(
                str(transcript.get("language") or "")
            )
            texts.append(text)
            languages.append(language)
            artifact_ids.append(artifact_id)
            windows.append((start_ms, end_ms))

        return _SpeechCorpus(
            manifest_id=manifest_id,
            texts=tuple(texts),
            languages=tuple(languages),
            artifact_ids=tuple(artifact_ids),
            windows=tuple(windows),
            plan_signature=run.plan_signature,
            synopsis_language=synopsis_language,
        )

    @staticmethod
    def _upsert_candidates(
        conn: sqlite3.Connection,
        scan_id: int,
        candidate_plan,
    ) -> tuple[int, tuple[str, ...]]:
        existing = {
            str(row["candidate_key"])
            for row in conn.execute(
                """SELECT candidate_key
                   FROM media_identity_candidates
                   WHERE scan_id=?""",
                (int(scan_id),),
            ).fetchall()
        }
        added = 0
        added_keys: list[str] = []
        for candidate in candidate_plan.candidates:
            identity = candidate.identity
            if candidate.key not in existing:
                added += 1
                added_keys.append(candidate.key)
            conn.execute(
                """INSERT INTO media_identity_candidates(
                     scan_id,candidate_key,identity_kind,provider,
                     provider_item_id,expected_episode_id,order_namespace,
                     season,episode,display_name,rank,score,
                     support_strength,conflict_strength,
                     independent_categories,details_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,0,0,0,?)
                   ON CONFLICT(scan_id,candidate_key) DO UPDATE SET
                     identity_kind=excluded.identity_kind,
                     provider=excluded.provider,
                     provider_item_id=excluded.provider_item_id,
                     expected_episode_id=excluded.expected_episode_id,
                     order_namespace=excluded.order_namespace,
                     season=excluded.season,
                     episode=excluded.episode,
                     display_name=excluded.display_name,
                     rank=excluded.rank,
                     score=0,
                     support_strength=0,
                     conflict_strength=0,
                     independent_categories=0,
                     details_json=excluded.details_json""",
                (
                    int(scan_id),
                    candidate.key,
                    identity.identity_kind,
                    identity.provider,
                    identity.provider_item_id,
                    identity.expected_episode_id,
                    identity.order_namespace,
                    identity.season,
                    identity.episode,
                    identity.display_name,
                    candidate.rank,
                    _canonical_json(dict(candidate.details)),
                ),
            )
        return added, tuple(added_keys)

    @staticmethod
    def _candidate_rows(
        conn: sqlite3.Connection,
        scan_id: int,
    ) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in conn.execute(
                """SELECT *
                   FROM media_identity_candidates
                   WHERE scan_id=?
                   ORDER BY rank,candidate_key""",
                (int(scan_id),),
            ).fetchall()
        ]

    @staticmethod
    def _visual_correlation_group(
        conn: sqlite3.Connection,
        scan_id: int,
        file_id: int,
    ) -> str:
        groups = {
            str(row["correlation_group"] or "")
            for row in conn.execute(
                """SELECT correlation_group
                   FROM media_identity_evidence
                   WHERE scan_id=? AND analyzer_key=?""",
                (int(scan_id), NORMAL_OCR_EVIDENCE_KEY),
            ).fetchall()
            if str(row["correlation_group"] or "")
        }
        if len(groups) > 1:
            raise DeepEvidencePromotionError(
                "Normal visual evidence has inconsistent correlation groups."
            )
        return (
            next(iter(groups))
            if groups
            else f"visual-text:{int(file_id)}:deep"
        )

    @staticmethod
    def _insert_evidence(
        conn: sqlite3.Connection,
        rows: list[tuple[Any, ...]],
    ) -> int:
        if not rows:
            return 0
        conn.executemany(
            """INSERT INTO media_identity_evidence(
                 scan_id,candidate_key,analyzer_key,analyzer_version,
                 evidence_category,correlation_group,relation,strength,
                 source_kind,source_ref,timestamp_ms,value_text,details_json,
                 cache_key,profile
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        return len(rows)

    def _persist_visual_evidence(
        self,
        conn: sqlite3.Connection,
        *,
        scan: Mapping[str, Any],
        candidates: list[dict[str, Any]],
        corpus: _VisualCorpus,
    ) -> int:
        conn.execute(
            """DELETE FROM media_identity_evidence
               WHERE scan_id=? AND analyzer_key=?""",
            (int(scan["id"]), DEEP_VISUAL_EVIDENCE_KEY),
        )
        group = self._visual_correlation_group(
            conn,
            int(scan["id"]),
            int(scan["file_id"]),
        )
        usable = [
            (text, confidence, timestamp)
            for text, confidence, timestamp in zip(
                corpus.texts,
                corpus.confidences,
                corpus.timestamps_ms,
            )
            if text.strip()
        ]
        rows: list[tuple[Any, ...]] = []
        cache_key = hashlib.sha256(
            _canonical_json({
                "version": DEEP_EVIDENCE_PROMOTION_VERSION,
                "manifest_id": corpus.manifest_id,
                "plan_signature": corpus.plan_signature,
                "artifact_ids": list(corpus.artifact_ids),
            }).encode("utf-8")
        ).hexdigest()
        if usable:
            combined = "\n".join(item[0] for item in usable)
            text_features = text_corpus(combined)
            qualities = [
                (
                    float(confidence)
                    if confidence is not None
                    else NORMAL_UNCALIBRATED_OCR_QUALITY
                )
                for _, confidence, _ in usable
            ]
            quality = sum(qualities) / len(qualities)
            comparable = 0
            for candidate in candidates:
                details = _json_object(candidate.get("details_json"))
                overview = str(details.get("overview") or "").strip()
                if not overview:
                    continue
                comparable += 1
                similarity = synopsis_similarity_from_corpus(
                    text_features,
                    overview,
                )
                relation = (
                    EvidenceRelation.SUPPORTS.value
                    if similarity >= NORMAL_OCR_SUPPORT_THRESHOLD
                    else EvidenceRelation.NEUTRAL.value
                )
                strength = (
                    round(
                        max(
                            0.0,
                            min(1.0, similarity * quality),
                        ),
                        6,
                    )
                    if relation == EvidenceRelation.SUPPORTS.value
                    else 0.0
                )
                rows.append((
                    int(scan["id"]),
                    str(candidate["candidate_key"]),
                    DEEP_VISUAL_EVIDENCE_KEY,
                    DEEP_EVIDENCE_VERSION,
                    EvidenceCategory.VISUAL_TEXT.value,
                    group,
                    relation,
                    strength,
                    "deep_visual_text",
                    f"manifest:{corpus.manifest_id}",
                    None,
                    f"deep OCR synopsis similarity {similarity:.3f}",
                    _canonical_json({
                        "similarity": similarity,
                        "support_threshold": (
                            NORMAL_OCR_SUPPORT_THRESHOLD
                        ),
                        "ocr_quality": round(quality, 6),
                        "artifact_ids": list(corpus.artifact_ids),
                        "timestamps_ms": [
                            item[2] for item in usable
                        ],
                        "manifest_artifact_id": corpus.manifest_id,
                        "text_excerpt": combined[:4000],
                        "correlated_with": [NORMAL_OCR_EVIDENCE_KEY],
                    }),
                    cache_key,
                    IdentityProfile.DEEP.value,
                ))
            if comparable == 0:
                rows.append((
                    int(scan["id"]),
                    "",
                    DEEP_VISUAL_EVIDENCE_KEY,
                    DEEP_EVIDENCE_VERSION,
                    EvidenceCategory.VISUAL_TEXT.value,
                    group,
                    EvidenceRelation.NEUTRAL.value,
                    0.0,
                    "deep_visual_text",
                    f"manifest:{corpus.manifest_id}",
                    None,
                    "Deep OCR text was available, but candidate synopses were unavailable.",
                    _canonical_json({
                        "artifact_ids": list(corpus.artifact_ids),
                        "manifest_artifact_id": corpus.manifest_id,
                        "correlated_with": [NORMAL_OCR_EVIDENCE_KEY],
                    }),
                    cache_key,
                    IdentityProfile.DEEP.value,
                ))
        else:
            rows.append((
                int(scan["id"]),
                "",
                DEEP_VISUAL_EVIDENCE_KEY,
                DEEP_EVIDENCE_VERSION,
                EvidenceCategory.VISUAL_TEXT.value,
                group,
                EvidenceRelation.NEUTRAL.value,
                0.0,
                "deep_visual_text",
                f"manifest:{corpus.manifest_id}",
                None,
                "Complete Deep OCR coverage produced no usable text.",
                _canonical_json({
                    "artifact_ids": list(corpus.artifact_ids),
                    "manifest_artifact_id": corpus.manifest_id,
                    "correlated_with": [NORMAL_OCR_EVIDENCE_KEY],
                }),
                cache_key,
                IdentityProfile.DEEP.value,
            ))
        return self._insert_evidence(conn, rows)

    def _persist_speech_evidence(
        self,
        conn: sqlite3.Connection,
        *,
        scan: Mapping[str, Any],
        candidates: list[dict[str, Any]],
        corpus: _SpeechCorpus,
    ) -> int:
        conn.execute(
            """DELETE FROM media_identity_evidence
               WHERE scan_id=? AND analyzer_key=?""",
            (int(scan["id"]), DEEP_SPEECH_EVIDENCE_KEY),
        )
        group = f"subtitle-dialogue:{int(scan['file_id'])}"
        aligned = [
            (text, language, window, artifact_id)
            for text, language, window, artifact_id in zip(
                corpus.texts,
                corpus.languages,
                corpus.windows,
                corpus.artifact_ids,
            )
            if (
                text.strip()
                and normalize_speech_language(language)
                == corpus.synopsis_language
            )
        ]
        mismatched = [
            language
            for text, language in zip(
                corpus.texts,
                corpus.languages,
            )
            if (
                text.strip()
                and normalize_speech_language(language)
                != corpus.synopsis_language
            )
        ]
        rows: list[tuple[Any, ...]] = []
        cache_key = hashlib.sha256(
            _canonical_json({
                "version": DEEP_EVIDENCE_PROMOTION_VERSION,
                "manifest_id": corpus.manifest_id,
                "plan_signature": corpus.plan_signature,
                "artifact_ids": list(corpus.artifact_ids),
                "synopsis_language": corpus.synopsis_language,
            }).encode("utf-8")
        ).hexdigest()
        if aligned:
            combined = "\n".join(item[0] for item in aligned)
            features = text_corpus(combined)
            comparable = 0
            for candidate in candidates:
                details = _json_object(candidate.get("details_json"))
                overview = str(details.get("overview") or "").strip()
                if not overview:
                    continue
                comparable += 1
                similarity = synopsis_similarity_from_corpus(
                    features,
                    overview,
                )
                relation = (
                    EvidenceRelation.SUPPORTS.value
                    if similarity >= TEXT_SUPPORT_THRESHOLD
                    else EvidenceRelation.NEUTRAL.value
                )
                rows.append((
                    int(scan["id"]),
                    str(candidate["candidate_key"]),
                    DEEP_SPEECH_EVIDENCE_KEY,
                    DEEP_EVIDENCE_VERSION,
                    EvidenceCategory.SPEECH.value,
                    group,
                    relation,
                    (
                        similarity
                        if relation == EvidenceRelation.SUPPORTS.value
                        else 0.0
                    ),
                    "deep_speech_transcript",
                    f"manifest:{corpus.manifest_id}",
                    None,
                    f"deep speech synopsis similarity {similarity:.3f}",
                    _canonical_json({
                        "similarity": similarity,
                        "support_threshold": TEXT_SUPPORT_THRESHOLD,
                        "artifact_ids": [
                            item[3] for item in aligned
                        ],
                        "windows": [
                            {
                                "artifact_id": item[3],
                                "start_ms": item[2][0],
                                "end_ms": item[2][1],
                            }
                            for item in aligned
                        ],
                        "manifest_artifact_id": corpus.manifest_id,
                        "transcript_excerpt": combined[:1200],
                        "synopsis_language": corpus.synopsis_language,
                        "transcript_languages": sorted({
                            item[1] for item in aligned
                        }),
                        "correlated_with": [
                            "subtitle-synopsis",
                            "speech-synopsis",
                        ],
                    }),
                    cache_key,
                    IdentityProfile.DEEP.value,
                ))
            if comparable == 0:
                rows.append((
                    int(scan["id"]),
                    "",
                    DEEP_SPEECH_EVIDENCE_KEY,
                    DEEP_EVIDENCE_VERSION,
                    EvidenceCategory.SPEECH.value,
                    group,
                    EvidenceRelation.NEUTRAL.value,
                    0.0,
                    "deep_speech_transcript",
                    f"manifest:{corpus.manifest_id}",
                    None,
                    "Deep speech was available, but candidate synopses were unavailable.",
                    _canonical_json({
                        "artifact_ids": [
                            item[3] for item in aligned
                        ],
                        "manifest_artifact_id": corpus.manifest_id,
                        "synopsis_language": corpus.synopsis_language,
                    }),
                    cache_key,
                    IdentityProfile.DEEP.value,
                ))
        elif mismatched:
            rows.append((
                int(scan["id"]),
                "",
                DEEP_SPEECH_EVIDENCE_KEY,
                DEEP_EVIDENCE_VERSION,
                EvidenceCategory.SPEECH.value,
                group,
                EvidenceRelation.NEUTRAL.value,
                0.0,
                "deep_speech_transcript",
                f"manifest:{corpus.manifest_id}",
                None,
                "Deep speech language did not align with the candidate synopsis language.",
                _canonical_json({
                    "artifact_ids": list(corpus.artifact_ids),
                    "manifest_artifact_id": corpus.manifest_id,
                    "synopsis_language": corpus.synopsis_language,
                    "transcript_languages": sorted(set(mismatched)),
                }),
                cache_key,
                IdentityProfile.DEEP.value,
            ))
        else:
            rows.append((
                int(scan["id"]),
                "",
                DEEP_SPEECH_EVIDENCE_KEY,
                DEEP_EVIDENCE_VERSION,
                EvidenceCategory.SPEECH.value,
                group,
                EvidenceRelation.NEUTRAL.value,
                0.0,
                "deep_speech_transcript",
                f"manifest:{corpus.manifest_id}",
                None,
                "Complete Deep speech coverage produced no usable transcript text.",
                _canonical_json({
                    "artifact_ids": list(corpus.artifact_ids),
                    "manifest_artifact_id": corpus.manifest_id,
                    "synopsis_language": corpus.synopsis_language,
                }),
                cache_key,
                IdentityProfile.DEEP.value,
            ))
        return self._insert_evidence(conn, rows)

    def promote(
        self,
        scan_id: int,
        *,
        visual: DeepSamplingRun | None = None,
        speech: DeepSpeechSamplingRun | None = None,
    ) -> DeepEvidencePromotionRun:
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            scan, _, evidence = MediaIdentityDecisionService._scan_snapshot(
                conn,
                int(scan_id),
            )
            if (
                scan.get("status") != "complete"
                or str(scan.get("completed_profile") or "")
                != IdentityProfile.NORMAL.value
            ):
                raise DeepEvidencePromotionError(
                    "Deep evidence promotion requires a complete current Normal scan."
                )
            current, file_row = (
                MediaIdentityDecisionService._scan_snapshot_is_current(
                    conn,
                    scan,
                    evidence,
                )
            )
            if not current or file_row is None:
                raise DeepEvidencePromotionError(
                    "The Normal scan became stale before Deep evidence promotion."
                )
            baseline_revision = result_revision(scan)
            if baseline_revision < 1:
                raise DeepEvidencePromotionError(
                    "Deep evidence promotion requires a sealed baseline revision."
                )
            full_file = self._file_row(
                conn,
                int(scan["file_id"]),
            )
            candidate_plan, _correlation_plan, deep_identity = (
                self._deep_plan(
                    conn,
                    scan,
                    full_file,
                )
            )

            visual_corpus = (
                None
                if visual is None or not visual.coverage_complete
                else self._validate_visual_manifest(
                    conn,
                    scan=scan,
                    run=visual,
                    deep_identity=deep_identity,
                )
            )
            speech_corpus = (
                None
                if speech is None or not speech.coverage_complete
                else self._validate_speech_manifest(
                    conn,
                    scan=scan,
                    baseline_revision=baseline_revision,
                    run=speech,
                    deep_identity=deep_identity,
                )
            )
            # A Deep run may legitimately have no usable OCR/speech runtime.
            # Candidate widening and later fingerprint/correlation work remain
            # valid, so zero promoted text modalities is not an error.

            claimed = _json_object(
                scan.get("claimed_identity_json")
            )
            previous_deep = claimed.get("deep_evidence")
            previous_added_keys: set[str] = set()
            if isinstance(previous_deep, Mapping):
                raw_previous_added = previous_deep.get(
                    "added_candidate_keys"
                )
                if isinstance(raw_previous_added, list):
                    for value in raw_previous_added:
                        if isinstance(value, str) and value:
                            previous_added_keys.add(value)

            current_plan_keys = {
                candidate.key
                for candidate in candidate_plan.candidates
            }
            existing_candidate_keys = {
                str(row["candidate_key"])
                for row in conn.execute(
                    """SELECT candidate_key
                       FROM media_identity_candidates
                       WHERE scan_id=?""",
                    (int(scan_id),),
                ).fetchall()
            }
            baseline_candidate_keys = (
                existing_candidate_keys - previous_added_keys
            )
            missing_baseline_keys = sorted(
                baseline_candidate_keys - current_plan_keys
            )
            if missing_baseline_keys:
                raise DeepEvidencePromotionError(
                    "Deep candidate widening would narrow the persisted "
                    "Normal/Fast baseline candidate set."
                )

            obsolete_added_keys = sorted(
                previous_added_keys - current_plan_keys
            )
            if obsolete_added_keys:
                placeholders = ",".join(
                    "?" for _ in obsolete_added_keys
                )
                conn.execute(
                    f"""DELETE FROM media_identity_evidence
                        WHERE scan_id=? AND candidate_key IN ({placeholders})""",
                    (int(scan_id), *obsolete_added_keys),
                )
                conn.execute(
                    f"""DELETE FROM media_identity_candidates
                        WHERE scan_id=? AND candidate_key IN ({placeholders})""",
                    (int(scan_id), *obsolete_added_keys),
                )

            # Every promotion replaces the complete Deep text contribution.
            # This prevents a previously-complete modality from leaking into a
            # later run where that modality is now incomplete or unavailable.
            conn.execute(
                """DELETE FROM media_identity_evidence
                   WHERE scan_id=? AND analyzer_key IN (?,?)""",
                (
                    int(scan_id),
                    DEEP_VISUAL_EVIDENCE_KEY,
                    DEEP_SPEECH_EVIDENCE_KEY,
                ),
            )

            added_candidates, newly_added_keys = self._upsert_candidates(
                conn,
                int(scan_id),
                candidate_plan,
            )
            active_deep_added_keys = tuple(sorted(
                (
                    previous_added_keys & current_plan_keys
                )
                | set(newly_added_keys)
            ))
            candidates = self._candidate_rows(
                conn,
                int(scan_id),
            )
            visual_evidence_count = (
                0
                if visual_corpus is None
                else self._persist_visual_evidence(
                    conn,
                    scan=scan,
                    candidates=candidates,
                    corpus=visual_corpus,
                )
            )
            speech_evidence_count = (
                0
                if speech_corpus is None
                else self._persist_speech_evidence(
                    conn,
                    scan=scan,
                    candidates=candidates,
                    corpus=speech_corpus,
                )
            )

            claimed["deep_identity"] = dict(deep_identity)
            claimed["deep_evidence"] = {
                "version": DEEP_EVIDENCE_PROMOTION_VERSION,
                "baseline_revision": baseline_revision,
                "visual_manifest_artifact_id": (
                    None
                    if visual_corpus is None
                    else visual_corpus.manifest_id
                ),
                "speech_manifest_artifact_id": (
                    None
                    if speech_corpus is None
                    else speech_corpus.manifest_id
                ),
                "visual_evidence_count": visual_evidence_count,
                "speech_evidence_count": speech_evidence_count,
                "added_candidate_keys": list(active_deep_added_keys),
            }
            conn.execute(
                """UPDATE media_identity_scans
                   SET requested_profile='deep',
                       stage='deep_evidence_staged',
                       claimed_identity_json=?,
                       result_state=NULL,
                       best_candidate_key=NULL,
                       completed_at=CURRENT_TIMESTAMP,
                       error=''
                   WHERE id=?""",
                (
                    _canonical_json(claimed),
                    int(scan_id),
                ),
            )
            seal_decision_snapshot(
                conn,
                int(scan_id),
                revision=baseline_revision + 1,
            )

        return DeepEvidencePromotionRun(
            scan_id=int(scan_id),
            baseline_revision=baseline_revision,
            staged_revision=baseline_revision + 1,
            candidate_plan_signature=str(
                deep_identity["candidate_plan_signature"]
            ),
            correlation_plan_signature=str(
                deep_identity["correlation_plan_signature"]
            ),
            candidate_count=len(candidate_plan.candidates),
            added_candidate_count=added_candidates,
            visual_manifest_artifact_id=(
                None
                if visual_corpus is None
                else visual_corpus.manifest_id
            ),
            speech_manifest_artifact_id=(
                None
                if speech_corpus is None
                else speech_corpus.manifest_id
            ),
            visual_evidence_count=visual_evidence_count,
            speech_evidence_count=speech_evidence_count,
        )

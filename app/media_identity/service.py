from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Mapping

from ..db import Database
from ..naming import contained_destination, plex_episode_filename
from .candidates import generate_episode_candidates
from .decision_snapshot import (
    DECISION_SNAPSHOT_VERSION,
    decision_snapshot_matches,
    result_revision,
    seal_decision_snapshot,
)
from .external import ExternalAnalysisError, ExternalSourceRegistry, PreviewFrameRef
from .external_config import external_source_config_signature
from .fast import (
    SCAN_INPUT_SIGNATURE_VERSION,
    TEXT_SUPPORT_THRESHOLD,
    combined_scan_input_signature,
    scan_input_signatures,
)
from .media_generation import media_content_sha256
from .models import IdentityProfile, IdentityResultState
from .scoring import IdentityResolution, resolve_identity
from .speech_audio import normalize_speech_language
from .text import (
    TextCorpus,
    discover_sidecar_subtitles,
    sidecar_identity,
    synopsis_similarity_from_corpus,
    text_corpus,
)
from .visual_budget import VisualAttemptBudget, visual_budget_scope
from .versions import (
    EPISODE_IDENTITY_DECISION_ALGORITHM_VERSION,
    NORMAL_EVIDENCE_ALGORITHM_VERSION,
    NORMAL_SPEECH_EVIDENCE_ALGORITHM_VERSION,
    NORMAL_SPEECH_ORCHESTRATION_VERSION,
)


SUGGESTED_CONFIRM_STATES = {
    IdentityResultState.POSSIBLE_MISMATCH.value,
    IdentityResultState.LIKELY_MISMATCH.value,
    IdentityResultState.STRONG_MATCH_OTHER.value,
}

ACTIONABLE_STATES = SUGGESTED_CONFIRM_STATES | {
    IdentityResultState.EPISODE_ORDER_CONFLICT.value,
}

_ACTION_EXTERNAL_PREVIEW_MAX_FRAMES = 12
_ACTION_EXTERNAL_PREVIEW_MAX_SOURCE_BYTES = 48 * 1024 * 1024
_MIE_NORMAL_HISTORY_VALIDATION_LIMIT = 8


def _same_modified_at(first: Any, second: Any) -> bool:
    if first is None or second is None:
        return first is None and second is None
    return float(first) == float(second)


def _json_object(value: Any) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _strength_label(value: Any) -> str:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return "No meaningful support"
    if score >= 0.75:
        return "Strong"
    if score >= 0.55:
        return "Moderate"
    if score >= 0.30:
        return "Limited"
    return "Weak or none"


def _separation_label(value: Any) -> str:
    try:
        margin = float(value)
    except (TypeError, ValueError):
        return "Not established"
    if margin >= 0.24:
        return "Clear"
    if margin >= 0.14:
        return "Meaningful"
    if margin >= 0.10:
        return "Narrow"
    return "Too close to call"


def _candidate_view(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["details"] = _json_object(result.pop("details_json", "{}"))
    return result


def _evidence_view(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["details"] = _json_object(result.pop("details_json", "{}"))
    return result


class MediaIdentityDecisionError(ValueError):
    pass


class MediaIdentityDecisionService:
    """Resolve persisted evidence and own explicit human confirmation state."""

    def __init__(
        self,
        database: Database,
        *,
        external_registry_factory: Callable[[], ExternalSourceRegistry] | None = None,
    ) -> None:
        self.database = database
        self.external_registry_factory = external_registry_factory

    @staticmethod
    def _scan_snapshot(
        conn: sqlite3.Connection,
        scan_id: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        scan_row = conn.execute(
            "SELECT * FROM media_identity_scans WHERE id=?", (int(scan_id),)
        ).fetchone()
        if not scan_row:
            raise MediaIdentityDecisionError("Episode Identity scan was not found.")
        scan = dict(scan_row)
        candidates = [
            _candidate_view(row)
            for row in conn.execute(
                """SELECT * FROM media_identity_candidates
                   WHERE scan_id=? ORDER BY rank,candidate_key""",
                (int(scan_id),),
            ).fetchall()
        ]
        evidence = [
            _evidence_view(row)
            for row in conn.execute(
                """SELECT * FROM media_identity_evidence
                   WHERE scan_id=? ORDER BY id""",
                (int(scan_id),),
            ).fetchall()
        ]
        return scan, candidates, evidence

    @staticmethod
    def _claimed_identity(scan: Mapping[str, Any]) -> dict[str, Any]:
        return _json_object(scan.get("claimed_identity_json"))

    @staticmethod
    def _resolve_snapshot(
        scan: Mapping[str, Any],
        candidates: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> IdentityResolution:
        return resolve_identity(
            candidates,
            evidence,
            MediaIdentityDecisionService._claimed_identity(scan),
        )

    def resolve_scan(self, scan_id: int) -> IdentityResolution:
        with self.database.connect() as conn:
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            scan, candidates, evidence = self._scan_snapshot(conn, int(scan_id))
            if scan["status"] != "complete":
                raise MediaIdentityDecisionError(
                    "Only a complete Episode Identity scan can be resolved."
                )
            current, _ = self._scan_snapshot_is_current(conn, scan, evidence)
            if not current:
                raise MediaIdentityDecisionError(
                    "The Episode Identity result changed after publication. "
                    "Run verification again before resolving it."
                )
            revision = result_revision(scan)
            resolution = self._resolve_snapshot(scan, candidates, evidence)
            if scan.get("result_state") is not None:
                if (
                    str(scan.get("result_state") or "") != resolution.state.value
                    or str(scan.get("best_candidate_key") or "")
                    != str(resolution.best_candidate_key or "")
                ):
                    raise MediaIdentityDecisionError(
                        "The sealed Episode Identity resolution does not match "
                        "the current decision algorithm. Run verification again."
                    )
                return resolution
            by_key = {
                item.candidate_key: item for item in resolution.candidates
            }
            for candidate in candidates:
                key = str(candidate["candidate_key"])
                resolved = by_key.get(key)
                if resolved is None:
                    continue
                details = dict(candidate.get("details") or {})
                details["resolution"] = {
                    **dict(resolved.details),
                    "score": resolved.score,
                    "support_strength": resolved.support_strength,
                    "conflict_strength": resolved.conflict_strength,
                    "independent_categories": resolved.independent_categories,
                    "margin_from_runner_up": (
                        resolution.margin
                        if key == resolution.best_candidate_key
                        else None
                    ),
                }
                conn.execute(
                    """UPDATE media_identity_candidates
                       SET score=?,support_strength=?,conflict_strength=?,
                           independent_categories=?,details_json=?
                       WHERE scan_id=? AND candidate_key=?""",
                    (
                        resolved.score,
                        resolved.support_strength,
                        resolved.conflict_strength,
                        resolved.independent_categories,
                        json.dumps(
                            details,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            default=str,
                        ),
                        int(scan_id),
                        key,
                    ),
                )
            conn.execute(
                """UPDATE media_identity_scans
                   SET result_state=?,best_candidate_key=?,stage='resolved'
                   WHERE id=?""",
                (
                    resolution.state.value,
                    resolution.best_candidate_key,
                    int(scan_id),
                ),
            )
            seal_decision_snapshot(
                conn,
                int(scan_id),
                revision=revision + 1,
            )
        return resolution

    def resolve_pending(self, limit: int = 250) -> int:
        limit = max(1, min(int(limit), 1000))
        with self.database.connect() as conn:
            ids = [
                int(row["id"])
                for row in conn.execute(
                    """SELECT id FROM media_identity_scans
                       WHERE status='complete' AND result_state IS NULL
                       ORDER BY id LIMIT ?""",
                    (limit,),
                ).fetchall()
            ]
        resolved = 0
        for scan_id in ids:
            try:
                self.resolve_scan(scan_id)
            except MediaIdentityDecisionError:
                continue
            resolved += 1
        return resolved

    @staticmethod
    def _current_hash(
        conn: sqlite3.Connection,
        file_id: int,
        size_bytes: int,
        modified_at: Any,
    ) -> str | None:
        row = conn.execute(
            """SELECT sha256,size_bytes,modified_at,status
               FROM media_file_hashes WHERE file_id=?""",
            (int(file_id),),
        ).fetchone()
        if (
            not row
            or row["status"] != "complete"
            or not row["sha256"]
            or int(row["size_bytes"] or 0) != int(size_bytes)
            or not _same_modified_at(row["modified_at"], modified_at)
        ):
            return None
        return str(row["sha256"])

    @staticmethod
    def _snapshot_is_current(
        conn: sqlite3.Connection,
        file_id: int,
        *,
        size_bytes: int,
        modified_at: Any,
        sha256: str | None = None,
        verify_content: bool = False,
    ) -> tuple[bool, dict[str, Any] | None]:
        row = conn.execute(
            """SELECT id,title_id,path,filename,size_bytes,modified_at,extension,
                      season,episode_start,episode_end
               FROM files WHERE id=?""",
            (int(file_id),),
        ).fetchone()
        if not row:
            return False, None
        current = dict(row)
        if int(current["size_bytes"] or 0) != int(size_bytes):
            return False, current
        if not _same_modified_at(current["modified_at"], modified_at):
            return False, current
        path = Path(str(current["path"]))
        try:
            stat_result = path.stat()
        except OSError:
            return False, current
        if not path.is_file():
            return False, current
        if int(stat_result.st_size) != int(size_bytes):
            return False, current
        if modified_at is not None and not _same_modified_at(
            stat_result.st_mtime, modified_at
        ):
            return False, current
        if sha256 and verify_content:
            current_hash = media_content_sha256(path)
            if current_hash != str(sha256):
                return False, current
        return True, current

    @staticmethod
    def _scan_snapshot_is_current(
        conn: sqlite3.Connection,
        scan: Mapping[str, Any],
        evidence: list[dict[str, Any]],
        *,
        verify_content: bool = False,
    ) -> tuple[bool, dict[str, Any] | None]:
        current, file_row = MediaIdentityDecisionService._snapshot_is_current(
            conn,
            int(scan["file_id"]),
            size_bytes=int(scan["file_size_bytes"] or 0),
            modified_at=scan["file_modified_at"],
            sha256=scan["file_sha256"],
            verify_content=verify_content,
        )
        if not current or file_row is None:
            return False, file_row

        claimed = MediaIdentityDecisionService._claimed_identity(scan)
        try:
            expected_season = int(claimed["season"])
            expected_start = int(claimed["episode_start"])
            expected_end = int(claimed.get("episode_end") or expected_start)
            current_season = int(file_row["season"])
            current_start = int(file_row["episode_start"])
            current_end = int(file_row["episode_end"] or current_start)
        except (KeyError, TypeError, ValueError):
            return False, file_row

        expected_filename = str(claimed.get("filename") or "")
        if (
            not expected_filename
            or str(file_row["filename"] or "") != expected_filename
            or (current_season, current_start, current_end)
            != (expected_season, expected_start, expected_end)
        ):
            return False, file_row

        # Pre-repair scans do not contain enough information to prove that provider
        # metadata, title identity, technical metadata, streams, and the complete
        # subtitle selection are still the same. They remain reviewable but are
        # deliberately non-actionable until a new verification creates v2 inputs.
        try:
            signature_version = int(claimed.get("input_signature_version") or 0)
        except (TypeError, ValueError):
            return False, file_row
        expected_signatures = claimed.get("input_signatures")
        if (
            signature_version != SCAN_INPUT_SIGNATURE_VERSION
            or not isinstance(expected_signatures, dict)
            or not expected_signatures
        ):
            return False, file_row

        full_row = conn.execute(
            """SELECT f.*,t.kind AS title_kind,t.tvdb_id,
                      COALESCE(t.metadata_title,t.title) AS title_name
               FROM files f
               JOIN titles t ON t.id=f.title_id
               WHERE f.id=?""",
            (int(scan["file_id"]),),
        ).fetchone()
        if not full_row:
            return False, file_row
        full_file = dict(full_row)
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

        language = str(claimed.get("scan_language") or "eng").strip().casefold() or "eng"
        expanded_specials = bool(claimed.get("expanded_specials"))
        try:
            candidate_set = generate_episode_candidates(
                conn,
                title_id=int(full_file["title_id"]),
                season=int(full_file["season"]),
                episode_start=int(full_file["episode_start"]),
                episode_end=int(
                    full_file["episode_end"] or full_file["episode_start"]
                ),
                include_specials=(
                    int(full_file["season"]) == 0 or expanded_specials
                ),
                language=language,
            )
        except (TypeError, ValueError, sqlite3.Error):
            return False, file_row

        sidecar_identities = []
        for path in discover_sidecar_subtitles(str(full_file["path"])):
            identity = sidecar_identity(path)
            if identity is None:
                return False, file_row
            sidecar_identities.append(identity)

        current_signatures = scan_input_signatures(
            full_file,
            streams,
            candidate_set,
            sidecar_identities,
            language=language,
            expanded_specials=expanded_specials,
        )
        try:
            decision_version = int(
                claimed.get("decision_algorithm_version") or 0
            )
        except (TypeError, ValueError):
            return False, file_row
        if decision_version != EPISODE_IDENTITY_DECISION_ALGORITHM_VERSION:
            return False, file_row

        if str(scan.get("completed_profile") or "") == "normal":
            normal_metadata = claimed.get("normal_ocr")
            if not isinstance(normal_metadata, Mapping):
                return False, file_row
            try:
                normal_version = int(
                    normal_metadata.get("algorithm_version") or 0
                )
            except (TypeError, ValueError):
                return False, file_row
            if normal_version != NORMAL_EVIDENCE_ALGORITHM_VERSION:
                return False, file_row
            normal_source_key = str(
                normal_metadata.get("source_key") or ""
            ).strip().casefold()
            expected_source_config_signature = str(
                normal_metadata.get("source_config_signature") or ""
            )
            if normal_source_key in {"plex", "jellyfin"}:
                if not expected_source_config_signature:
                    return False, file_row
                current_source_config_signature = (
                    external_source_config_signature(
                        conn,
                        normal_source_key,
                    )
                    or "unconfigured"
                )
                if (
                    current_source_config_signature
                    != expected_source_config_signature
                ):
                    return False, file_row
            if not isinstance(normal_metadata.get("coverage_complete"), bool):
                return False, file_row
            try:
                planned_frame_count = int(
                    normal_metadata.get("planned_frame_count") or 0
                )
                completed_frame_count = int(
                    normal_metadata.get("completed_frame_count") or 0
                )
            except (TypeError, ValueError):
                return False, file_row
            if (
                planned_frame_count < 0
                or completed_frame_count < 0
                or completed_frame_count > planned_frame_count
                or (
                    bool(normal_metadata.get("coverage_complete"))
                    and bool(normal_metadata.get("budget_exhausted"))
                )
            ):
                return False, file_row

            speech_metadata = claimed.get("normal_speech")
            if not isinstance(speech_metadata, Mapping):
                return False, file_row
            try:
                speech_version = int(
                    speech_metadata.get("algorithm_version") or 0
                )
            except (TypeError, ValueError):
                return False, file_row
            if speech_version != NORMAL_SPEECH_ORCHESTRATION_VERSION:
                return False, file_row
            try:
                speech_evidence_version = int(
                    speech_metadata.get("evidence_algorithm_version") or 0
                )
            except (TypeError, ValueError):
                return False, file_row
            if (
                speech_evidence_version
                != NORMAL_SPEECH_EVIDENCE_ALGORITHM_VERSION
            ):
                return False, file_row

            try:
                transcript_count = int(
                    speech_metadata.get("transcript_count") or 0
                )
                text_transcript_count = int(
                    speech_metadata.get("text_transcript_count") or 0
                )
            except (TypeError, ValueError):
                return False, file_row
            if (
                transcript_count < 0
                or text_transcript_count < 0
                or text_transcript_count > transcript_count
            ):
                return False, file_row
            if not isinstance(speech_metadata.get("coverage_complete"), bool):
                return False, file_row
            try:
                planned_speech_windows = int(
                    speech_metadata.get("planned_windows") or 0
                )
            except (TypeError, ValueError):
                return False, file_row
            speech_failures = speech_metadata.get("failures")
            if not isinstance(speech_failures, list):
                return False, file_row
            speech_coverage_complete = bool(
                speech_metadata.get("coverage_complete")
            )
            if (
                planned_speech_windows < 0
                or (
                    speech_coverage_complete
                    and (
                        planned_speech_windows <= 0
                        or transcript_count != planned_speech_windows
                        or bool(speech_failures)
                        or bool(speech_metadata.get("budget_exhausted"))
                    )
                )
            ):
                return False, file_row

            speech_escalated = bool(speech_metadata.get("escalated"))
            speech_evidence_rows = [
                item for item in evidence
                if str(item.get("analyzer_key") or "") == "speech-synopsis"
            ]
            if (
                (speech_escalated and not speech_evidence_rows)
                or (not speech_escalated and speech_evidence_rows)
                or (not speech_escalated and transcript_count)
            ):
                return False, file_row

            expected_dialogue_group = f"subtitle-dialogue:{int(scan['file_id'])}"
            for speech_row in speech_evidence_rows:
                if (
                    str(speech_row.get("analyzer_version") or "")
                    != str(NORMAL_SPEECH_EVIDENCE_ALGORITHM_VERSION)
                    or str(speech_row.get("evidence_category") or "") != "speech"
                    or str(speech_row.get("correlation_group") or "")
                    != expected_dialogue_group
                    or str(speech_row.get("profile") or "") != "normal"
                ):
                    return False, file_row

            artifact_rows = []
            artifact_ids: list[int] = []
            if transcript_count:
                raw_artifact_ids = speech_metadata.get("artifact_ids")
                raw_cache_keys = speech_metadata.get("cache_keys")
                if (
                    not isinstance(raw_artifact_ids, list)
                    or not isinstance(raw_cache_keys, list)
                    or len(raw_artifact_ids) != transcript_count
                    or len(raw_cache_keys) != transcript_count
                ):
                    return False, file_row
                try:
                    artifact_ids = [int(value) for value in raw_artifact_ids]
                except (TypeError, ValueError):
                    return False, file_row
                cache_keys = [str(value or "") for value in raw_cache_keys]
                if (
                    any(value <= 0 for value in artifact_ids)
                    or len(set(artifact_ids)) != transcript_count
                    or any(not value for value in cache_keys)
                    or len(set(cache_keys)) != transcript_count
                ):
                    return False, file_row

                placeholders = ",".join("?" for _ in artifact_ids)
                artifact_rows = conn.execute(
                    f"""SELECT id,file_id,artifact_type,analyzer_key,
                               analyzer_version,cache_key,status,profile,
                               source_signature,file_size_bytes,file_modified_at,
                               start_ms,end_ms,text_value,payload_json
                        FROM media_identity_artifacts
                        WHERE id IN ({placeholders})""",
                    tuple(artifact_ids),
                ).fetchall()
                if len(artifact_rows) != transcript_count:
                    return False, file_row
                expected_by_id = dict(zip(artifact_ids, cache_keys))
                for artifact_row in artifact_rows:
                    artifact_payload = _json_object(
                        artifact_row["payload_json"]
                    )
                    window_payload = artifact_payload.get("window")
                    audio_payload = artifact_payload.get("audio_identity")
                    if (
                        not isinstance(window_payload, Mapping)
                        or not isinstance(audio_payload, Mapping)
                        or not isinstance(artifact_payload.get("engine"), Mapping)
                        or not isinstance(artifact_payload.get("model"), Mapping)
                        or not isinstance(artifact_payload.get("request"), Mapping)
                        or not isinstance(artifact_payload.get("transcript"), Mapping)
                    ):
                        return False, file_row
                    try:
                        payload_start = int(window_payload["start_ms"])
                        payload_end = int(window_payload["end_ms"])
                        artifact_start = int(artifact_row["start_ms"])
                        artifact_end = int(artifact_row["end_ms"])
                    except (KeyError, TypeError, ValueError):
                        return False, file_row
                    artifact_source_signature = str(
                        artifact_row["source_signature"] or ""
                    )
                    if (
                        payload_start != artifact_start
                        or payload_end != artifact_end
                        or not artifact_source_signature
                        or str(audio_payload.get("source_signature") or "")
                        != artifact_source_signature
                    ):
                        return False, file_row

                    if (
                        int(artifact_row["file_id"]) != int(scan["file_id"])
                        or str(artifact_row["artifact_type"] or "")
                        != "speech_transcript"
                        or str(artifact_row["analyzer_key"] or "")
                        != "local-speech-transcript"
                        or str(artifact_row["analyzer_version"] or "") != "1"
                        or str(artifact_row["status"] or "") != "complete"
                        or str(artifact_row["profile"] or "") != "normal"
                        or str(artifact_row["cache_key"] or "")
                        != expected_by_id.get(int(artifact_row["id"]), "")
                        or int(artifact_row["file_size_bytes"] or 0)
                        != int(scan["file_size_bytes"] or 0)
                        or not _same_modified_at(
                            artifact_row["file_modified_at"],
                            scan["file_modified_at"],
                        )
                    ):
                        return False, file_row

            artifact_by_id = {
                int(row["id"]): row for row in artifact_rows
            }
            text_artifact_ids = {
                artifact_id
                for artifact_id, row in artifact_by_id.items()
                if str(row["text_value"] or "").strip()
            }
            if len(text_artifact_ids) != text_transcript_count:
                return False, file_row

            if speech_escalated:
                target_speech_language = normalize_speech_language(language)
                speech_tokens: set[str] = set()
                speech_bigrams: set[tuple[str, str]] = set()
                expected_windows: list[dict[str, Any]] = []
                cache_observations: list[dict[str, Any]] = []
                transcript_parts: list[str] = []
                aligned_artifact_ids: set[int] = set()
                mismatched_artifact_ids: set[int] = set()
                aligned_languages: set[str] = set()
                mismatched_languages: set[str] = set()

                for artifact_id in artifact_ids:
                    artifact_row = artifact_by_id.get(artifact_id)
                    if artifact_row is None:
                        return False, file_row
                    raw_transcript_text = str(
                        artifact_row["text_value"] or ""
                    )
                    transcript_text = raw_transcript_text.strip()
                    if not transcript_text:
                        continue

                    artifact_payload = _json_object(
                        artifact_row["payload_json"]
                    )
                    transcript_payload = artifact_payload.get("transcript")
                    if not isinstance(transcript_payload, Mapping):
                        return False, file_row
                    transcript_language = normalize_speech_language(
                        str(transcript_payload.get("language") or "")
                    )

                    cache_observations.append({
                        "cache_key": str(artifact_row["cache_key"] or ""),
                        "source_signature": str(
                            artifact_row["source_signature"] or ""
                        ),
                        "start_ms": int(artifact_row["start_ms"]),
                        "end_ms": int(artifact_row["end_ms"]),
                        "transcript_sha256": hashlib.sha256(
                            raw_transcript_text.encode("utf-8")
                        ).hexdigest(),
                    })

                    if transcript_language != target_speech_language:
                        mismatched_artifact_ids.add(artifact_id)
                        mismatched_languages.add(transcript_language)
                        continue

                    aligned_artifact_ids.add(artifact_id)
                    aligned_languages.add(transcript_language)
                    corpus = text_corpus(transcript_text)
                    speech_tokens.update(corpus.tokens)
                    speech_bigrams.update(corpus.bigrams)
                    expected_windows.append({
                        "artifact_id": artifact_id,
                        "start_ms": int(artifact_row["start_ms"]),
                        "end_ms": int(artifact_row["end_ms"]),
                        "cache_key": str(artifact_row["cache_key"] or ""),
                    })
                    transcript_parts.append(transcript_text)

                expected_speech_cache_key = ""
                if aligned_artifact_ids:
                    expected_speech_cache_key = hashlib.sha256(
                        json.dumps(
                            {"observations": cache_observations},
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            default=str,
                        ).encode("utf-8")
                    ).hexdigest()

                speech_corpus = TextCorpus(
                    tokens=frozenset(speech_tokens),
                    bigrams=frozenset(speech_bigrams),
                )
                expected_similarities: dict[str, float] = {}
                if speech_corpus.tokens:
                    candidate_rows = conn.execute(
                        """SELECT candidate_key,details_json
                           FROM media_identity_candidates
                           WHERE scan_id=?
                           ORDER BY rank,candidate_key""",
                        (int(scan["id"]),),
                    ).fetchall()
                    for candidate_row in candidate_rows:
                        candidate_details = _json_object(
                            candidate_row["details_json"]
                        )
                        overview = str(
                            candidate_details.get("overview") or ""
                        ).strip()
                        if overview:
                            expected_similarities[
                                str(candidate_row["candidate_key"])
                            ] = synopsis_similarity_from_corpus(
                                speech_corpus,
                                overview,
                            )

                if expected_similarities:
                    evidence_candidate_keys = [
                        str(row.get("candidate_key") or "")
                        for row in speech_evidence_rows
                    ]
                    if (
                        len(evidence_candidate_keys)
                        != len(expected_similarities)
                        or set(evidence_candidate_keys)
                        != set(expected_similarities)
                    ):
                        return False, file_row
                elif (
                    len(speech_evidence_rows) != 1
                    or str(
                        speech_evidence_rows[0].get("candidate_key") or ""
                    )
                ):
                    return False, file_row

                expected_excerpt = "\n".join(transcript_parts)[:1200]
                for speech_row in speech_evidence_rows:
                    details = speech_row.get("details")
                    if not isinstance(details, Mapping):
                        details = _json_object(speech_row.get("details_json"))
                    if not details:
                        return False, file_row
                    correlated_with = details.get("correlated_with")
                    if (
                        not isinstance(correlated_with, list)
                        or "subtitle-synopsis" not in {
                            str(value) for value in correlated_with
                        }
                    ):
                        return False, file_row
                    try:
                        speech_strength = float(
                            speech_row.get("strength") or 0.0
                        )
                    except (TypeError, ValueError):
                        return False, file_row

                    if aligned_artifact_ids:
                        raw_ids = details.get("artifact_ids")
                        if not isinstance(raw_ids, list):
                            return False, file_row
                        try:
                            evidence_artifact_ids = {
                                int(value) for value in raw_ids
                            }
                            evidence_transcript_count = int(
                                details.get("transcript_count") or 0
                            )
                        except (TypeError, ValueError):
                            return False, file_row
                        if (
                            evidence_artifact_ids != aligned_artifact_ids
                            or details.get("windows") != expected_windows
                            or evidence_transcript_count
                            != len(aligned_artifact_ids)
                            or str(
                                details.get("transcript_excerpt") or ""
                            ) != expected_excerpt
                            or str(speech_row.get("cache_key") or "")
                            != expected_speech_cache_key
                            or normalize_speech_language(
                                str(details.get("synopsis_language") or "")
                            )
                            != target_speech_language
                            or sorted(
                                str(value)
                                for value in details.get(
                                    "transcript_languages", []
                                )
                            )
                            != sorted(aligned_languages)
                        ):
                            return False, file_row
                    elif mismatched_artifact_ids:
                        raw_ids = details.get("artifact_ids")
                        raw_languages = details.get("transcript_languages")
                        if (
                            not isinstance(raw_ids, list)
                            or not isinstance(raw_languages, list)
                        ):
                            return False, file_row
                        try:
                            evidence_artifact_ids = {
                                int(value) for value in raw_ids
                            }
                            evidence_transcript_count = int(
                                details.get("transcript_count") or 0
                            )
                        except (TypeError, ValueError):
                            return False, file_row
                        if (
                            evidence_artifact_ids != mismatched_artifact_ids
                            or evidence_transcript_count
                            != len(mismatched_artifact_ids)
                            or normalize_speech_language(
                                str(details.get("synopsis_language") or "")
                            )
                            != target_speech_language
                            or sorted(str(value) for value in raw_languages)
                            != sorted(mismatched_languages)
                            or str(speech_row.get("cache_key") or "")
                        ):
                            return False, file_row

                    candidate_key = str(
                        speech_row.get("candidate_key") or ""
                    )
                    if expected_similarities:
                        expected_similarity = expected_similarities.get(
                            candidate_key
                        )
                        if expected_similarity is None:
                            return False, file_row
                        try:
                            persisted_similarity = float(
                                details.get("similarity")
                            )
                            persisted_threshold = float(
                                details.get("support_threshold")
                            )
                        except (TypeError, ValueError):
                            return False, file_row
                        expected_relation = (
                            "supports"
                            if expected_similarity
                            >= TEXT_SUPPORT_THRESHOLD
                            else "neutral"
                        )
                        expected_strength = (
                            expected_similarity
                            if expected_relation == "supports"
                            else 0.0
                        )
                        if (
                            abs(
                                persisted_similarity
                                - expected_similarity
                            ) > 1e-12
                            or abs(
                                persisted_threshold
                                - TEXT_SUPPORT_THRESHOLD
                            ) > 1e-12
                            or str(
                                speech_row.get("relation") or ""
                            ) != expected_relation
                            or abs(
                                speech_strength - expected_strength
                            ) > 1e-12
                        ):
                            return False, file_row
                    elif (
                        str(speech_row.get("relation") or "")
                        != "neutral"
                        or speech_strength != 0.0
                        or (
                            not aligned_artifact_ids
                            and not mismatched_artifact_ids
                            and str(speech_row.get("cache_key") or "")
                        )
                    ):
                        return False, file_row

        normalized_expected = {
            str(key): str(value)
            for key, value in expected_signatures.items()
        }
        if current_signatures != normalized_expected:
            return False, file_row
        if (
            combined_scan_input_signature(current_signatures)
            != str(scan.get("metadata_signature") or "")
        ):
            return False, file_row

        if not decision_snapshot_matches(conn, scan):
            return False, file_row

        return True, file_row

    def _external_visual_snapshot_is_current(
        self,
        conn: sqlite3.Connection,
        scan: Mapping[str, Any],
    ) -> bool:
        """Re-read exact external preview bytes for external-backed decisions."""
        claimed = self._claimed_identity(scan)
        normal_metadata = claimed.get("normal_ocr")
        if not isinstance(normal_metadata, Mapping):
            return True
        source_key = str(normal_metadata.get("source_key") or "").strip().casefold()
        if source_key not in {"plex", "jellyfin"}:
            return True

        raw_cache_keys = normal_metadata.get("observation_cache_keys")
        if not isinstance(raw_cache_keys, list):
            return False
        cache_keys = [str(value or "") for value in raw_cache_keys]
        if any(not value for value in cache_keys) or len(set(cache_keys)) != len(cache_keys):
            return False
        if not cache_keys:
            return True
        if self.external_registry_factory is None:
            # An external-backed finding cannot remain actionable when this
            # process has no way to revalidate the exact provider preview bytes.
            return False

        placeholders = ",".join("?" for _ in cache_keys)
        rows = conn.execute(
            f"""SELECT id,cache_key,source_kind,source_ref,source_signature,
                       start_ms,payload_json
                FROM media_identity_artifacts
                WHERE file_id=? AND artifact_type='visual_text'
                  AND analyzer_key='external-preview-ocr'
                  AND cache_key IN ({placeholders})
                  AND status='complete'
                ORDER BY id""",
            (int(scan["file_id"]), *cache_keys),
        ).fetchall()
        by_cache_key = {str(row["cache_key"] or ""): row for row in rows}
        if set(by_cache_key) != set(cache_keys):
            return False

        try:
            registry = self.external_registry_factory()
            source = registry.get(source_key)
        except Exception:
            return False
        if source is None:
            return False

        budget = VisualAttemptBudget(
            max_frame_attempts=_ACTION_EXTERNAL_PREVIEW_MAX_FRAMES,
            max_source_bytes=_ACTION_EXTERNAL_PREVIEW_MAX_SOURCE_BYTES,
            # Freshness does not run OCR, but these fields remain positive so
            # the same shared budget object can be used by source adapters.
            max_image_bytes=1,
            max_text_chars=1,
        )
        with visual_budget_scope(budget):
            for cache_key in cache_keys:
                row = by_cache_key[cache_key]
                if str(row["source_kind"] or "").strip().casefold() != source_key:
                    return False
                payload = _json_object(row["payload_json"])
                details = payload.get("details")
                if not isinstance(details, Mapping):
                    return False
                expected_sha256 = str(details.get("preview_sha256") or "").strip().casefold()
                if (
                    len(expected_sha256) != 64
                    or any(character not in "0123456789abcdef" for character in expected_sha256)
                ):
                    return False
                item_id = str(payload.get("item_id") or "").strip()
                source_ref = str(row["source_ref"] or "")
                source_signature = str(row["source_signature"] or "")
                try:
                    timestamp_ms = int(row["start_ms"])
                except (TypeError, ValueError):
                    return False
                if not item_id or not source_ref or not source_signature or timestamp_ms < 0:
                    return False

                width = height = None
                try:
                    asset_payload = json.loads(source_ref)
                except (TypeError, ValueError, json.JSONDecodeError):
                    asset_payload = None
                if isinstance(asset_payload, Mapping):
                    try:
                        if asset_payload.get("width") is not None:
                            width = int(asset_payload["width"])
                        if asset_payload.get("height") is not None:
                            height = int(asset_payload["height"])
                    except (TypeError, ValueError):
                        return False

                frame = PreviewFrameRef(
                    source_key=source_key,
                    item_id=item_id,
                    timestamp_ms=timestamp_ms,
                    asset_ref=source_ref,
                    source_signature=source_signature,
                    width=width,
                    height=height,
                )
                try:
                    budget.reserve_frame_attempt()
                    current_payload = bytes(source.read_preview(frame))
                except ExternalAnalysisError:
                    return False
                except (OSError, TypeError, ValueError):
                    return False
                if hashlib.sha256(current_payload).hexdigest() != expected_sha256:
                    return False
        return True

    def _review_snapshot_is_current(
        self,
        conn: sqlite3.Connection,
        scan: Mapping[str, Any],
        evidence: list[dict[str, Any]],
        *,
        verify_content: bool = False,
    ) -> tuple[bool, dict[str, Any] | None]:
        current, file_row = self._scan_snapshot_is_current(
            conn,
            scan,
            evidence,
            verify_content=verify_content,
        )
        if (
            current
            and verify_content
            and not self._external_visual_snapshot_is_current(conn, scan)
        ):
            return False, file_row
        return current, file_row

    @staticmethod
    def _decision_token(
        claimed: Mapping[str, Any],
    ) -> tuple[int, str]:
        snapshot = claimed.get("decision_snapshot")
        if not isinstance(snapshot, Mapping):
            return 0, ""
        try:
            snapshot_version = int(snapshot.get("version") or 0)
            snapshot_revision = int(snapshot.get("revision") or 0)
        except (TypeError, ValueError):
            return 0, ""
        revision = result_revision(claimed)
        digest = str(snapshot.get("sha256") or "").strip().casefold()
        if (
            snapshot_version != DECISION_SNAPSHOT_VERSION
            or revision <= 0
            or snapshot_revision != revision
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            return 0, ""
        return revision, digest

    @staticmethod
    def _confirmation_provenance(
        scan: Mapping[str, Any],
    ) -> dict[str, Any]:
        claimed = _json_object(scan.get("claimed_identity_json"))
        revision, digest = MediaIdentityDecisionService._decision_token(claimed)
        metadata_signature = str(scan.get("metadata_signature") or "")
        if revision <= 0 or not digest or not metadata_signature:
            raise MediaIdentityDecisionError(
                "Episode Identity confirmation has incomplete decision provenance."
            )
        return {
            "source_scan_snapshot_id": int(scan["id"]),
            "source_result_revision": revision,
            "source_decision_snapshot_sha256": digest,
            "source_metadata_signature": metadata_signature,
        }

    def confirmation_status(
        self,
        file_id: int,
        *,
        verify_external: bool = True,
        validated_source: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT * FROM media_identity_confirmations WHERE file_id=?""",
                (int(file_id),),
            ).fetchone()
            if not row:
                return None
            confirmation = dict(row)
            source_scan_id = confirmation.get("source_scan_id")
            try:
                source_scan_snapshot_id = int(
                    confirmation.get("source_scan_snapshot_id") or 0
                )
                source_result_revision = int(
                    confirmation.get("source_result_revision") or 0
                )
            except (TypeError, ValueError):
                source_scan_snapshot_id = 0
                source_result_revision = 0
            source_decision_sha256 = str(
                confirmation.get("source_decision_snapshot_sha256") or ""
            ).strip().casefold()
            source_metadata_signature = str(
                confirmation.get("source_metadata_signature") or ""
            )

            validated_match = False
            if isinstance(validated_source, Mapping):
                try:
                    validated_match = (
                        bool(validated_source.get("current"))
                        and bool(validated_source.get("content_verified"))
                        and int(validated_source.get("file_id") or 0)
                        == int(file_id)
                        and int(validated_source.get("scan_id") or 0)
                        == int(source_scan_id or 0)
                        and int(validated_source.get("result_revision") or 0)
                        == source_result_revision
                        and str(
                            validated_source.get(
                                "decision_snapshot_sha256"
                            ) or ""
                        ).strip().casefold()
                        == source_decision_sha256
                        and str(
                            validated_source.get("metadata_signature") or ""
                        )
                        == source_metadata_signature
                        and int(
                            validated_source.get("file_size_bytes") or 0
                        )
                        == int(confirmation["confirmed_size_bytes"] or 0)
                        and _same_modified_at(
                            validated_source.get("file_modified_at"),
                            confirmation["confirmed_modified_at"],
                        )
                        and str(
                            validated_source.get("file_sha256") or ""
                        ).strip().casefold()
                        == str(
                            confirmation.get("confirmed_sha256") or ""
                        ).strip().casefold()
                    )
                except (TypeError, ValueError):
                    validated_match = False

            if validated_match:
                current = True
            else:
                current, _ = self._snapshot_is_current(
                    conn,
                    int(file_id),
                    size_bytes=int(
                        confirmation["confirmed_size_bytes"] or 0
                    ),
                    modified_at=confirmation["confirmed_modified_at"],
                    sha256=confirmation["confirmed_sha256"],
                    verify_content=True,
                )

            if current and (
                source_scan_id is None
                or source_scan_snapshot_id <= 0
                or source_result_revision <= 0
                or len(source_decision_sha256) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in source_decision_sha256
                )
                or not source_metadata_signature
            ):
                current = False
            if current:
                try:
                    live_source_scan_id = int(source_scan_id)
                except (TypeError, ValueError):
                    current = False
                else:
                    if live_source_scan_id != source_scan_snapshot_id:
                        current = False
            if current:
                try:
                    scan, _, evidence = self._scan_snapshot(
                        conn, int(source_scan_id)
                    )
                except MediaIdentityDecisionError:
                    current = False
                else:
                    scan_matches_confirmed_file = (
                        int(scan.get("file_size_bytes") or 0)
                        == int(confirmation["confirmed_size_bytes"] or 0)
                        and _same_modified_at(
                            scan.get("file_modified_at"),
                            confirmation["confirmed_modified_at"],
                        )
                        and str(
                            scan.get("file_sha256") or ""
                        ).strip().casefold()
                        == str(
                            confirmation.get("confirmed_sha256") or ""
                        ).strip().casefold()
                    )
                    if not scan_matches_confirmed_file:
                        current = False
                    elif not validated_match:
                        # The exact current media bytes were already hashed
                        # against the confirmation snapshot above. If the source
                        # scan names that identical size/mtime/SHA snapshot, do
                        # not hash the same file a second time in this request.
                        current, _ = self._scan_snapshot_is_current(
                            conn,
                            scan,
                            evidence,
                            verify_content=False,
                        )
                        if (
                            current
                            and verify_external
                            and not self._external_visual_snapshot_is_current(
                                conn,
                                scan,
                            )
                        ):
                            current = False
                    if current:
                        try:
                            live_provenance = self._confirmation_provenance(scan)
                        except MediaIdentityDecisionError:
                            current = False
                        else:
                            current = (
                                int(live_provenance["source_scan_snapshot_id"])
                                == source_scan_snapshot_id
                                and int(live_provenance["source_result_revision"])
                                == source_result_revision
                                and str(
                                    live_provenance[
                                        "source_decision_snapshot_sha256"
                                    ]
                                )
                                == source_decision_sha256
                                and str(
                                    live_provenance["source_metadata_signature"]
                                )
                                == source_metadata_signature
                            )
        confirmation["freshness"] = "current" if current else "stale"
        confirmation["current"] = current
        return confirmation

    @staticmethod
    def _candidate_for_key(
        candidates: list[dict[str, Any]], candidate_key: str
    ) -> dict[str, Any]:
        candidate = next(
            (
                item for item in candidates
                if str(item.get("candidate_key") or "") == str(candidate_key)
            ),
            None,
        )
        if candidate is None:
            raise MediaIdentityDecisionError(
                "That episode candidate does not belong to this scan."
            )
        return candidate

    def _confirm(
        self,
        scan_id: int,
        candidate_key: str,
        user_id: int | None,
        *,
        expected_candidate_key: str,
        expected_result_revision: int,
        expected_decision_snapshot_sha256: str,
        intent_kind: str,
    ) -> dict[str, Any]:
        reviewed_candidate_key = str(expected_candidate_key or "").strip()
        candidate_key = str(candidate_key or "").strip()
        if not reviewed_candidate_key or candidate_key != reviewed_candidate_key:
            raise MediaIdentityDecisionError(
                "The reviewed Episode Identity candidate is missing or changed. "
                "Refresh the scan before confirming it."
            )
        with self.database.connect() as conn:
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            scan, candidates, evidence = self._scan_snapshot(conn, int(scan_id))
            if scan["status"] != "complete":
                raise MediaIdentityDecisionError(
                    "Only a complete Episode Identity scan can be confirmed."
                )
            current_claimed = self._claimed_identity(scan)
            current_revision, current_digest = self._decision_token(
                current_claimed
            )
            if (
                current_revision != int(expected_result_revision)
                or current_digest
                != str(expected_decision_snapshot_sha256 or "").strip().casefold()
            ):
                raise MediaIdentityDecisionError(
                    "The Episode Identity result changed after it was reviewed. "
                    "Refresh the scan before confirming it."
                )

            resolution = self._resolve_snapshot(scan, candidates, evidence)
            if (
                scan.get("result_state") is None
                or str(scan.get("result_state") or "") != resolution.state.value
                or str(scan.get("best_candidate_key") or "")
                != str(resolution.best_candidate_key or "")
            ):
                raise MediaIdentityDecisionError(
                    "The sealed Episode Identity decision no longer matches the "
                    "reviewed result. Refresh the scan before confirming it."
                )
            claimed_keys = tuple(resolution.claimed_candidate_keys)
            if intent_kind == "current":
                if len(claimed_keys) != 1 or candidate_key != claimed_keys[0]:
                    raise MediaIdentityDecisionError(
                        "The reviewed current-filename candidate is no longer the "
                        "single catalog claim. Refresh the scan before confirming it."
                    )
            elif intent_kind == "best":
                if (
                    str(scan.get("result_state") or "") not in SUGGESTED_CONFIRM_STATES
                    or candidate_key != str(resolution.best_candidate_key or "")
                    or candidate_key in set(claimed_keys)
                ):
                    raise MediaIdentityDecisionError(
                        "The reviewed suggested candidate is no longer the current "
                        "alternate decision. Refresh the scan before confirming it."
                    )
            else:
                raise MediaIdentityDecisionError(
                    "Episode Identity confirmation intent is invalid."
                )

            candidate = self._candidate_for_key(candidates, candidate_key)
            current, _ = self._review_snapshot_is_current(
                conn,
                scan,
                evidence,
                verify_content=True,
            )
            if not current:
                raise MediaIdentityDecisionError(
                    "The media file or supporting evidence changed after this identity scan. Run verification again before confirming it."
                )
            provenance = self._confirmation_provenance(scan)
            conn.execute(
                """INSERT INTO media_identity_confirmations(
                     file_id,identity_kind,provider,provider_item_id,
                     expected_episode_id,order_namespace,season,episode,display_name,
                     source_scan_id,source_scan_snapshot_id,
                     source_result_revision,source_decision_snapshot_sha256,
                     source_metadata_signature,confirmed_size_bytes,
                     confirmed_modified_at,confirmed_sha256,confirmed_by,confirmed_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(file_id) DO UPDATE SET
                     identity_kind=excluded.identity_kind,
                     provider=excluded.provider,
                     provider_item_id=excluded.provider_item_id,
                     expected_episode_id=excluded.expected_episode_id,
                     order_namespace=excluded.order_namespace,
                     season=excluded.season,
                     episode=excluded.episode,
                     display_name=excluded.display_name,
                     source_scan_id=excluded.source_scan_id,
                     source_scan_snapshot_id=excluded.source_scan_snapshot_id,
                     source_result_revision=excluded.source_result_revision,
                     source_decision_snapshot_sha256=excluded.source_decision_snapshot_sha256,
                     source_metadata_signature=excluded.source_metadata_signature,
                     confirmed_size_bytes=excluded.confirmed_size_bytes,
                     confirmed_modified_at=excluded.confirmed_modified_at,
                     confirmed_sha256=excluded.confirmed_sha256,
                     confirmed_by=excluded.confirmed_by,
                     confirmed_at=CURRENT_TIMESTAMP""",
                (
                    int(scan["file_id"]),
                    candidate["identity_kind"],
                    candidate["provider"] or "",
                    candidate["provider_item_id"] or "",
                    candidate["expected_episode_id"],
                    candidate["order_namespace"] or "",
                    candidate["season"],
                    candidate["episode"],
                    candidate["display_name"] or "",
                    int(scan_id),
                    int(provenance["source_scan_snapshot_id"]),
                    int(provenance["source_result_revision"]),
                    str(provenance["source_decision_snapshot_sha256"]),
                    str(provenance["source_metadata_signature"]),
                    int(scan["file_size_bytes"] or 0),
                    scan["file_modified_at"],
                    scan["file_sha256"],
                    user_id if user_id and int(user_id) > 0 else None,
                ),
            )
            saved = conn.execute(
                "SELECT * FROM media_identity_confirmations WHERE file_id=?",
                (int(scan["file_id"]),),
            ).fetchone()
            if saved is None:
                raise MediaIdentityDecisionError(
                    "Episode Identity confirmation was not saved."
                )
            status = dict(saved)
        status["freshness"] = "current"
        status["current"] = True
        return status

    def confirm_current(
        self,
        scan_id: int,
        user_id: int | None,
        *,
        expected_result_revision: int,
        expected_decision_snapshot_sha256: str,
        expected_candidate_key: str,
    ) -> dict[str, Any]:
        return self._confirm(
            int(scan_id),
            str(expected_candidate_key or ""),
            user_id,
            expected_candidate_key=str(expected_candidate_key or ""),
            expected_result_revision=int(expected_result_revision),
            expected_decision_snapshot_sha256=str(
                expected_decision_snapshot_sha256 or ""
            ),
            intent_kind="current",
        )

    def confirm_best(
        self,
        scan_id: int,
        user_id: int | None,
        *,
        expected_result_revision: int,
        expected_decision_snapshot_sha256: str,
        expected_candidate_key: str,
    ) -> dict[str, Any]:
        return self._confirm(
            int(scan_id),
            str(expected_candidate_key or ""),
            user_id,
            expected_candidate_key=str(expected_candidate_key or ""),
            expected_result_revision=int(expected_result_revision),
            expected_decision_snapshot_sha256=str(
                expected_decision_snapshot_sha256 or ""
            ),
            intent_kind="best",
        )

    def scan_detail(
        self,
        scan_id: int,
        *,
        resolve_if_needed: bool = False,
        verify_actionable_content: bool = True,
        include_confirmation: bool = True,
        verify_confirmation_external: bool = True,
    ) -> dict[str, Any]:
        if resolve_if_needed:
            with self.database.connect() as conn:
                row = conn.execute(
                    "SELECT result_state,status FROM media_identity_scans WHERE id=?",
                    (int(scan_id),),
                ).fetchone()
            if row and row["status"] == "complete" and row["result_state"] is None:
                self.resolve_scan(int(scan_id))

        with self.database.connect() as conn:
            if not conn.in_transaction:
                conn.execute("BEGIN")
            scan, candidates, evidence = self._scan_snapshot(conn, int(scan_id))
            file_row = conn.execute(
                """SELECT f.id,f.title_id,f.filename,f.path,f.size_bytes,f.modified_at,
                          f.season,f.episode_start,f.episode_end,f.extension,
                          COALESCE(NULLIF(t.metadata_title,''),t.title) title_name,
                          t.metadata_year,t.year,r.id root_id,r.label root_label
                   FROM files f
                   JOIN titles t ON t.id=f.title_id
                   JOIN roots r ON r.id=t.root_id
                   WHERE f.id=?""",
                (int(scan["file_id"]),),
            ).fetchone()
            snapshot_current, _ = self._review_snapshot_is_current(
                conn,
                scan,
                evidence,
                verify_content=(
                    verify_actionable_content
                    and str(scan.get("result_state") or "") in ACTIONABLE_STATES
                ),
            )
        claimed = self._claimed_identity(scan)
        resolution = self._resolve_snapshot(scan, candidates, evidence)
        result_revision_value, decision_digest = self._decision_token(claimed)
        content_verified = bool(
            verify_actionable_content
            and str(scan.get("result_state") or "") in ACTIONABLE_STATES
        )
        validated_source = (
            {
                "current": True,
                "content_verified": True,
                "scan_id": int(scan["id"]),
                "file_id": int(scan["file_id"]),
                "result_revision": result_revision_value,
                "decision_snapshot_sha256": decision_digest,
                "metadata_signature": str(
                    scan.get("metadata_signature") or ""
                ),
                "file_size_bytes": int(
                    scan.get("file_size_bytes") or 0
                ),
                "file_modified_at": scan.get("file_modified_at"),
                "file_sha256": str(scan.get("file_sha256") or ""),
            }
            if snapshot_current and content_verified
            else None
        )

        speech_analysis = None
        speech_metadata = claimed.get("normal_speech")
        if isinstance(speech_metadata, Mapping):
            speech_rows = [
                item for item in evidence
                if str(item.get("analyzer_key") or "") == "speech-synopsis"
            ]
            strongest_details: dict[str, Any] = {}
            strongest_similarity = 0.0
            correlation_group = ""
            for item in speech_rows:
                details = item.get("details")
                if not isinstance(details, Mapping):
                    continue
                try:
                    similarity = float(details.get("similarity") or 0.0)
                except (TypeError, ValueError):
                    similarity = 0.0
                if not strongest_details or similarity > strongest_similarity:
                    strongest_details = dict(details)
                    strongest_similarity = max(0.0, min(1.0, similarity))
                    correlation_group = str(item.get("correlation_group") or "")

            def speech_int(key: str) -> int:
                try:
                    return max(0, int(speech_metadata.get(key) or 0))
                except (TypeError, ValueError):
                    return 0

            raw_windows = strongest_details.get("windows")
            windows: list[dict[str, int]] = []
            if isinstance(raw_windows, list):
                for item in raw_windows[:8]:
                    if not isinstance(item, Mapping):
                        continue
                    try:
                        start_ms = max(0, int(item.get("start_ms") or 0))
                        end_ms = max(start_ms, int(item.get("end_ms") or start_ms))
                    except (TypeError, ValueError):
                        continue
                    windows.append({
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                    })

            raw_failures = speech_metadata.get("failures")
            failures = (
                [str(item) for item in raw_failures[:8]]
                if isinstance(raw_failures, list)
                else []
            )
            speech_analysis = {
                "escalated": bool(speech_metadata.get("escalated")),
                "planned_windows": speech_int("planned_windows"),
                "transcript_count": speech_int("transcript_count"),
                "text_transcript_count": speech_int("text_transcript_count"),
                "reused_artifact_count": speech_int("reused_artifact_count"),
                "budget_exhausted": bool(speech_metadata.get("budget_exhausted")),
                "failures": failures,
                "evidence_count": len(speech_rows),
                "strongest_similarity": round(strongest_similarity, 6),
                "correlation_group": correlation_group,
                "windows": windows,
                "transcript_excerpt": str(
                    strongest_details.get("transcript_excerpt") or ""
                )[:1200],
            }

        candidate_by_key = {
            str(item["candidate_key"]): item for item in candidates
        }
        best = candidate_by_key.get(str(scan.get("best_candidate_key") or ""))
        confirmation = (
            self.confirmation_status(
                int(scan["file_id"]),
                verify_external=verify_confirmation_external,
                validated_source=validated_source,
            )
            if include_confirmation
            else None
        )
        result = dict(scan)
        result["claimed_identity"] = claimed
        result.pop("claimed_identity_json", None)
        result["result_revision"] = result_revision_value
        result["decision_snapshot_sha256"] = decision_digest
        result["candidates"] = candidates
        for item in evidence:
            item["strength_label"] = _strength_label(item.get("strength"))
        for candidate in candidates:
            candidate["support_label"] = _strength_label(
                candidate.get("support_strength")
            )
            candidate["conflict_label"] = _strength_label(
                candidate.get("conflict_strength")
            )
        result["evidence"] = evidence
        result["speech_analysis"] = speech_analysis
        result["file"] = dict(file_row) if file_row else None
        result["best_candidate"] = best
        result["claimed_candidate_keys"] = list(resolution.claimed_candidate_keys)
        result["margin"] = resolution.margin
        result["decision_pending"] = (
            str(result.get("status") or "") == "complete"
            and not result.get("result_state")
        )
        result["resolution_explanation"] = (
            "This completed scan is waiting for the decision resolver. Refresh Library Health before making an identity decision."
            if result["decision_pending"]
            else resolution.explanation
        )
        result["confirmation"] = confirmation
        result["snapshot_current"] = snapshot_current
        confirmed_key = None
        if confirmation and confirmation.get("current"):
            for candidate in candidates:
                if self._confirmation_matches_candidate(confirmation, candidate):
                    confirmed_key = str(candidate["candidate_key"])
                    break
        result["confirmed_claimed"] = (
            confirmed_key is not None
            and confirmed_key in set(result["claimed_candidate_keys"])
        )
        result["actionable"] = (
            snapshot_current
            and not result["confirmed_claimed"]
            and str(result.get("result_state") or "") in ACTIONABLE_STATES
        )
        return result

    def latest_scan_for_file(
        self, file_id: int, *, resolve_if_needed: bool = True
    ) -> dict[str, Any] | None:
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT id FROM media_identity_scans
                   WHERE file_id=? AND status='complete'
                   ORDER BY id DESC LIMIT 1""",
                (int(file_id),),
            ).fetchone()
        if not row:
            return None
        return self.scan_detail(int(row["id"]), resolve_if_needed=resolve_if_needed)

    @staticmethod
    def _confirmation_matches_candidate(
        confirmation: Mapping[str, Any] | None,
        candidate: Mapping[str, Any] | None,
    ) -> bool:
        if not confirmation or not confirmation.get("current") or not candidate:
            return False
        provider_id = str(candidate.get("provider_item_id") or "")
        if provider_id:
            return (
                str(confirmation.get("provider") or "").casefold()
                == str(candidate.get("provider") or "").casefold()
                and str(confirmation.get("provider_item_id") or "") == provider_id
            )
        expected = candidate.get("expected_episode_id")
        return expected is not None and confirmation.get("expected_episode_id") == expected

    def mie_findings(self) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            latest_rows = [
                dict(row)
                for row in conn.execute(
                    """SELECT s.id,s.file_id,s.completed_profile,
                              s.metadata_signature,s.file_size_bytes,s.file_sha256
                       FROM media_identity_scans s
                       WHERE s.status='complete' AND s.result_state IS NOT NULL
                         AND s.id=(
                           SELECT MAX(latest.id) FROM media_identity_scans latest
                           WHERE latest.file_id=s.file_id AND latest.status='complete'
                         )
                       ORDER BY s.id"""
                ).fetchall()
            ]
            effective_scan_ids: list[
                tuple[int, tuple[int, ...], bool]
            ] = []
            for latest in latest_rows:
                preserved_normal_ids: tuple[int, ...] = ()
                history_truncated = False
                metadata_signature = str(
                    latest.get("metadata_signature") or ""
                )
                file_sha256 = str(latest.get("file_sha256") or "").strip().casefold()
                if (
                    metadata_signature
                    and len(file_sha256) == 64
                    and all(
                        character in "0123456789abcdef"
                        for character in file_sha256
                    )
                ):
                    preserved_rows = conn.execute(
                        """SELECT id
                           FROM media_identity_scans
                           WHERE file_id=? AND id<=?
                             AND status='complete' AND result_state IS NOT NULL
                             AND completed_profile='normal'
                             AND metadata_signature=?
                             AND file_size_bytes=?
                             AND COALESCE(file_sha256,'')=?
                           ORDER BY id DESC LIMIT ?""",
                        (
                            int(latest["file_id"]),
                            int(latest["id"]),
                            metadata_signature,
                            int(latest.get("file_size_bytes") or 0),
                            file_sha256,
                            _MIE_NORMAL_HISTORY_VALIDATION_LIMIT + 1,
                        ),
                    ).fetchall()
                    history_truncated = (
                        len(preserved_rows)
                        > _MIE_NORMAL_HISTORY_VALIDATION_LIMIT
                    )
                    preserved_normal_ids = tuple(
                        int(row["id"])
                        for row in preserved_rows[
                            :_MIE_NORMAL_HISTORY_VALIDATION_LIMIT
                        ]
                    )
                effective_scan_ids.append(
                    (
                        int(latest["id"]),
                        preserved_normal_ids,
                        history_truncated,
                    )
                )

        findings: list[dict[str, Any]] = []
        for (
            latest_scan_id,
            preserved_normal_ids,
            history_truncated,
        ) in effective_scan_ids:
            detail = None
            for preserved_normal_id in preserved_normal_ids:
                preserved_detail = self.scan_detail(preserved_normal_id)
                if preserved_detail.get("snapshot_current"):
                    detail = preserved_detail
                    break
            if detail is None and history_truncated:
                latest_detail = self.scan_detail(latest_scan_id)
                file_row = latest_detail.get("file") or {}
                findings.append({
                    "fingerprint": (
                        "episode-identity-history:"
                        f"file:{int(latest_detail['file_id'])}:"
                        f"latest:{latest_scan_id}"
                    ),
                    "rule_key": "episode-identity-history-uncertain",
                    "category": "identity",
                    "severity": "information",
                    "root_id": file_row.get("root_id"),
                    "title_id": file_row.get("title_id"),
                    "file_id": latest_detail["file_id"],
                    "expected_episode_id": None,
                    "summary": (
                        f"{file_row.get('filename')}: Episode Identity history "
                        "needs a fresh Normal verification"
                    ),
                    "explanation": (
                        "InfoMancer found more matching historical Normal results "
                        "than it can safely revalidate in one Library Health pass. "
                        "The checked newer Normal results were stale, so it did not "
                        "silently fall back to a weaker Fast result."
                    ),
                    "recommendation": (
                        "Run Normal verification again to establish a fresh bounded "
                        "result before relying on the current identity warning state."
                    ),
                    "evidence": {
                        "latest_scan_id": latest_scan_id,
                        "normal_history_checked": len(preserved_normal_ids),
                        "normal_history_validation_limit": (
                            _MIE_NORMAL_HISTORY_VALIDATION_LIMIT
                        ),
                        "history_truncated": True,
                    },
                })
                continue
            if detail is None:
                latest_detail = self.scan_detail(latest_scan_id)
                if latest_detail.get("snapshot_current"):
                    detail = latest_detail
            if detail is None:
                # A scan describes frozen media and supporting-evidence inputs.
                # Once any of those inputs change, its old conclusion must not be
                # presented as evidence about the current file.
                continue
            scan_id = int(detail["id"])
            state = str(detail.get("result_state") or "")
            if state not in ACTIONABLE_STATES:
                continue
            file_row = detail.get("file") or {}
            best = detail.get("best_candidate")
            confirmation = detail.get("confirmation")
            claimed_keys = set(detail.get("claimed_candidate_keys") or [])

            if confirmation and confirmation.get("current"):
                confirmed_key = None
                for candidate in detail.get("candidates") or []:
                    if self._confirmation_matches_candidate(confirmation, candidate):
                        confirmed_key = str(candidate["candidate_key"])
                        break
                if confirmed_key in claimed_keys:
                    # Mark Correct explicitly overrides advisory mismatch/order warnings
                    # for this exact media snapshot without rewriting the algorithmic scan.
                    continue

            label = (
                best.get("display_name")
                if best and best.get("display_name")
                else "another episode"
            )
            coordinate = ""
            if best and best.get("season") is not None and best.get("episode") is not None:
                coordinate = f"S{int(best['season']):02d}E{int(best['episode']):02d}"
            claim = detail.get("claimed_identity") or {}
            claimed_code = ""
            if claim.get("season") is not None and claim.get("episode_start") is not None:
                claimed_code = (
                    f"S{int(claim['season']):02d}E{int(claim['episode_start']):02d}"
                )

            if state == IdentityResultState.EPISODE_ORDER_CONFLICT.value:
                summary = f"{file_row.get('filename')}: episode order differs"
                recommendation = (
                    "Compare the episode orders before changing the filename. The content identity itself is not treated as wrong."
                )
                severity = "information"
            else:
                summary = f"{file_row.get('filename')}: content may be {coordinate or label}"
                recommendation = (
                    "Review the evidence. Mark the current filename correct if intentional, confirm the suggested identity if appropriate, or preview a rename. InfoMancer will not rename the file automatically."
                )
                severity = (
                    "warning"
                    if state in {
                        IdentityResultState.LIKELY_MISMATCH.value,
                        IdentityResultState.STRONG_MATCH_OTHER.value,
                    }
                    else "information"
                )

            best_resolution = (
                (best.get("details") or {}).get("resolution")
                if best else {}
            ) or {}
            decision_digest = str(
                detail.get("decision_snapshot_sha256") or ""
            ).strip().casefold()
            findings.append({
                "fingerprint": (
                    f"episode-identity:file:{int(detail['file_id'])}:"
                    f"decision:{decision_digest}"
                ),
                "rule_key": "episode-identity-review",
                "category": "identity",
                "severity": severity,
                "root_id": file_row.get("root_id"),
                "title_id": file_row.get("title_id"),
                "file_id": detail["file_id"],
                "expected_episode_id": best.get("expected_episode_id") if best else None,
                "summary": summary,
                "explanation": detail.get("resolution_explanation") or (
                    "Episode Identity evidence needs review."
                ),
                "recommendation": recommendation,
                "evidence": {
                    "scan_id": scan_id,
                    "result_state": state,
                    "result_revision": int(detail.get("result_revision") or 0),
                    "decision_snapshot_sha256": decision_digest,
                    "profile": detail.get("completed_profile") or detail.get("requested_profile"),
                    "claimed_episode": claimed_code or "Not recorded",
                    "best_candidate": (
                        f"{coordinate} {label}".strip() if best else "No candidate"
                    ),
                    "best_candidate_support": (
                        _strength_label(best.get("support_strength")) if best else "None"
                    ),
                    "conflicting_evidence": (
                        _strength_label(best.get("conflict_strength")) if best else "None"
                    ),
                    "independent_categories": best.get("independent_categories") if best else 0,
                    "candidate_separation": _separation_label(detail.get("margin")),
                    "support_categories": best_resolution.get("support_categories", []),
                    "confirmation": (
                        confirmation.get("freshness") if confirmation else "none"
                    ),
                },
            })
        return findings

    def rename_preview(
        self,
        scan_id: int,
        *,
        expected_result_revision: int,
        expected_decision_snapshot_sha256: str,
        expected_candidate_key: str,
    ) -> dict[str, Any]:
        detail = self.scan_detail(
            int(scan_id),
            verify_actionable_content=False,
            include_confirmation=False,
            verify_confirmation_external=False,
        )
        reviewed_revision = int(expected_result_revision)
        reviewed_digest = str(
            expected_decision_snapshot_sha256 or ""
        ).strip().casefold()
        reviewed_candidate_key = str(expected_candidate_key or "").strip()
        if detail.get("decision_pending"):
            return {
                "available": False,
                "status": "unavailable",
                "reason": (
                    "This scan is still waiting for the decision resolver. "
                    "Refresh Library Health before previewing a rename."
                ),
                "scan": detail,
            }
        displayed_result_matches = (
            reviewed_revision > 0
            and len(reviewed_digest) == 64
            and all(
                character in "0123456789abcdef"
                for character in reviewed_digest
            )
            and bool(reviewed_candidate_key)
            and int(detail.get("result_revision") or 0) == reviewed_revision
            and str(
                detail.get("decision_snapshot_sha256") or ""
            ).strip().casefold() == reviewed_digest
            and str(detail.get("best_candidate_key") or "")
            == reviewed_candidate_key
        )
        if not displayed_result_matches:
            stale_detail = dict(detail)
            stale_detail["snapshot_current"] = False
            stale_detail["actionable"] = False
            return {
                "available": False,
                "status": "stale",
                "reason": (
                    "The Episode Identity result changed after this page was reviewed. "
                    "Refresh the scan before previewing a rename."
                ),
                "scan": stale_detail,
            }
        if not detail.get("snapshot_current"):
            return {
                "available": False,
                "status": "stale",
                "reason": "The media or supporting evidence changed after verification. Run Episode Identity again before considering a rename.",
                "scan": detail,
            }
        if not detail.get("actionable"):
            return {
                "available": False,
                "status": "unavailable",
                "reason": "This identity result does not support a rename suggestion.",
                "scan": detail,
            }
        best = detail.get("best_candidate")
        file_row = detail.get("file") or {}
        if not best or not file_row:
            return {
                "available": False,
                "status": "unavailable",
                "reason": "The suggested episode or media file is unavailable.",
                "scan": detail,
            }
        claim = detail.get("claimed_identity") or {}
        try:
            start = int(claim.get("episode_start"))
            end = int(claim.get("episode_end") or start)
        except (TypeError, ValueError):
            start = end = 0
        if end != start:
            return {
                "available": False,
                "status": "unavailable",
                "reason": "Multi-episode files require manual review before any rename suggestion.",
                "scan": detail,
            }

        mappings = (best.get("details") or {}).get("mappings") or []
        default_mapping = next(
            (
                mapping for mapping in mappings
                if isinstance(mapping, Mapping)
                and str(mapping.get("order_namespace") or "") == "default"
                and mapping.get("season") is not None
                and mapping.get("episode") is not None
            ),
            None,
        )
        target_season = (
            default_mapping.get("season") if default_mapping else best.get("season")
        )
        target_episode = (
            default_mapping.get("episode") if default_mapping else best.get("episode")
        )
        if target_season is None or target_episode is None:
            return {
                "available": False,
                "status": "unavailable",
                "reason": "The suggested identity has no usable episode coordinate.",
                "scan": detail,
            }

        with self.database.connect() as conn:
            scan, _, evidence = self._scan_snapshot(conn, int(scan_id))
            current_claimed = self._claimed_identity(scan)
            current_revision, current_digest = self._decision_token(
                current_claimed
            )
            same_reviewed_result = (
                reviewed_revision > 0
                and bool(reviewed_digest)
                and current_revision == reviewed_revision
                and current_digest == reviewed_digest
            )
            current = False
            if same_reviewed_result:
                current, _ = self._review_snapshot_is_current(
                    conn,
                    scan,
                    evidence,
                    verify_content=True,
                )
        if not current:
            stale_detail = dict(detail)
            stale_detail["snapshot_current"] = False
            stale_detail["actionable"] = False
            return {
                "available": False,
                "status": "stale",
                "reason": (
                    "The Episode Identity result, exact media content, or supporting "
                    "evidence changed after review."
                ),
                "scan": stale_detail,
            }

        validated_source = {
            "current": True,
            "content_verified": True,
            "scan_id": int(scan["id"]),
            "file_id": int(scan["file_id"]),
            "result_revision": current_revision,
            "decision_snapshot_sha256": current_digest,
            "metadata_signature": str(scan.get("metadata_signature") or ""),
            "file_size_bytes": int(scan.get("file_size_bytes") or 0),
            "file_modified_at": scan.get("file_modified_at"),
            "file_sha256": str(scan.get("file_sha256") or ""),
        }
        confirmation = self.confirmation_status(
            int(scan["file_id"]),
            validated_source=validated_source,
        )
        confirmed_key = None
        if confirmation and confirmation.get("current"):
            for candidate in detail.get("candidates") or []:
                if self._confirmation_matches_candidate(
                    confirmation,
                    candidate,
                ):
                    confirmed_key = str(candidate["candidate_key"])
                    break
        confirmed_claimed = (
            confirmed_key is not None
            and confirmed_key in set(
                detail.get("claimed_candidate_keys") or []
            )
        )
        detail = dict(detail)
        detail["confirmation"] = confirmation
        detail["confirmed_claimed"] = confirmed_claimed
        detail["snapshot_current"] = True
        detail["actionable"] = (
            not confirmed_claimed
            and str(detail.get("result_state") or "") in ACTIONABLE_STATES
        )
        if confirmed_claimed:
            return {
                "available": False,
                "status": "unavailable",
                "reason": "The current filename was marked correct for this media snapshot, so no alternate rename is suggested.",
                "scan": detail,
            }

        source = Path(str(file_row["path"]))
        raw_extension = str(file_row.get("extension") or "").strip()
        extension = (
            raw_extension
            if raw_extension.startswith(".")
            else f".{raw_extension}" if raw_extension
            else source.suffix
        )
        new_name = plex_episode_filename(
            str(file_row.get("title_name") or ""),
            file_row.get("metadata_year") or file_row.get("year"),
            int(target_season),
            int(target_episode),
            str(best.get("display_name") or ""),
            extension,
            None,
        )
        try:
            destination = contained_destination(source, new_name)
        except ValueError as exc:
            return {
                "available": False,
                "status": "blocked",
                "reason": str(exc),
                "scan": detail,
            }

        if not current:
            status, reason = (
                "stale",
                "The media snapshot changed after verification. Run Episode Identity again before considering a rename.",
            )
        elif destination == source:
            status, reason = "unchanged", "The file already has the suggested name."
        elif destination.exists():
            status, reason = (
                "conflict",
                "A file already exists at the suggested destination. Nothing will be overwritten.",
            )
        else:
            status, reason = (
                "ready",
                "This is a read-only preview. Applying a rename still requires the existing validated rename workflow after the catalog identity is corrected.",
            )
        return {
            "available": status in {"ready", "unchanged"},
            "status": status,
            "reason": reason,
            "source": str(source),
            "destination": str(destination),
            "target_season": int(target_season),
            "target_episode": int(target_episode),
            "target_name": str(best.get("display_name") or ""),
            "scan": detail,
        }

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from ..db import Database
from ..naming import contained_destination, plex_episode_filename
from .candidates import generate_episode_candidates
from .fast import (
    SCAN_INPUT_SIGNATURE_VERSION,
    combined_scan_input_signature,
    scan_input_signatures,
)
from .models import IdentityResultState
from .scoring import IdentityResolution, resolve_identity
from .text import discover_sidecar_subtitles, sidecar_identity
from .versions import (
    EPISODE_IDENTITY_DECISION_ALGORITHM_VERSION,
    NORMAL_EVIDENCE_ALGORITHM_VERSION,
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

    def __init__(self, database: Database) -> None:
        self.database = database

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
            resolution = self._resolve_snapshot(scan, candidates, evidence)
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
        if sha256:
            current_hash = MediaIdentityDecisionService._current_hash(
                conn, int(file_id), int(size_bytes), modified_at
            )
            if current_hash != sha256:
                return False, current
        return True, current

    @staticmethod
    def _scan_snapshot_is_current(
        conn: sqlite3.Connection,
        scan: Mapping[str, Any],
        evidence: list[dict[str, Any]],
    ) -> tuple[bool, dict[str, Any] | None]:
        current, file_row = MediaIdentityDecisionService._snapshot_is_current(
            conn,
            int(scan["file_id"]),
            size_bytes=int(scan["file_size_bytes"] or 0),
            modified_at=scan["file_modified_at"],
            sha256=scan["file_sha256"],
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
                transcript_count = int(
                    speech_metadata.get("transcript_count") or 0
                )
            except (TypeError, ValueError):
                return False, file_row
            if transcript_count < 0:
                return False, file_row

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
                               start_ms,end_ms,payload_json
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

        return True, file_row

    def confirmation_status(self, file_id: int) -> dict[str, Any] | None:
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT * FROM media_identity_confirmations WHERE file_id=?""",
                (int(file_id),),
            ).fetchone()
            if not row:
                return None
            confirmation = dict(row)
            current, _ = self._snapshot_is_current(
                conn,
                int(file_id),
                size_bytes=int(confirmation["confirmed_size_bytes"] or 0),
                modified_at=confirmation["confirmed_modified_at"],
                sha256=confirmation["confirmed_sha256"],
            )
            source_scan_id = confirmation.get("source_scan_id")
            if current and source_scan_id is not None:
                try:
                    scan, _, evidence = self._scan_snapshot(
                        conn, int(source_scan_id)
                    )
                except MediaIdentityDecisionError:
                    current = False
                else:
                    current, _ = self._scan_snapshot_is_current(
                        conn,
                        scan,
                        evidence,
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
    ) -> dict[str, Any]:
        with self.database.connect() as conn:
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            scan, candidates, evidence = self._scan_snapshot(conn, int(scan_id))
            if scan["status"] != "complete":
                raise MediaIdentityDecisionError(
                    "Only a complete Episode Identity scan can be confirmed."
                )
            candidate = self._candidate_for_key(candidates, candidate_key)
            current, _ = self._scan_snapshot_is_current(
                conn,
                scan,
                evidence,
            )
            if not current:
                raise MediaIdentityDecisionError(
                    "The media file or supporting evidence changed after this identity scan. Run verification again before confirming it."
                )
            conn.execute(
                """INSERT INTO media_identity_confirmations(
                     file_id,identity_kind,provider,provider_item_id,
                     expected_episode_id,order_namespace,season,episode,display_name,
                     source_scan_id,confirmed_size_bytes,confirmed_modified_at,
                     confirmed_sha256,confirmed_by,confirmed_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
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
                    int(scan["file_size_bytes"] or 0),
                    scan["file_modified_at"],
                    scan["file_sha256"],
                    user_id if user_id and int(user_id) > 0 else None,
                ),
            )
        status = self.confirmation_status(int(scan["file_id"]))
        if status is None:
            raise MediaIdentityDecisionError("Episode Identity confirmation was not saved.")
        return status

    def confirm_current(self, scan_id: int, user_id: int | None) -> dict[str, Any]:
        detail = self.scan_detail(int(scan_id), resolve_if_needed=True)
        claimed_keys = list(detail.get("claimed_candidate_keys") or [])
        if len(claimed_keys) != 1:
            raise MediaIdentityDecisionError(
                "The current filename does not map to exactly one content candidate, so InfoMancer cannot mark it correct safely."
            )
        return self._confirm(int(scan_id), claimed_keys[0], user_id)

    def confirm_best(self, scan_id: int, user_id: int | None) -> dict[str, Any]:
        detail = self.scan_detail(int(scan_id), resolve_if_needed=True)
        if str(detail.get("result_state") or "") not in SUGGESTED_CONFIRM_STATES:
            raise MediaIdentityDecisionError(
                "This scan is not strong enough to confirm an alternate episode."
            )
        candidate_key = str(detail.get("best_candidate_key") or "")
        if not candidate_key or candidate_key in set(
            detail.get("claimed_candidate_keys") or []
        ):
            raise MediaIdentityDecisionError(
                "This scan has no distinct alternate episode to confirm."
            )
        return self._confirm(int(scan_id), candidate_key, user_id)

    def scan_detail(
        self,
        scan_id: int,
        *,
        resolve_if_needed: bool = False,
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
            snapshot_current, _ = self._scan_snapshot_is_current(
                conn,
                scan,
                evidence,
            )
        claimed = self._claimed_identity(scan)
        resolution = self._resolve_snapshot(scan, candidates, evidence)
        candidate_by_key = {
            str(item["candidate_key"]): item for item in candidates
        }
        best = candidate_by_key.get(str(scan.get("best_candidate_key") or ""))
        confirmation = self.confirmation_status(int(scan["file_id"]))
        result = dict(scan)
        result["claimed_identity"] = claimed
        result.pop("claimed_identity_json", None)
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
            scan_ids = [
                int(row["id"])
                for row in conn.execute(
                    """SELECT s.id
                       FROM media_identity_scans s
                       WHERE s.status='complete' AND s.result_state IS NOT NULL
                         AND s.id=(
                           SELECT MAX(latest.id) FROM media_identity_scans latest
                           WHERE latest.file_id=s.file_id AND latest.status='complete'
                         )
                       ORDER BY s.id"""
                ).fetchall()
            ]

        findings: list[dict[str, Any]] = []
        for scan_id in scan_ids:
            detail = self.scan_detail(scan_id)
            if not detail.get("snapshot_current"):
                # A scan describes frozen media and supporting-evidence inputs.
                # Once any of those inputs change, its old conclusion must not be
                # presented as evidence about the current file.
                continue
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
            findings.append({
                "fingerprint": (
                    f"episode-identity:file:{int(detail['file_id'])}:"
                    f"{detail.get('metadata_signature') or scan_id}"
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

    def rename_preview(self, scan_id: int) -> dict[str, Any]:
        detail = self.scan_detail(int(scan_id))
        if not detail.get("snapshot_current"):
            return {
                "available": False,
                "status": "stale",
                "reason": "The media or supporting evidence changed after verification. Run Episode Identity again before considering a rename.",
                "scan": detail,
            }
        if detail.get("confirmed_claimed"):
            return {
                "available": False,
                "status": "unavailable",
                "reason": "The current filename was marked correct for this media snapshot, so no alternate rename is suggested.",
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
            current, _ = self._scan_snapshot_is_current(
                conn,
                scan,
                evidence,
            )
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

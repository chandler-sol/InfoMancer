from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable

from ..db import Database
from .candidates import CandidateSet, generate_episode_candidates
from .models import (
    EvidenceCategory,
    EvidenceRelation,
    IdentityCandidate,
    IdentityEvidence,
    IdentityProfile,
)
from .text import (
    SidecarIdentity,
    SidecarText,
    TextCorpus,
    combined_synopsis_similarity,
    discover_sidecar_subtitles,
    read_sidecar_text,
    sidecar_identity,
    text_corpus,
)


TEXT_SUPPORT_THRESHOLD = 0.30
SPECIAL_EXPANSION_THRESHOLD = 0.20
SIDECAR_ANALYZER_KEY = "sidecar-subtitle"
SIDECAR_ANALYZER_VERSION = "1"


class FastIdentityScanError(RuntimeError):
    """Raised when a Fast scan cannot safely produce a complete evidence snapshot."""


class FastIdentityStaleError(FastIdentityScanError):
    """Raised when file or sidecar freshness changes during a scan."""


@dataclass(frozen=True)
class PreparedSidecar:
    identity: SidecarIdentity
    text: SidecarText
    reused_artifact_id: int | None = None


@dataclass(frozen=True)
class FastScanResult:
    scan_id: int
    file_id: int
    candidate_count: int
    evidence_count: int
    artifact_count: int
    reused_artifact_count: int
    provider_cache_used: bool
    expanded_specials: bool


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _signature(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _same_modified_at(first: Any, second: Any) -> bool:
    if first is None or second is None:
        return first is None and second is None
    return abs(float(first) - float(second)) <= 0.001


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _filename_text(value: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", value.casefold()).split())


class FastIdentityService:
    """Gather and persist cheap, read-only Episode Identity evidence.

    Fast deliberately does not decide whether the file is mislabeled. It creates one
    atomic evidence snapshot for the later conservative resolver.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _file_row(conn: sqlite3.Connection, file_id: int) -> dict[str, Any]:
        row = conn.execute(
            """SELECT f.*,t.kind AS title_kind,t.tvdb_id,
                      COALESCE(t.metadata_title,t.title) AS title_name
               FROM files f
               JOIN titles t ON t.id=f.title_id
               WHERE f.id=?""",
            (int(file_id),),
        ).fetchone()
        if not row:
            raise FastIdentityScanError("Episode Identity file was not found in the catalog.")
        result = dict(row)
        if result["title_kind"] != "tv":
            raise FastIdentityScanError("Fast Episode Identity scans require a TV file.")
        if result["season"] is None or result["episode_start"] is None:
            raise FastIdentityScanError(
                "Fast Episode Identity scans require a parsed season and episode claim."
            )
        return result

    @staticmethod
    def _stream_rows(conn: sqlite3.Connection, file_id: int) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in conn.execute(
                """SELECT stream_index,stream_type,codec,language,title,channels,
                          channel_layout,sample_rate,default_flag,forced_flag,
                          hearing_impaired,visual_impaired,commentary,disposition_json
                   FROM media_streams
                   WHERE file_id=? ORDER BY stream_index""",
                (int(file_id),),
            ).fetchall()
        ]

    @staticmethod
    def _current_sha256(conn: sqlite3.Connection, file_row: dict[str, Any]) -> str | None:
        row = conn.execute(
            """SELECT sha256,size_bytes,modified_at,status
               FROM media_file_hashes WHERE file_id=?""",
            (int(file_row["id"]),),
        ).fetchone()
        if not row or row["status"] != "complete" or not row["sha256"]:
            return None
        if int(row["size_bytes"] or 0) != int(file_row["size_bytes"] or 0):
            return None
        if not _same_modified_at(row["modified_at"], file_row["modified_at"]):
            return None
        return str(row["sha256"])

    @staticmethod
    def _verify_media_snapshot(file_row: dict[str, Any]) -> None:
        path = Path(str(file_row["path"]))
        try:
            if not path.is_file():
                raise FastIdentityScanError(
                    "The media file is unavailable. InfoMancer did not create an identity scan."
                )
            stat = path.stat()
        except OSError as exc:
            raise FastIdentityScanError(
                "The media file could not be read. InfoMancer did not create an identity scan."
            ) from exc
        if int(stat.st_size) != int(file_row["size_bytes"] or 0):
            raise FastIdentityStaleError(
                "The media file size changed since the catalog scan. Rescan the source and retry."
            )
        modified_at = file_row.get("modified_at")
        if modified_at is not None and not _same_modified_at(stat.st_mtime, modified_at):
            raise FastIdentityStaleError(
                "The media file modification time changed since the catalog scan. "
                "Rescan the source and retry."
            )

    @staticmethod
    def _cached_sidecar(
        conn: sqlite3.Connection,
        file_row: dict[str, Any],
        identity: SidecarIdentity,
    ) -> tuple[int, SidecarText] | None:
        row = conn.execute(
            """SELECT id,text_value,source_signature,source_ref
               FROM media_identity_artifacts
               WHERE file_id=? AND artifact_type='subtitle_text'
                 AND analyzer_key=? AND analyzer_version=? AND cache_key=?
                 AND status='complete' AND file_size_bytes=?
                 AND COALESCE(file_modified_at,-1)=COALESCE(?,-1)
               ORDER BY id DESC LIMIT 1""",
            (
                int(file_row["id"]),
                SIDECAR_ANALYZER_KEY,
                SIDECAR_ANALYZER_VERSION,
                identity.cache_key,
                int(file_row["size_bytes"] or 0),
                file_row["modified_at"],
            ),
        ).fetchone()
        if not row or str(row["source_signature"] or "") != identity.source_signature:
            return None
        return (
            int(row["id"]),
            SidecarText(
                path=identity.path,
                source_signature=identity.source_signature,
                cache_key=identity.cache_key,
                normalized_text=str(row["text_value"] or ""),
            ),
        )

    def _prepare_sidecars(self, file_row: dict[str, Any]) -> list[PreparedSidecar]:
        prepared: list[PreparedSidecar] = []
        with self.database.connect() as conn:
            for path in discover_sidecar_subtitles(str(file_row["path"])):
                identity = sidecar_identity(path)
                if identity is None:
                    continue
                cached = self._cached_sidecar(conn, file_row, identity)
                if cached is not None:
                    artifact_id, text = cached
                    prepared.append(PreparedSidecar(identity, text, artifact_id))
                    continue
                text = read_sidecar_text(path, identity)
                if text is not None:
                    prepared.append(PreparedSidecar(identity, text, None))
        return prepared

    @staticmethod
    def _verify_sidecars(prepared: Iterable[PreparedSidecar]) -> None:
        for item in prepared:
            current = sidecar_identity(item.identity.path)
            if current is None or current.cache_key != item.identity.cache_key:
                raise FastIdentityStaleError(
                    "A subtitle sidecar changed during the Fast scan. Retry the scan."
                )

    @staticmethod
    def _candidate_score(
        candidate: IdentityCandidate,
        sidecars: list[PreparedSidecar],
        corpora: dict[str, TextCorpus],
    ) -> tuple[float, str]:
        overview = str(candidate.details.get("overview") or "").strip()
        if not overview or not sidecars:
            return 0.0, ""
        return combined_synopsis_similarity(
            [item.text for item in sidecars],
            overview,
            corpora,
        )

    def _candidate_set(
        self,
        file_row: dict[str, Any],
        sidecars: list[PreparedSidecar],
        language: str,
    ) -> tuple[CandidateSet, bool, dict[str, TextCorpus]]:
        season = int(file_row["season"])
        episode_start = int(file_row["episode_start"])
        episode_end = int(file_row["episode_end"] or episode_start)
        corpora = {
            item.text.cache_key: text_corpus(item.text.normalized_text)
            for item in sidecars
            if item.text.normalized_text
        }
        with self.database.connect() as conn:
            candidate_set = generate_episode_candidates(
                conn,
                title_id=int(file_row["title_id"]),
                season=season,
                episode_start=episode_start,
                episode_end=episode_end,
                include_specials=season == 0,
                language=language,
            )
            if not candidate_set.candidates:
                raise FastIdentityScanError(
                    "No episode candidates are available for this TV file. Refresh metadata and retry."
                )

            expanded_specials = False
            if (
                season != 0
                and candidate_set.used_provider_cache
                and corpora
            ):
                best_regular = max(
                    (
                        self._candidate_score(candidate, sidecars, corpora)[0]
                        for candidate in candidate_set.candidates
                    ),
                    default=0.0,
                )
                if best_regular < SPECIAL_EXPANSION_THRESHOLD:
                    expanded = generate_episode_candidates(
                        conn,
                        title_id=int(file_row["title_id"]),
                        season=season,
                        episode_start=episode_start,
                        episode_end=episode_end,
                        include_specials=True,
                        language=language,
                    )
                    if len(expanded.candidates) > len(candidate_set.candidates):
                        candidate_set = expanded
                        expanded_specials = True
        return candidate_set, expanded_specials, corpora

    @staticmethod
    def _claimed_evidence(
        file_row: dict[str, Any],
        candidates: Iterable[IdentityCandidate],
    ) -> list[IdentityEvidence]:
        season = int(file_row["season"])
        start = int(file_row["episode_start"])
        end = int(file_row["episode_end"] or start)
        claim = f"S{season:02d}E{start:02d}"
        if end != start:
            claim += f"-E{end:02d}"
        correlation = f"catalog-claim:{file_row['id']}:{season}:{start}:{end}"
        evidence: list[IdentityEvidence] = []
        filename_text = _filename_text(str(file_row["filename"] or ""))

        for candidate in candidates:
            origins = set(candidate.details.get("origins") or [])
            if "claimed_coordinate" in origins:
                evidence.append(IdentityEvidence(
                    analyzer_key="catalog-claim",
                    analyzer_version="1",
                    category=EvidenceCategory.CLAIMED_IDENTITY,
                    relation=EvidenceRelation.SUPPORTS,
                    strength=0.35,
                    correlation_group=correlation,
                    candidate_key=candidate.key,
                    source_kind="catalog_filename",
                    source_ref=str(file_row["path"]),
                    value=claim,
                    details={
                        "season": season,
                        "episode_start": start,
                        "episode_end": end,
                        "filename": str(file_row["filename"] or ""),
                    },
                ))

            episode_name = _filename_text(candidate.identity.display_name)
            if len(episode_name) >= 6 and len(episode_name.split()) >= 2 and episode_name in filename_text:
                evidence.append(IdentityEvidence(
                    analyzer_key="catalog-claim",
                    analyzer_version="1",
                    category=EvidenceCategory.PROVIDER_METADATA,
                    relation=EvidenceRelation.SUPPORTS,
                    strength=0.25,
                    correlation_group=correlation,
                    candidate_key=candidate.key,
                    source_kind="filename_episode_title",
                    source_ref=str(file_row["path"]),
                    value=candidate.identity.display_name,
                    details={"filename": str(file_row["filename"] or "")},
                ))

        if not any(item.category == EvidenceCategory.CLAIMED_IDENTITY for item in evidence):
            evidence.append(IdentityEvidence(
                analyzer_key="catalog-claim",
                analyzer_version="1",
                category=EvidenceCategory.CLAIMED_IDENTITY,
                relation=EvidenceRelation.NEUTRAL,
                strength=0.0,
                correlation_group=correlation,
                source_kind="catalog_filename",
                source_ref=str(file_row["path"]),
                value=claim,
                details={"candidate_mapping_found": False},
            ))
        return evidence

    @staticmethod
    def _container_evidence(
        file_row: dict[str, Any],
        streams: list[dict[str, Any]],
        candidates: Iterable[IdentityCandidate],
    ) -> list[IdentityEvidence]:
        correlation = f"container-metadata:{file_row['id']}:{file_row.get('media_info_at') or 'catalog'}"
        compact_streams = [
            {
                "index": stream.get("stream_index"),
                "type": stream.get("stream_type"),
                "codec": stream.get("codec"),
                "language": stream.get("language"),
                "title": stream.get("title"),
                "channels": stream.get("channels"),
                "default": bool(stream.get("default_flag")),
                "forced": bool(stream.get("forced_flag")),
            }
            for stream in streams
        ]
        details = {
            "runtime_seconds": file_row.get("runtime_seconds"),
            "width": file_row.get("width"),
            "height": file_row.get("height"),
            "video_codec": file_row.get("video_codec"),
            "audio_codec": file_row.get("audio_codec"),
            "audio_channels": file_row.get("audio_channels"),
            "bitrate": file_row.get("bitrate"),
            "container": file_row.get("container"),
            "dynamic_range": file_row.get("dynamic_range"),
            "media_info_at": file_row.get("media_info_at"),
            "media_info_error": file_row.get("media_info_error"),
            "streams": compact_streams,
            "embedded_subtitle_text_extracted": False,
        }
        evidence = [IdentityEvidence(
            analyzer_key="catalog-media-info",
            analyzer_version="1",
            category=EvidenceCategory.CONTAINER_METADATA,
            relation=EvidenceRelation.NEUTRAL,
            strength=0.0,
            correlation_group=correlation,
            source_kind="ffprobe_catalog",
            source_ref=f"file:{file_row['id']}",
            value=(
                f"{file_row.get('container') or 'unknown'}; "
                f"{len(streams)} persisted stream(s)"
            ),
            details=details,
        )]

        runtime_seconds = _optional_float(file_row.get("runtime_seconds"))
        if runtime_seconds and runtime_seconds > 0:
            for candidate in candidates:
                metadata = candidate.details.get("metadata") or {}
                expected_minutes = _optional_float(metadata.get("runtime"))
                if not expected_minutes or expected_minutes <= 0:
                    continue
                expected_seconds = expected_minutes * 60.0
                delta = abs(runtime_seconds - expected_seconds) / expected_seconds
                relation = (
                    EvidenceRelation.SUPPORTS
                    if delta <= 0.08
                    else EvidenceRelation.NEUTRAL
                )
                strength = 0.18 if relation == EvidenceRelation.SUPPORTS else 0.0
                evidence.append(IdentityEvidence(
                    analyzer_key="runtime-metadata",
                    analyzer_version="1",
                    category=EvidenceCategory.CONTAINER_METADATA,
                    relation=relation,
                    strength=strength,
                    correlation_group=f"runtime:{file_row['id']}",
                    candidate_key=candidate.key,
                    source_kind="catalog_runtime",
                    source_ref=f"file:{file_row['id']}",
                    value=f"{runtime_seconds:.3f}s vs {expected_minutes:g}m",
                    details={
                        "runtime_seconds": runtime_seconds,
                        "provider_runtime_minutes": expected_minutes,
                        "relative_delta": round(delta, 6),
                    },
                ))
        return evidence

    def _subtitle_evidence(
        self,
        file_row: dict[str, Any],
        candidates: Iterable[IdentityCandidate],
        sidecars: list[PreparedSidecar],
        corpora: dict[str, TextCorpus],
    ) -> list[IdentityEvidence]:
        correlation = f"subtitle-dialogue:{file_row['id']}"
        usable = [item for item in sidecars if item.text.normalized_text]
        if not usable:
            return [IdentityEvidence(
                analyzer_key="subtitle-synopsis",
                analyzer_version="1",
                category=EvidenceCategory.SUBTITLE_TEXT,
                relation=EvidenceRelation.NEUTRAL,
                strength=0.0,
                correlation_group=correlation,
                source_kind="sidecar_subtitle_discovery",
                source_ref=str(file_row["path"]),
                value="No supported sidecar subtitle dialogue was available.",
                details={"sidecar_count": len(sidecars)},
            )]

        by_path = {str(item.text.path): item for item in usable}
        evidence: list[IdentityEvidence] = []
        comparable = 0
        for candidate in candidates:
            overview = str(candidate.details.get("overview") or "").strip()
            if not overview:
                continue
            comparable += 1
            score, source = self._candidate_score(candidate, usable, corpora)
            selected = by_path.get(source)
            relation = (
                EvidenceRelation.SUPPORTS
                if score >= TEXT_SUPPORT_THRESHOLD
                else EvidenceRelation.NEUTRAL
            )
            evidence.append(IdentityEvidence(
                analyzer_key="subtitle-synopsis",
                analyzer_version="1",
                category=EvidenceCategory.SUBTITLE_TEXT,
                relation=relation,
                strength=score if relation == EvidenceRelation.SUPPORTS else 0.0,
                correlation_group=correlation,
                candidate_key=candidate.key,
                source_kind="sidecar_subtitle",
                source_ref=source,
                value=f"synopsis similarity {score:.3f}",
                details={
                    "similarity": score,
                    "support_threshold": TEXT_SUPPORT_THRESHOLD,
                    "source_cache_key": selected.text.cache_key if selected else "",
                    "source_signature": selected.text.source_signature if selected else "",
                    "provider_overview": overview[:1000],
                },
                cache_key=selected.text.cache_key if selected else "",
            ))

        if comparable == 0:
            evidence.append(IdentityEvidence(
                analyzer_key="subtitle-synopsis",
                analyzer_version="1",
                category=EvidenceCategory.SUBTITLE_TEXT,
                relation=EvidenceRelation.NEUTRAL,
                strength=0.0,
                correlation_group=correlation,
                source_kind="provider_metadata",
                source_ref=f"title:{file_row['title_id']}",
                value="Subtitle dialogue was available, but candidate synopses were unavailable.",
                details={"sidecar_count": len(usable)},
            ))
        return evidence

    @staticmethod
    def _metadata_signature(
        file_row: dict[str, Any],
        candidate_set: CandidateSet,
        streams: list[dict[str, Any]],
        sidecars: list[PreparedSidecar],
    ) -> str:
        return _signature({
            "file": {
                "id": file_row["id"],
                "size_bytes": file_row.get("size_bytes"),
                "modified_at": file_row.get("modified_at"),
                "season": file_row.get("season"),
                "episode_start": file_row.get("episode_start"),
                "episode_end": file_row.get("episode_end"),
                "runtime_seconds": file_row.get("runtime_seconds"),
                "container": file_row.get("container"),
                "video_codec": file_row.get("video_codec"),
                "audio_codec": file_row.get("audio_codec"),
                "media_info_at": file_row.get("media_info_at"),
            },
            "provider_signature": candidate_set.provider_signature,
            "streams": streams,
            "sidecars": [item.identity.source_signature for item in sidecars],
        })

    @staticmethod
    def _persist_artifacts(
        conn: sqlite3.Connection,
        file_row: dict[str, Any],
        prepared: list[PreparedSidecar],
    ) -> list[int]:
        artifact_ids: list[int] = []
        for item in prepared:
            if item.reused_artifact_id is not None:
                conn.execute(
                    "UPDATE media_identity_artifacts SET last_used_at=CURRENT_TIMESTAMP WHERE id=?",
                    (item.reused_artifact_id,),
                )
                artifact_ids.append(item.reused_artifact_id)
                continue

            conn.execute(
                """INSERT OR IGNORE INTO media_identity_artifacts(
                     file_id,artifact_type,analyzer_key,analyzer_version,cache_key,
                     status,profile,source_kind,source_ref,source_signature,
                     file_size_bytes,file_modified_at,text_value,payload_json,
                     updated_at,last_used_at
                   ) VALUES (?,?,?,?,?,'complete','fast','sidecar_subtitle',?,?,?, ?,?,?,
                             CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                (
                    int(file_row["id"]),
                    "subtitle_text",
                    SIDECAR_ANALYZER_KEY,
                    SIDECAR_ANALYZER_VERSION,
                    item.text.cache_key,
                    str(item.text.path),
                    item.text.source_signature,
                    int(file_row["size_bytes"] or 0),
                    file_row["modified_at"],
                    item.text.normalized_text,
                    _canonical_json({
                        "normalizer": "subtitle-normalizer-v1",
                        "characters": len(item.text.normalized_text),
                    }),
                ),
            )
            row = conn.execute(
                """SELECT id FROM media_identity_artifacts
                   WHERE file_id=? AND artifact_type='subtitle_text'
                     AND analyzer_key=? AND analyzer_version=? AND cache_key=?
                   ORDER BY id DESC LIMIT 1""",
                (
                    int(file_row["id"]),
                    SIDECAR_ANALYZER_KEY,
                    SIDECAR_ANALYZER_VERSION,
                    item.text.cache_key,
                ),
            ).fetchone()
            if not row:
                raise FastIdentityScanError("Fast subtitle artifact persistence failed.")
            artifact_id = int(row["id"])
            conn.execute(
                "UPDATE media_identity_artifacts SET last_used_at=CURRENT_TIMESTAMP WHERE id=?",
                (artifact_id,),
            )
            artifact_ids.append(artifact_id)
        return artifact_ids

    @staticmethod
    def _persist_candidates(
        conn: sqlite3.Connection,
        scan_id: int,
        candidates: Iterable[IdentityCandidate],
    ) -> None:
        conn.executemany(
            """INSERT INTO media_identity_candidates(
                 scan_id,candidate_key,identity_kind,provider,provider_item_id,
                 expected_episode_id,order_namespace,season,episode,display_name,
                 rank,score,support_strength,conflict_strength,independent_categories,
                 details_json
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    scan_id,
                    candidate.key,
                    candidate.identity.identity_kind,
                    candidate.identity.provider,
                    candidate.identity.provider_item_id,
                    candidate.identity.expected_episode_id,
                    candidate.identity.order_namespace,
                    candidate.identity.season,
                    candidate.identity.episode,
                    candidate.identity.display_name,
                    candidate.rank,
                    candidate.score,
                    candidate.support_strength,
                    candidate.conflict_strength,
                    candidate.independent_categories,
                    _canonical_json(candidate.details),
                )
                for candidate in candidates
            ],
        )

    @staticmethod
    def _persist_evidence(
        conn: sqlite3.Connection,
        scan_id: int,
        evidence: Iterable[IdentityEvidence],
    ) -> int:
        rows = list(evidence)
        conn.executemany(
            """INSERT INTO media_identity_evidence(
                 scan_id,candidate_key,analyzer_key,analyzer_version,
                 evidence_category,correlation_group,relation,strength,
                 source_kind,source_ref,timestamp_ms,value_text,details_json,
                 cache_key,profile
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    scan_id,
                    item.candidate_key,
                    item.analyzer_key,
                    item.analyzer_version,
                    item.category.value,
                    item.correlation_group,
                    item.relation.value,
                    float(item.strength),
                    item.source_kind,
                    item.source_ref,
                    item.timestamp_ms,
                    item.value,
                    _canonical_json(item.details),
                    item.cache_key,
                    item.profile.value,
                )
                for item in rows
            ],
        )
        return len(rows)

    def scan_file(
        self,
        file_id: int,
        *,
        requested_by: int | None = None,
        language: str = "eng",
    ) -> FastScanResult:
        language = str(language or "eng").strip().casefold() or "eng"
        with self.database.connect() as conn:
            file_row = self._file_row(conn, int(file_id))
            streams = self._stream_rows(conn, int(file_id))
            file_sha256 = self._current_sha256(conn, file_row)

        self._verify_media_snapshot(file_row)
        sidecars = self._prepare_sidecars(file_row)
        candidate_set, expanded_specials, corpora = self._candidate_set(
            file_row, sidecars, language
        )

        evidence = []
        evidence.extend(self._claimed_evidence(file_row, candidate_set.candidates))
        evidence.extend(self._container_evidence(file_row, streams, candidate_set.candidates))
        evidence.extend(
            self._subtitle_evidence(file_row, candidate_set.candidates, sidecars, corpora)
        )
        metadata_signature = self._metadata_signature(
            file_row, candidate_set, streams, sidecars
        )

        self._verify_media_snapshot(file_row)
        self._verify_sidecars(sidecars)

        with self.database.connect() as conn:
            current = self._file_row(conn, int(file_id))
            if (
                int(current["size_bytes"] or 0) != int(file_row["size_bytes"] or 0)
                or not _same_modified_at(current["modified_at"], file_row["modified_at"])
                or int(current["season"]) != int(file_row["season"])
                or int(current["episode_start"]) != int(file_row["episode_start"])
                or int(current["episode_end"] or current["episode_start"])
                    != int(file_row["episode_end"] or file_row["episode_start"])
            ):
                raise FastIdentityStaleError(
                    "The catalog claim changed during the Fast scan. Retry the scan."
                )

            claimed_identity = {
                "identity_kind": "episode",
                "source": "catalog_filename",
                "season": int(file_row["season"]),
                "episode_start": int(file_row["episode_start"]),
                "episode_end": int(file_row["episode_end"] or file_row["episode_start"]),
                "filename": str(file_row["filename"] or ""),
            }
            cursor = conn.execute(
                """INSERT INTO media_identity_scans(
                     file_id,identity_kind,requested_profile,status,stage,
                     claimed_identity_json,file_size_bytes,file_modified_at,
                     file_sha256,metadata_signature,requested_by,started_at
                   ) VALUES (?,'episode','fast','running','persisting_fast',?,?,?,?,?,?,
                             CURRENT_TIMESTAMP)""",
                (
                    int(file_id),
                    _canonical_json(claimed_identity),
                    int(file_row["size_bytes"] or 0),
                    file_row["modified_at"],
                    file_sha256,
                    metadata_signature,
                    requested_by,
                ),
            )
            scan_id = int(cursor.lastrowid)
            self._persist_candidates(conn, scan_id, candidate_set.candidates)
            artifact_ids = self._persist_artifacts(conn, file_row, sidecars)
            evidence_count = self._persist_evidence(conn, scan_id, evidence)
            conn.execute(
                """UPDATE media_identity_scans
                   SET status='complete',stage='fast_complete',completed_profile='fast',
                       completed_at=CURRENT_TIMESTAMP,error=''
                   WHERE id=?""",
                (scan_id,),
            )

        return FastScanResult(
            scan_id=scan_id,
            file_id=int(file_id),
            candidate_count=len(candidate_set.candidates),
            evidence_count=evidence_count,
            artifact_count=len(artifact_ids),
            reused_artifact_count=sum(
                item.reused_artifact_id is not None for item in sidecars
            ),
            provider_cache_used=candidate_set.used_provider_cache,
            expanded_specials=expanded_specials,
        )

    def scan_details(self, scan_id: int) -> dict[str, Any] | None:
        """Return the persisted, explainable Fast snapshot without resolving identity."""
        with self.database.connect() as conn:
            scan = conn.execute(
                "SELECT * FROM media_identity_scans WHERE id=?", (int(scan_id),)
            ).fetchone()
            if not scan:
                return None
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
        result = dict(scan)
        try:
            result["claimed_identity"] = json.loads(
                result.pop("claimed_identity_json") or "{}"
            )
        except json.JSONDecodeError:
            result["claimed_identity"] = {}
            result.pop("claimed_identity_json", None)
        for candidate in candidates:
            try:
                candidate["details"] = json.loads(candidate.pop("details_json") or "{}")
            except json.JSONDecodeError:
                candidate["details"] = {}
                candidate.pop("details_json", None)
        for item in evidence:
            try:
                item["details"] = json.loads(item.pop("details_json") or "{}")
            except json.JSONDecodeError:
                item["details"] = {}
                item.pop("details_json", None)
        result["candidates"] = candidates
        result["evidence"] = evidence
        return result

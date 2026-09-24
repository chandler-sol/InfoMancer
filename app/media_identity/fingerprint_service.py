from __future__ import annotations

from dataclasses import dataclass
import json
import math
import sqlite3
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..db import Database
from ..file_hashes import MediaHashService
from .decision_snapshot import result_revision
from .fingerprint import (
    ContentFingerprint,
    FingerprintComparison,
    FingerprintError,
    FingerprintMatchPolicy,
    VIDEO_DHASH64_V1,
    bounded_fingerprint_matches,
    fingerprint_from_payload,
)
from .fingerprint_local import (
    LocalFingerprintError,
    LocalVideoFingerprintExtractor,
    plan_video_fingerprint_timestamps,
)
from .models import MediaIdentityFile
from .service import MediaIdentityDecisionService
from .versions import DEEP_FINGERPRINT_CONTRACT_VERSION


DEEP_FINGERPRINT_ARTIFACT_KEY = VIDEO_DHASH64_V1.key
DEEP_FINGERPRINT_ARTIFACT_VERSION = str(
    DEEP_FINGERPRINT_CONTRACT_VERSION
)


class DeepFingerprintError(RuntimeError):
    """A Deep fingerprint could not be reused or published safely."""


@dataclass(frozen=True)
class DeepFingerprintRun:
    file_id: int
    artifact_id: int | None
    fingerprint: ContentFingerprint | None
    reused: bool
    failure: str = ""


@dataclass(frozen=True)
class DeepFingerprintMatchRun:
    query_file_id: int
    requested_candidate_count: int
    available_candidate_count: int
    comparisons: tuple[FingerprintComparison, ...]
    missing_file_ids: tuple[int, ...]


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DeepFingerprintError(
            "Fingerprint artifact metadata could not be serialized safely."
        ) from exc


def _same_modified_at(first: object, second: object) -> bool:
    if first is None or second is None:
        return first is None and second is None
    try:
        return float(first) == float(second)
    except (TypeError, ValueError):
        return False


def _valid_sha256(value: object) -> bool:
    digest = str(value or "").strip().casefold()
    return (
        len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
    )


class DeepFingerprintArtifactService:
    """Generate and reuse file-owned, candidate-neutral Deep fingerprints."""

    def __init__(
        self,
        database: Database,
        *,
        extractor_factory: Callable[..., LocalVideoFingerprintExtractor] = (
            LocalVideoFingerprintExtractor
        ),
    ) -> None:
        self.database = database
        self.extractor_factory = extractor_factory

    @staticmethod
    def _file_snapshot(
        conn: sqlite3.Connection,
        file_id: int,
    ) -> dict[str, Any]:
        row = conn.execute(
            """SELECT f.*,t.kind title_kind,
                      h.sha256 current_sha256,
                      h.size_bytes hash_size_bytes,
                      h.modified_at hash_modified_at,
                      h.status hash_status
               FROM files f
               JOIN titles t ON t.id=f.title_id
               LEFT JOIN media_file_hashes h ON h.file_id=f.id
               WHERE f.id=?""",
            (int(file_id),),
        ).fetchone()
        if row is None:
            raise DeepFingerprintError(
                "Fingerprint media file was not found."
            )
        snapshot = dict(row)
        if str(snapshot.get("title_kind") or "") != "tv":
            raise DeepFingerprintError(
                "Deep episode fingerprints require a TV file."
            )
        runtime = snapshot.get("runtime_seconds")
        try:
            runtime_seconds = float(runtime)
        except (TypeError, ValueError):
            runtime_seconds = 0.0
        if not math.isfinite(runtime_seconds) or runtime_seconds <= 0:
            raise DeepFingerprintError(
                "Deep fingerprinting requires a positive cataloged runtime."
            )

        trusted_sha256 = ""
        hash_source = ""
        if (
            str(snapshot.get("hash_status") or "") == "complete"
            and _valid_sha256(snapshot.get("current_sha256"))
            and int(snapshot.get("hash_size_bytes") or -1)
            == int(snapshot.get("size_bytes") or 0)
            and _same_modified_at(
                snapshot.get("hash_modified_at"),
                snapshot.get("modified_at"),
            )
        ):
            trusted_sha256 = str(
                snapshot["current_sha256"]
            ).strip().casefold()
            hash_source = "media_file_hashes"
        else:
            scan_rows = conn.execute(
                """SELECT id,file_sha256,file_size_bytes,file_modified_at
                   FROM media_identity_scans
                   WHERE file_id=? AND status='complete' AND file_sha256!=''
                   ORDER BY id DESC LIMIT 8""",
                (int(file_id),),
            ).fetchall()
            for scan_row in scan_rows:
                if (
                    _valid_sha256(scan_row["file_sha256"])
                    and int(scan_row["file_size_bytes"] or 0)
                    == int(snapshot.get("size_bytes") or 0)
                    and _same_modified_at(
                        scan_row["file_modified_at"],
                        snapshot.get("modified_at"),
                    )
                ):
                    trusted_sha256 = str(
                        scan_row["file_sha256"]
                    ).strip().casefold()
                    hash_source = f"media_identity_scan:{int(scan_row['id'])}"
                    break

        if not trusted_sha256:
            raise DeepFingerprintError(
                "Deep fingerprinting requires a current exact file SHA-256."
            )

        snapshot["runtime_ms"] = max(
            1,
            int(round(runtime_seconds * 1000.0)),
        )
        snapshot["current_sha256"] = trusted_sha256
        snapshot["hash_source"] = hash_source
        return snapshot


    @staticmethod
    def _media(snapshot: Mapping[str, Any]) -> MediaIdentityFile:
        return MediaIdentityFile(
            file_id=int(snapshot["id"]),
            title_id=int(snapshot["title_id"]),
            path=str(snapshot["path"]),
            size_bytes=int(snapshot["size_bytes"] or 0),
            modified_at=snapshot["modified_at"],
            sha256=str(snapshot["current_sha256"]),
        )

    @staticmethod
    def _require_scan_binding(
        conn: sqlite3.Connection,
        *,
        scan_id: int,
        file_snapshot: Mapping[str, Any],
        expected_revision: int | None,
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM media_identity_scans WHERE id=?",
            (int(scan_id),),
        ).fetchone()
        if row is None:
            raise DeepFingerprintError(
                "Episode Identity scan disappeared before fingerprinting."
            )
        scan = dict(row)
        if int(scan["file_id"]) != int(file_snapshot["id"]):
            raise DeepFingerprintError(
                "Episode Identity scan belongs to a different media file."
            )
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
        if (
            not current
            or scan["status"] != "complete"
            or str(scan["file_sha256"] or "").strip().casefold()
            != str(file_snapshot["current_sha256"])
            or int(scan["file_size_bytes"] or 0)
            != int(file_snapshot["size_bytes"] or 0)
            or not _same_modified_at(
                scan["file_modified_at"],
                file_snapshot["modified_at"],
            )
        ):
            raise DeepFingerprintError(
                "Episode Identity snapshot is stale for fingerprinting."
            )
        revision = result_revision(scan)
        if revision <= 0:
            raise DeepFingerprintError(
                "Deep fingerprinting requires a sealed Episode Identity result."
            )
        if expected_revision is not None and revision != int(expected_revision):
            raise DeepFingerprintError(
                "Episode Identity publication changed before fingerprinting."
            )
        return scan

    @staticmethod
    def _artifact_fingerprint(
        row: Mapping[str, Any],
        *,
        snapshot: Mapping[str, Any],
        source_signature: str | None,
        expected_timestamps: Sequence[int] | None,
    ) -> ContentFingerprint | None:
        if (
            int(row.get("file_id") or 0) != int(snapshot["id"])
            or str(row.get("artifact_type") or "") != "content_fingerprint"
            or str(row.get("analyzer_key") or "")
            != DEEP_FINGERPRINT_ARTIFACT_KEY
            or str(row.get("analyzer_version") or "")
            != DEEP_FINGERPRINT_ARTIFACT_VERSION
            or str(row.get("status") or "") != "complete"
            or str(row.get("profile") or "") != "deep"
            or str(row.get("source_kind") or "") != "local_ffmpeg"
            or str(row.get("source_ref") or "")
            != f"file:{int(snapshot['id'])}"
            or (
                source_signature is not None
                and str(row.get("source_signature") or "") != source_signature
            )
            or int(row.get("file_size_bytes") or 0)
            != int(snapshot["size_bytes"] or 0)
            or not _same_modified_at(
                row.get("file_modified_at"),
                snapshot.get("modified_at"),
            )
        ):
            return None
        try:
            payload = json.loads(str(row.get("payload_json") or "{}"))
            fingerprint = fingerprint_from_payload(payload)
        except (TypeError, ValueError, json.JSONDecodeError, FingerprintError):
            return None
        if (
            fingerprint.file_id != int(snapshot["id"])
            or fingerprint.file_sha256
            != str(snapshot["current_sha256"])
            or fingerprint.runtime_ms != int(snapshot["runtime_ms"])
            or fingerprint.algorithm.identity_payload()
            != VIDEO_DHASH64_V1.identity_payload()
            or fingerprint.source_kind != "local_ffmpeg"
            or (
                source_signature is not None
                and fingerprint.source_signature != source_signature
            )
            or str(row.get("source_signature") or "")
            != fingerprint.source_signature
            or (
                expected_timestamps is not None
                and tuple(
                    sample.timestamp_ms for sample in fingerprint.samples
                )
                != tuple(int(value) for value in expected_timestamps)
            )
            or str(row.get("cache_key") or "")
            != fingerprint.cache_key()
        ):
            return None
        if expected_timestamps is None:
            try:
                sample_count = int(
                    fingerprint.parameters.get("sample_count")
                )
            except (TypeError, ValueError):
                return None
            if sample_count != len(fingerprint.samples):
                return None
            try:
                planned_timestamps = plan_video_fingerprint_timestamps(
                    int(snapshot["runtime_ms"]),
                    sample_count=sample_count,
                )
            except FingerprintError:
                return None
            if tuple(
                sample.timestamp_ms for sample in fingerprint.samples
            ) != planned_timestamps:
                return None
        return fingerprint

    def _load_current(
        self,
        snapshot: Mapping[str, Any],
        *,
        source_signature: str | None,
        expected_timestamps: Sequence[int] | None,
    ) -> tuple[int, ContentFingerprint] | None:
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM media_identity_artifacts
                   WHERE file_id=? AND artifact_type='content_fingerprint'
                     AND analyzer_key=? AND analyzer_version=?
                     AND status='complete'
                   ORDER BY id DESC LIMIT 8""",
                (
                    int(snapshot["id"]),
                    DEEP_FINGERPRINT_ARTIFACT_KEY,
                    DEEP_FINGERPRINT_ARTIFACT_VERSION,
                ),
            ).fetchall()
            for raw in rows:
                row = dict(raw)
                fingerprint = self._artifact_fingerprint(
                    row,
                    snapshot=snapshot,
                    source_signature=source_signature,
                    expected_timestamps=expected_timestamps,
                )
                if fingerprint is None:
                    continue
                conn.execute(
                    """UPDATE media_identity_artifacts
                       SET last_used_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (int(row["id"]),),
                )
                return int(row["id"]), fingerprint
        return None

    def _persist(
        self,
        baseline: Mapping[str, Any],
        fingerprint: ContentFingerprint,
        *,
        scan_id: int | None,
        expected_revision: int | None,
    ) -> tuple[int, ContentFingerprint, bool]:
        payload = fingerprint.persisted_payload()
        cache_key = fingerprint.cache_key()
        expected_timestamps = tuple(
            sample.timestamp_ms for sample in fingerprint.samples
        )

        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._file_snapshot(conn, int(baseline["id"]))
            if (
                str(current["path"]) != str(baseline["path"])
                or int(current["title_id"]) != int(baseline["title_id"])
                or int(current["size_bytes"] or 0)
                != int(baseline["size_bytes"] or 0)
                or not _same_modified_at(
                    current["modified_at"],
                    baseline["modified_at"],
                )
                or str(current["current_sha256"])
                != str(baseline["current_sha256"])
                or int(current["runtime_ms"]) != int(baseline["runtime_ms"])
            ):
                raise DeepFingerprintError(
                    "Media snapshot changed before fingerprint publication."
                )
            if scan_id is not None:
                self._require_scan_binding(
                    conn,
                    scan_id=int(scan_id),
                    file_snapshot=current,
                    expected_revision=expected_revision,
                )

            cursor = conn.execute(
                """INSERT OR IGNORE INTO media_identity_artifacts(
                     file_id,artifact_type,analyzer_key,analyzer_version,
                     cache_key,status,profile,source_kind,source_ref,
                     source_signature,file_size_bytes,file_modified_at,
                     payload_json
                   ) VALUES (
                     ?,'content_fingerprint',?,?,?,'complete','deep',
                     'local_ffmpeg',?,?,?,?,?
                   )""",
                (
                    int(current["id"]),
                    DEEP_FINGERPRINT_ARTIFACT_KEY,
                    DEEP_FINGERPRINT_ARTIFACT_VERSION,
                    cache_key,
                    f"file:{int(current['id'])}",
                    fingerprint.source_signature,
                    int(current["size_bytes"] or 0),
                    current["modified_at"],
                    _canonical_json(payload),
                ),
            )
            inserted = cursor.rowcount == 1
            row = conn.execute(
                """SELECT * FROM media_identity_artifacts
                   WHERE file_id=? AND artifact_type='content_fingerprint'
                     AND analyzer_key=? AND analyzer_version=? AND cache_key=?
                   ORDER BY id DESC LIMIT 1""",
                (
                    int(current["id"]),
                    DEEP_FINGERPRINT_ARTIFACT_KEY,
                    DEEP_FINGERPRINT_ARTIFACT_VERSION,
                    cache_key,
                ),
            ).fetchone()
            if row is None:
                raise DeepFingerprintError(
                    "InfoMancer could not persist the Deep fingerprint."
                )
            row_dict = dict(row)
            persisted = self._artifact_fingerprint(
                row_dict,
                snapshot=current,
                source_signature=fingerprint.source_signature,
                expected_timestamps=expected_timestamps,
            )
            if persisted is None:
                repair = conn.execute(
                    """UPDATE media_identity_artifacts
                       SET status='complete',profile='deep',
                           source_kind='local_ffmpeg',source_ref=?,
                           source_signature=?,file_size_bytes=?,
                           file_modified_at=?,payload_json=?,error='',
                           updated_at=CURRENT_TIMESTAMP,
                           last_used_at=CURRENT_TIMESTAMP
                       WHERE id=? AND cache_key=?""",
                    (
                        f"file:{int(current['id'])}",
                        fingerprint.source_signature,
                        int(current["size_bytes"] or 0),
                        current["modified_at"],
                        _canonical_json(payload),
                        int(row_dict["id"]),
                        cache_key,
                    ),
                )
                if repair.rowcount != 1:
                    raise DeepFingerprintError(
                        "InfoMancer could not repair the fingerprint cache safely."
                    )
                persisted = fingerprint
                inserted = True
            else:
                conn.execute(
                    """UPDATE media_identity_artifacts
                       SET last_used_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (int(row_dict["id"]),),
                )
            return int(row_dict["id"]), persisted, inserted

    def ensure_file(
        self,
        file_id: int,
        *,
        scan_id: int | None = None,
        expected_revision: int | None = None,
    ) -> DeepFingerprintRun:
        try:
            with self.database.connect() as conn:
                snapshot = self._file_snapshot(conn, int(file_id))
        except DeepFingerprintError as exc:
            if "current exact file SHA-256" not in str(exc):
                raise
            try:
                MediaHashService(self.database).hash_file(int(file_id))
            except (OSError, ValueError) as hash_exc:
                return DeepFingerprintRun(
                    file_id=int(file_id),
                    artifact_id=None,
                    fingerprint=None,
                    reused=False,
                    failure=(
                        "fingerprint-hash-unavailable:"
                        f"{type(hash_exc).__name__}:{hash_exc}"
                    ),
                )
            with self.database.connect() as conn:
                snapshot = self._file_snapshot(conn, int(file_id))

        with self.database.connect() as conn:
            if scan_id is not None:
                scan = self._require_scan_binding(
                    conn,
                    scan_id=int(scan_id),
                    file_snapshot=snapshot,
                    expected_revision=expected_revision,
                )
                if expected_revision is None:
                    expected_revision = result_revision(scan)
            media = self._media(snapshot)

        cached_any = self._load_current(
            snapshot,
            source_signature=None,
            expected_timestamps=None,
        )
        if cached_any is not None:
            artifact_id, fingerprint = cached_any
            if scan_id is not None:
                with self.database.connect() as conn:
                    self._require_scan_binding(
                        conn,
                        scan_id=int(scan_id),
                        file_snapshot=snapshot,
                        expected_revision=expected_revision,
                    )
            return DeepFingerprintRun(
                file_id=int(file_id),
                artifact_id=artifact_id,
                fingerprint=fingerprint,
                reused=True,
            )

        try:
            extractor = self.extractor_factory(
                media,
                int(snapshot["runtime_ms"]),
            )
        except (FingerprintError, LocalFingerprintError, OSError) as exc:
            return DeepFingerprintRun(
                file_id=int(file_id),
                artifact_id=None,
                fingerprint=None,
                reused=False,
                failure=f"fingerprint-unavailable:{type(exc).__name__}:{exc}",
            )
        if not extractor.available():
            return DeepFingerprintRun(
                file_id=int(file_id),
                artifact_id=None,
                fingerprint=None,
                reused=False,
                failure="fingerprint-unavailable",
            )

        existing = self._load_current(
            snapshot,
            source_signature=extractor.source_signature,
            expected_timestamps=extractor.timestamps,
        )
        if existing is not None:
            artifact_id, fingerprint = existing
            return DeepFingerprintRun(
                file_id=int(file_id),
                artifact_id=artifact_id,
                fingerprint=fingerprint,
                reused=True,
            )

        try:
            fingerprint = extractor.extract()
        except (FingerprintError, LocalFingerprintError, OSError) as exc:
            return DeepFingerprintRun(
                file_id=int(file_id),
                artifact_id=None,
                fingerprint=None,
                reused=False,
                failure=f"fingerprint-extraction:{type(exc).__name__}:{exc}",
            )
        if (
            fingerprint.file_id != int(snapshot["id"])
            or fingerprint.file_sha256
            != str(snapshot["current_sha256"])
            or fingerprint.runtime_ms != int(snapshot["runtime_ms"])
            or fingerprint.source_signature != extractor.source_signature
            or tuple(
                sample.timestamp_ms for sample in fingerprint.samples
            )
            != tuple(extractor.timestamps)
        ):
            raise DeepFingerprintError(
                "Fingerprint extractor returned output outside its prepared identity."
            )

        artifact_id, persisted, inserted = self._persist(
            snapshot,
            fingerprint,
            scan_id=scan_id,
            expected_revision=expected_revision,
        )
        return DeepFingerprintRun(
            file_id=int(file_id),
            artifact_id=artifact_id,
            fingerprint=persisted,
            reused=not inserted,
        )

    def ensure_scan(self, scan_id: int) -> DeepFingerprintRun:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT file_id FROM media_identity_scans WHERE id=?",
                (int(scan_id),),
            ).fetchone()
            if row is None:
                raise DeepFingerprintError(
                    "Episode Identity scan was not found."
                )
            file_id = int(row["file_id"])
        return self.ensure_file(
            file_id,
            scan_id=int(scan_id),
        )

    def load_current_file(
        self,
        file_id: int,
    ) -> ContentFingerprint | None:
        with self.database.connect() as conn:
            snapshot = self._file_snapshot(conn, int(file_id))
        existing = self._load_current(
            snapshot,
            source_signature=None,
            expected_timestamps=None,
        )
        return None if existing is None else existing[1]


    def compare_current_files(
        self,
        query_file_id: int,
        candidate_file_ids: Iterable[int],
        *,
        policy: FingerprintMatchPolicy | None = None,
    ) -> DeepFingerprintMatchRun:
        policy = policy or FingerprintMatchPolicy()
        candidate_ids = tuple(int(value) for value in candidate_file_ids)
        if len(candidate_ids) > policy.max_candidates:
            raise FingerprintError(
                "Fingerprint comparison candidate count exceeds the bounded policy."
            )
        if len(set(candidate_ids)) != len(candidate_ids):
            raise FingerprintError(
                "Fingerprint candidate files must be unique."
            )
        query = self.load_current_file(int(query_file_id))
        if query is None:
            raise DeepFingerprintError(
                "The query file does not have a current trusted fingerprint."
            )

        available: list[ContentFingerprint] = []
        missing: list[int] = []
        for file_id in candidate_ids:
            if file_id == int(query_file_id):
                continue
            try:
                fingerprint = self.load_current_file(file_id)
            except DeepFingerprintError:
                fingerprint = None
            if fingerprint is None:
                missing.append(file_id)
            else:
                available.append(fingerprint)

        comparisons = bounded_fingerprint_matches(
            query,
            available,
            policy=policy,
        )
        return DeepFingerprintMatchRun(
            query_file_id=int(query_file_id),
            requested_candidate_count=len(
                [value for value in candidate_ids if value != int(query_file_id)]
            ),
            available_candidate_count=len(available),
            comparisons=comparisons,
            missing_file_ids=tuple(missing),
        )

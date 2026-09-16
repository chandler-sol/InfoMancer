from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import Database


INTEGRITY_STATUSES = {"passed", "warning", "failed", "error"}
INTEGRITY_MODES = {"sample", "full"}


class MediaIntegrityResultService:
    """Persist read-only media-integrity evidence independently of scan execution."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def record(
        self,
        file_id: int,
        *,
        status: str,
        mode: str,
        checked_modified_at: float | None,
        checked_size_bytes: int,
        issues: list[str] | None = None,
        details: dict[str, Any] | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        normalized_status = str(status).strip().lower()
        normalized_mode = str(mode).strip().lower()
        if normalized_status not in INTEGRITY_STATUSES:
            raise ValueError("Integrity status must be passed, warning, failed, or error.")
        if normalized_mode not in INTEGRITY_MODES:
            raise ValueError("Integrity mode must be sample or full.")
        normalized_issues = [str(item) for item in (issues or [])]
        payload = dict(details or {})
        payload.setdefault("issues", normalized_issues)

        if conn is None:
            with self.database.connect() as connection:
                self.record(
                    file_id,
                    status=normalized_status,
                    mode=normalized_mode,
                    checked_modified_at=checked_modified_at,
                    checked_size_bytes=checked_size_bytes,
                    issues=normalized_issues,
                    details=payload,
                    conn=connection,
                )
            return

        conn.execute(
            """INSERT INTO media_integrity_results(
                 file_id,status,mode,checked_at,checked_modified_at,
                 checked_size_bytes,issue_count,details_json
               ) VALUES (?,?,?,CURRENT_TIMESTAMP,?,?,?,?)
               ON CONFLICT(file_id) DO UPDATE SET
                 status=excluded.status,
                 mode=excluded.mode,
                 checked_at=CURRENT_TIMESTAMP,
                 checked_modified_at=excluded.checked_modified_at,
                 checked_size_bytes=excluded.checked_size_bytes,
                 issue_count=excluded.issue_count,
                 details_json=excluded.details_json""",
            (
                int(file_id),
                normalized_status,
                normalized_mode,
                checked_modified_at,
                max(0, int(checked_size_bytes)),
                len(normalized_issues),
                json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
            ),
        )

    def result(self, file_id: int) -> dict[str, Any] | None:
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT i.*,f.modified_at AS current_modified_at,
                          f.size_bytes AS current_size_bytes,
                          CASE
                            WHEN COALESCE(i.checked_modified_at,-1) != COALESCE(f.modified_at,-1)
                              OR i.checked_size_bytes != f.size_bytes
                            THEN 1 ELSE 0
                          END AS stale
                   FROM media_integrity_results i
                   JOIN files f ON f.id=i.file_id
                   WHERE i.file_id=?""",
                (file_id,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            result["details"] = json.loads(result.pop("details_json") or "{}")
        except json.JSONDecodeError:
            result["details"] = {}
            result.pop("details_json", None)
        result["stale"] = bool(result["stale"])
        return result

    def pending_files(self, file_ids: list[int] | None = None) -> list[dict[str, Any]]:
        params: list[Any] = []
        clause = ""
        if file_ids:
            placeholders = ",".join("?" for _ in file_ids)
            clause = f"AND f.id IN ({placeholders})"
            params.extend(int(file_id) for file_id in file_ids)
        with self.database.connect() as conn:
            rows = conn.execute(
                f"""SELECT f.id,f.path,f.filename,f.modified_at,f.size_bytes,
                            f.runtime_seconds,COALESCE(t.metadata_title,t.title) title
                     FROM files f
                     JOIN titles t ON t.id=f.title_id
                     LEFT JOIN media_integrity_results i ON i.file_id=f.id
                     WHERE (
                       i.file_id IS NULL
                       OR COALESCE(i.checked_modified_at,-1) != COALESCE(f.modified_at,-1)
                       OR i.checked_size_bytes != f.size_bytes
                     ) {clause}
                     ORDER BY f.id""",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def summary(self) -> dict[str, int]:
        with self.database.connect() as conn:
            counts = {
                str(row["status"]): int(row["count"])
                for row in conn.execute(
                    "SELECT status,COUNT(*) count FROM media_integrity_results GROUP BY status"
                )
            }
            stale = int(conn.execute(
                """SELECT COUNT(*)
                   FROM files f
                   LEFT JOIN media_integrity_results i ON i.file_id=f.id
                   WHERE i.file_id IS NULL
                      OR COALESCE(i.checked_modified_at,-1) != COALESCE(f.modified_at,-1)
                      OR i.checked_size_bytes != f.size_bytes"""
            ).fetchone()[0])
            total = int(conn.execute("SELECT COUNT(*) FROM files").fetchone()[0])
        return {
            "total_files": total,
            "unchecked_or_stale": stale,
            "passed": counts.get("passed", 0),
            "warning": counts.get("warning", 0),
            "failed": counts.get("failed", 0),
            "error": counts.get("error", 0),
        }

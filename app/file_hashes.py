from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Callable, Iterable

from .db import Database


class MediaHashService:
    """Maintain reusable SHA-256 fingerprints without modifying media."""

    def __init__(self, database: Database):
        self.database = database
        # A process restart can interrupt a fingerprint mid-file. Put those
        # records back in the queue so they are never stranded as "running".
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE media_file_hashes SET status='queued',
                   error='Fingerprinting was interrupted and will be retried.',
                   queued_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                   WHERE status='running'"""
            )

    @staticmethod
    def _current(row) -> bool:
        return (
            row and row["status"] == "complete" and row["sha256"]
            and int(row["hash_size"] or 0) == int(row["size_bytes"] or 0)
            and float(row["hash_modified"] or 0) == float(row["modified_at"] or 0)
        )

    def queue(self, file_ids: Iterable[int]) -> list[int]:
        queued: list[int] = []
        with self.database.connect() as conn:
            for file_id in dict.fromkeys(int(value) for value in file_ids):
                row = conn.execute(
                    """SELECT f.id,f.size_bytes,f.modified_at,h.sha256,h.status,
                              h.size_bytes hash_size,h.modified_at hash_modified
                       FROM files f LEFT JOIN media_file_hashes h ON h.file_id=f.id
                       WHERE f.id=?""", (file_id,),
                ).fetchone()
                if not row or self._current(row):
                    continue
                conn.execute(
                    """INSERT INTO media_file_hashes(
                         file_id,size_bytes,modified_at,status,error,queued_at,updated_at
                       ) VALUES (?,?,?,'queued','',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                       ON CONFLICT(file_id) DO UPDATE SET
                         sha256=NULL,size_bytes=excluded.size_bytes,
                         modified_at=excluded.modified_at,status='queued',error='',
                         queued_at=CURRENT_TIMESTAMP,hashed_at=NULL,
                         updated_at=CURRENT_TIMESTAMP""",
                    (file_id, int(row["size_bytes"] or 0), row["modified_at"]),
                )
                queued.append(file_id)
        return queued

    def eligible_ids(self, *, queued_only: bool = False) -> list[int]:
        where = "h.status='queued'" if queued_only else """(
            h.file_id IS NULL OR h.status IN ('queued','error') OR
            h.size_bytes!=f.size_bytes OR COALESCE(h.modified_at,0)!=COALESCE(f.modified_at,0)
        )"""
        with self.database.connect() as conn:
            return [int(row["id"]) for row in conn.execute(
                f"""SELECT f.id FROM files f
                    LEFT JOIN media_file_hashes h ON h.file_id=f.id
                    WHERE {where} ORDER BY f.id"""
            )]

    def records(self) -> dict[int, dict]:
        with self.database.connect() as conn:
            return {int(row["file_id"]): dict(row) for row in conn.execute(
                "SELECT * FROM media_file_hashes"
            )}

    def counts(self) -> dict[str, int]:
        result = {"queued": 0, "running": 0, "complete": 0, "error": 0}
        with self.database.connect() as conn:
            for row in conn.execute(
                "SELECT status,COUNT(*) count FROM media_file_hashes GROUP BY status"
            ):
                result[row["status"]] = int(row["count"])
            result["unhashed"] = int(conn.execute(
                """SELECT COUNT(*) count FROM files f
                   LEFT JOIN media_file_hashes h ON h.file_id=f.id
                   WHERE h.file_id IS NULL"""
            ).fetchone()["count"])
        return result

    def hash_file(self, file_id: int, *, force: bool = False) -> str:
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT f.*,h.sha256,h.status,h.size_bytes hash_size,
                          h.modified_at hash_modified
                   FROM files f LEFT JOIN media_file_hashes h ON h.file_id=f.id
                   WHERE f.id=?""", (file_id,),
            ).fetchone()
            if not row:
                raise ValueError("The media file is no longer in the catalog.")
            if not force and self._current(row):
                return str(row["sha256"])
            conn.execute(
                """INSERT INTO media_file_hashes(file_id,size_bytes,modified_at,status,updated_at)
                   VALUES (?,?,?,'running',CURRENT_TIMESTAMP)
                   ON CONFLICT(file_id) DO UPDATE SET status='running',error='',
                     updated_at=CURRENT_TIMESTAMP""",
                (file_id, int(row["size_bytes"] or 0), row["modified_at"]),
            )
            path = Path(row["path"])
        try:
            before = path.stat()
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                while chunk := handle.read(4 * 1024 * 1024):
                    digest.update(chunk)
            after = path.stat()
            if before.st_size != after.st_size or before.st_mtime != after.st_mtime:
                raise OSError("The file changed while it was being checked. It was queued to try again later.")
            value = digest.hexdigest()
            with self.database.connect() as conn:
                # Fingerprinting observes the file directly. Keep the catalog
                # signature in sync so this freshly calculated hash is not
                # immediately treated as stale when a file changed between
                # the last scan and this read.
                conn.execute(
                    """UPDATE files SET size_bytes=?,modified_at=? WHERE id=?""",
                    (after.st_size, after.st_mtime, file_id),
                )
                conn.execute(
                    """UPDATE media_file_hashes SET sha256=?,size_bytes=?,modified_at=?,
                       status='complete',error='',hashed_at=CURRENT_TIMESTAMP,
                       updated_at=CURRENT_TIMESTAMP WHERE file_id=?""",
                    (value, after.st_size, after.st_mtime, file_id),
                )
            return value
        except OSError as exc:
            message = (
                "InfoMancer could not read this file. Check that the source is online and "
                f"the file is readable, then retry. ({exc})"
            )
            with self.database.connect() as conn:
                conn.execute(
                    """UPDATE media_file_hashes SET status='error',error=?,
                       updated_at=CURRENT_TIMESTAMP WHERE file_id=?""", (message, file_id),
                )
            raise OSError(message) from exc

    def hash_many(
        self, file_ids: Iterable[int], *,
        progress: Callable[[int, int, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        paused: Callable[[], bool] | None = None,
        intensity: str = "low",
    ) -> dict[str, int]:
        ids = list(dict.fromkeys(int(value) for value in file_ids))
        complete = failed = 0
        for index, file_id in enumerate(ids, 1):
            if cancelled and cancelled():
                break
            while paused and paused():
                if cancelled and cancelled():
                    return {"complete": complete, "failed": failed, "total": len(ids)}
                time.sleep(.25)
            with self.database.connect() as conn:
                row = conn.execute("SELECT filename FROM files WHERE id=?", (file_id,)).fetchone()
            filename = row["filename"] if row else "media file"
            if progress:
                progress(index - 1, len(ids), filename)
            try:
                self.hash_file(file_id)
                complete += 1
            except (OSError, ValueError):
                failed += 1
            if intensity == "low":
                time.sleep(.03)
            elif intensity == "balanced":
                time.sleep(.005)
        if progress:
            progress(complete + failed, len(ids), "")
        return {"complete": complete, "failed": failed, "total": len(ids)}

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .db import Database


class MediaIntegrityService:
    """Read-only FFmpeg decode sampling used by the Media Error Scan Framework."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def available() -> bool:
        """Return whether the read-only FFmpeg decoder is available to this process."""
        return shutil.which("ffmpeg") is not None

    @staticmethod
    def _sample_offsets(runtime_seconds: float | None) -> list[float]:
        runtime = float(runtime_seconds or 0)
        if runtime <= 20:
            return [0.0]
        points = [max(0.0, runtime * ratio - 2.0) for ratio in (0.05, 0.35, 0.65, 0.9)]
        result: list[float] = []
        for value in points:
            rounded = round(value, 2)
            if rounded not in result:
                result.append(rounded)
        return result

    @staticmethod
    def _command(path: Path, *, start: float | None, full: bool) -> list[str]:
        command = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-xerror"]
        if start is not None and start > 0:
            command += ["-ss", f"{start:.2f}"]
        command += [
            "-i", str(path), "-map", "0:v:0?", "-map", "0:a:0?", "-sn", "-dn",
        ]
        if not full:
            command += ["-t", "4"]
        command += ["-f", "null", "-"]
        return command

    def check_path(
        self, path: Path, *, runtime_seconds: float | None = None,
        mode: str = "sample", timeout: int = 120,
    ) -> dict[str, Any]:
        if mode not in {"sample", "full"}:
            raise ValueError("Integrity mode must be sample or full.")
        if not path.exists():
            return {"status": "error", "issues": ["Media file is not currently available."], "samples": []}
        offsets = [None] if mode == "full" else self._sample_offsets(runtime_seconds)
        issues: list[str] = []
        samples: list[dict[str, Any]] = []
        for offset in offsets:
            command = self._command(path, start=offset, full=mode == "full")
            try:
                result = subprocess.run(
                    command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=timeout, check=False,
                )
            except FileNotFoundError:
                return {"status": "error", "issues": ["FFmpeg is not installed or is not on PATH."], "samples": []}
            except subprocess.TimeoutExpired:
                label = "full decode" if mode == "full" else f"sample near {offset or 0:.0f}s"
                issues.append(f"FFmpeg timed out during {label}.")
                samples.append({"offset": offset, "returncode": None, "detail": "timeout"})
                continue
            detail = (result.stderr or "").strip()
            samples.append({
                "offset": offset, "returncode": int(result.returncode),
                "detail": detail[:4000],
            })
            if result.returncode != 0:
                issues.append(detail[:1000] or f"FFmpeg exited with code {result.returncode}.")
            elif detail:
                issues.append(detail[:1000])
        status = "passed"
        if issues:
            status = "failed" if any(item.get("returncode") not in {0, None} for item in samples) else "warning"
            if any(item.get("detail") == "timeout" for item in samples) and status != "failed":
                status = "error"
        return {"status": status, "issues": issues, "samples": samples}

    def pending_files(self, file_ids: list[int] | None = None) -> list[dict[str, Any]]:
        params: list[Any] = []
        clause = ""
        if file_ids:
            placeholders = ",".join("?" for _ in file_ids)
            clause = f"AND f.id IN ({placeholders})"
            params.extend(file_ids)
        with self.database.connect() as conn:
            rows = conn.execute(
                f"""SELECT f.id,f.path,f.filename,f.modified_at,f.size_bytes,f.runtime_seconds,
                            COALESCE(t.metadata_title,t.title) title
                     FROM files f JOIN titles t ON t.id=f.title_id
                     LEFT JOIN media_integrity_results i ON i.file_id=f.id
                     WHERE (i.file_id IS NULL
                            OR COALESCE(i.checked_modified_at,-1) != COALESCE(f.modified_at,-1)
                            OR i.checked_size_bytes != f.size_bytes)
                       {clause}
                     ORDER BY f.id""",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def check_file(self, file_row: dict[str, Any], *, mode: str = "sample") -> dict[str, Any]:
        result = self.check_path(
            Path(file_row["path"]), runtime_seconds=file_row.get("runtime_seconds"), mode=mode,
        )
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO media_integrity_results(
                     file_id,status,mode,checked_at,checked_modified_at,checked_size_bytes,
                     issue_count,details_json
                   ) VALUES (?,?,?,CURRENT_TIMESTAMP,?,?,?,?)
                   ON CONFLICT(file_id) DO UPDATE SET
                     status=excluded.status,mode=excluded.mode,checked_at=CURRENT_TIMESTAMP,
                     checked_modified_at=excluded.checked_modified_at,
                     checked_size_bytes=excluded.checked_size_bytes,
                     issue_count=excluded.issue_count,details_json=excluded.details_json""",
                (
                    file_row["id"], result["status"], mode,
                    file_row.get("modified_at"), int(file_row.get("size_bytes") or 0),
                    len(result["issues"]), json.dumps(result, ensure_ascii=False),
                ),
            )
        return result

    def summary(self) -> dict[str, int]:
        with self.database.connect() as conn:
            counts = {str(row["status"]): int(row["count"]) for row in conn.execute(
                "SELECT status,COUNT(*) count FROM media_integrity_results GROUP BY status"
            )}
            stale = int(conn.execute(
                """SELECT COUNT(*) FROM files f LEFT JOIN media_integrity_results i ON i.file_id=f.id
                   WHERE i.file_id IS NULL OR COALESCE(i.checked_modified_at,-1) != COALESCE(f.modified_at,-1)
                     OR i.checked_size_bytes != f.size_bytes"""
            ).fetchone()[0])
            total = int(conn.execute("SELECT COUNT(*) FROM files").fetchone()[0])
        return {
            "total_files": total, "unchecked_or_stale": stale,
            "passed": counts.get("passed", 0), "warning": counts.get("warning", 0),
            "failed": counts.get("failed", 0), "error": counts.get("error", 0),
        }

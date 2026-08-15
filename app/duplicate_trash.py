from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import Database


FILE_COLUMNS = (
    "title_id", "path", "filename", "extension", "size_bytes", "modified_at",
    "season", "episode_start", "episode_end", "parsed_title", "original_filename",
    "runtime_seconds", "width", "height", "video_codec", "audio_codec",
    "audio_channels", "bitrate", "container", "dynamic_range", "media_info_at",
    "media_info_error", "edition_name", "version_name", "identity_confirmed",
    "version_preferred", "seen_scan",
)


class DuplicateTrashError(ValueError):
    pass


class DuplicateTrashService:
    """Reversible, explicitly selected duplicate-file quarantine."""

    def __init__(self, database: Database):
        self.database = database

    def preview(self, file_id: int, retention_days: int | None) -> dict:
        row = self._catalog_file(file_id)
        source = Path(row["path"])
        root = Path(row["root_path"])
        self._require_inside(source, root)
        trash_dir = root / ".infomancer-trash" / datetime.now().strftime("%Y-%m-%d")
        destination = trash_dir / f"{uuid.uuid4().hex[:10]}-{source.name}"
        purge_after = self._purge_after(retention_days)
        return {
            "file": row,
            "source": str(source),
            "destination": str(destination),
            "purge_after": purge_after,
            "retention_days": retention_days,
        }

    def move(self, file_id: int, retention_days: int | None, user_id: int | None) -> int:
        row = self._catalog_file(file_id)
        source = Path(row["path"])
        root = Path(row["root_path"])
        self._require_inside(source, root)
        if not source.is_file():
            raise DuplicateTrashError(
                "The selected file is no longer present at its cataloged path. Use “I deleted it myself” so InfoMancer can verify and update the catalog."
            )
        trash_dir = root / ".infomancer-trash" / datetime.now().strftime("%Y-%m-%d")
        destination = trash_dir / f"{uuid.uuid4().hex[:10]}-{source.name}"
        trash_dir.mkdir(parents=True, exist_ok=True)
        self._require_inside(destination, root / ".infomancer-trash")
        snapshot = {column: row[column] for column in FILE_COLUMNS}
        shutil.move(str(source), str(destination))
        try:
            with self.database.connect() as conn:
                cursor = conn.execute(
                    """INSERT INTO duplicate_trash(
                         original_file_id,title_id,root_id,original_path,trash_path,
                         file_snapshot,size_bytes,moved_by,purge_after
                       ) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (file_id, row["title_id"], row["root_id"], str(source),
                     str(destination), json.dumps(snapshot), row["size_bytes"] or 0, user_id,
                     self._purge_after(retention_days)),
                )
                conn.execute("DELETE FROM files WHERE id=?", (file_id,))
                return int(cursor.lastrowid)
        except Exception:
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination), str(source))
            raise

    def verify_manually_removed(self, file_id: int, user_id: int | None = None) -> str:
        row = self._catalog_file(file_id)
        path = Path(row["path"])
        try:
            path.stat()
        except FileNotFoundError:
            with self.database.connect() as conn:
                conn.execute(
                    """INSERT INTO duplicate_manual_removals(
                         original_file_id,title_id,root_id,path,filename,size_bytes,verified_by
                       ) VALUES (?,?,?,?,?,?,?)""",
                    (file_id, row["title_id"], row["root_id"], str(path),
                     row["filename"], row["size_bytes"] or 0, user_id),
                )
                conn.execute("DELETE FROM files WHERE id=?", (file_id,))
            return str(path)
        except (PermissionError, OSError) as exc:
            raise DuplicateTrashError(
                f"InfoMancer could not verify this file because the storage location could not be read: {path}. Check that the source is mounted and accessible, then try again."
            ) from exc
        raise DuplicateTrashError(
            f"The file is still present, so the catalog was not changed: {path}. Delete or move that exact file in your file manager, then try again."
        )

    def impact(self) -> dict[str, int]:
        """Summarize duplicate cleanup without overstating available disk space."""
        with self.database.connect() as conn:
            trash = conn.execute(
                """SELECT
                     COALESCE(SUM(CASE WHEN status='trashed' THEN size_bytes ELSE 0 END),0) pending_bytes,
                     COALESCE(SUM(CASE WHEN status='purged' THEN size_bytes ELSE 0 END),0) purged_bytes,
                     COALESCE(SUM(CASE WHEN status='trashed' THEN 1 ELSE 0 END),0) pending_files,
                     COALESCE(SUM(CASE WHEN status='purged' THEN 1 ELSE 0 END),0) purged_files
                   FROM duplicate_trash"""
            ).fetchone()
            manual = conn.execute(
                """SELECT COALESCE(SUM(size_bytes),0) reclaimed_bytes,COUNT(*) reclaimed_files
                   FROM duplicate_manual_removals"""
            ).fetchone()
        pending_bytes = int(trash["pending_bytes"] or 0)
        purged_bytes = int(trash["purged_bytes"] or 0)
        manual_bytes = int(manual["reclaimed_bytes"] or 0)
        pending_files = int(trash["pending_files"] or 0)
        purged_files = int(trash["purged_files"] or 0)
        manual_files = int(manual["reclaimed_files"] or 0)
        return {
            "reclaimed_bytes": purged_bytes + manual_bytes,
            "reclaimed_files": purged_files + manual_files,
            "pending_bytes": pending_bytes,
            "pending_files": pending_files,
            "handled_bytes": purged_bytes + manual_bytes + pending_bytes,
            "handled_files": purged_files + manual_files + pending_files,
        }

    def history(self, status: str = "all", limit: int = 50) -> list[dict]:
        """Return a combined, human-readable audit trail for duplicate cleanup."""
        allowed = {"all", "pending", "purged", "restored", "manual"}
        status = status if status in allowed else "all"
        with self.database.connect() as conn:
            rows = [dict(row) for row in conn.execute(
                """SELECT 'trash' record_type,d.id,d.status,
                          COALESCE(t.metadata_title,t.title,d.original_path) title_name,
                          d.original_path path,
                          COALESCE(json_extract(d.file_snapshot,'$.filename'),d.original_path) filename,
                          d.size_bytes,d.moved_at,d.restored_at,d.purged_at,
                          NULL verified_at
                   FROM duplicate_trash d
                   LEFT JOIN titles t ON t.id=d.title_id
                   UNION ALL
                   SELECT 'manual' record_type,m.id,'manual_deleted' status,
                          COALESCE(t.metadata_title,t.title,m.filename) title_name,
                          m.path,m.filename,m.size_bytes,NULL moved_at,NULL restored_at,
                          NULL purged_at,m.verified_at
                   FROM duplicate_manual_removals m
                   LEFT JOIN titles t ON t.id=m.title_id"""
            )]
        for row in rows:
            row["occurred_at"] = (
                row.get("verified_at") or row.get("purged_at")
                or row.get("restored_at") or row.get("moved_at")
            )
            row["physically_reclaimed"] = row["status"] in {
                "purged", "manual_deleted"
            }
            row["action_label"] = {
                "trashed": "Moved to managed Trash",
                "restored": "Restored",
                "purged": "Permanently removed",
                "missing": "Missing from managed Trash",
                "manual_deleted": "Manual deletion verified",
            }.get(row["status"], row["status"].replace("_", " ").title())
        if status == "pending":
            rows = [row for row in rows if row["status"] == "trashed"]
        elif status == "purged":
            rows = [row for row in rows if row["status"] == "purged"]
        elif status == "restored":
            rows = [row for row in rows if row["status"] == "restored"]
        elif status == "manual":
            rows = [row for row in rows if row["status"] == "manual_deleted"]
        rows.sort(key=lambda row: row.get("occurred_at") or "", reverse=True)
        return rows[:max(1, min(int(limit), 250))]

    def items(self) -> list[dict]:
        with self.database.connect() as conn:
            return [dict(row) for row in conn.execute(
                """SELECT d.*,COALESCE(t.metadata_title,t.title) title_name,r.label root_label
                   FROM duplicate_trash d JOIN titles t ON t.id=d.title_id
                   LEFT JOIN roots r ON r.id=d.root_id
                   WHERE d.status='trashed' ORDER BY d.moved_at DESC"""
            )]

    def restore(self, trash_id: int) -> str:
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT d.*,r.path root_path FROM duplicate_trash d
                   LEFT JOIN roots r ON r.id=d.root_id
                   WHERE d.id=? AND d.status='trashed'""",
                (trash_id,),
            ).fetchone()
        if not row:
            raise DuplicateTrashError("That trash item is no longer available to restore.")
        if not row["root_path"]:
            raise DuplicateTrashError(
                "Restore stopped because the configured source for this trash item is no longer available. No file was changed."
            )
        source = Path(row["trash_path"])
        destination = Path(row["original_path"])
        root = Path(row["root_path"])
        self._require_inside(source, root / ".infomancer-trash")
        self._require_inside(destination, root)
        if destination.exists():
            raise DuplicateTrashError(
                f"Restore stopped because another file already exists at the original path: {destination}. Move that file elsewhere before restoring."
            )
        if not source.is_file():
            with self.database.connect() as conn:
                conn.execute("UPDATE duplicate_trash SET status='missing' WHERE id=?", (trash_id,))
            raise DuplicateTrashError(
                "The trashed file is missing from InfoMancer’s managed trash folder, so it could not be restored."
            )
        snapshot = json.loads(row["file_snapshot"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        snapshot["path"] = str(destination)
        try:
            with self.database.connect() as conn:
                columns = ",".join(FILE_COLUMNS)
                placeholders = ",".join("?" for _ in FILE_COLUMNS)
                conn.execute(
                    f"INSERT INTO files({columns}) VALUES ({placeholders})",
                    [snapshot.get(column) for column in FILE_COLUMNS],
                )
                conn.execute(
                    "UPDATE duplicate_trash SET status='restored',restored_at=CURRENT_TIMESTAMP WHERE id=?",
                    (trash_id,),
                )
        except Exception:
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination), str(source))
            raise
        return str(destination)

    def purge_expired(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT d.*,r.path root_path FROM duplicate_trash d
                   LEFT JOIN roots r ON r.id=d.root_id
                   WHERE d.status='trashed' AND d.purge_after IS NOT NULL
                     AND d.purge_after<=?""", (now,)
            ).fetchall()
        purged = 0
        for row in rows:
            path = Path(row["trash_path"])
            if row["root_path"]:
                self._require_inside(path, Path(row["root_path"]) / ".infomancer-trash")
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            with self.database.connect() as conn:
                conn.execute(
                    "UPDATE duplicate_trash SET status='purged',purged_at=CURRENT_TIMESTAMP WHERE id=?",
                    (row["id"],),
                )
            purged += 1
        return purged

    def _catalog_file(self, file_id: int) -> dict:
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT f.*,t.root_id,r.path root_path,r.label root_label,
                          COALESCE(t.metadata_title,t.title) title_name
                   FROM files f JOIN titles t ON t.id=f.title_id
                   JOIN roots r ON r.id=t.root_id WHERE f.id=?""", (file_id,)
            ).fetchone()
        if not row:
            raise DuplicateTrashError(
                "That file is no longer in the catalog. Rescan the source and review the current duplicate candidates."
            )
        return dict(row)

    @staticmethod
    def _require_inside(path: Path, parent: Path) -> None:
        try:
            path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        except ValueError as exc:
            raise DuplicateTrashError(
                "InfoMancer stopped because the selected path is outside its configured source. No file was changed."
            ) from exc

    @staticmethod
    def _purge_after(retention_days: int | None) -> str | None:
        if retention_days is None:
            return None
        return (datetime.now(timezone.utc) + timedelta(days=retention_days)).isoformat()

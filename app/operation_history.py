from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .db import Database


class OperationHistoryError(ValueError):
    pass


class OperationHistoryService:
    """Durable history for completed operations with narrowly defined safe undo."""

    ALLOWED_STATUS = {"all", "completed", "undone"}
    ALLOWED_KIND = {
        "all", "rename_file", "rename_folder", "managed_trash_move",
        "managed_trash_restore",
    }

    def __init__(self, database: Database) -> None:
        self.database = database

    def _persisted_actor(self, actor_user_id: int | None) -> int | None:
        """Return a real user id, or NULL for synthetic auth-disabled identities."""
        if not actor_user_id or actor_user_id <= 0:
            return None
        with self.database.connect() as conn:
            row = conn.execute("SELECT id FROM users WHERE id=?", (actor_user_id,)).fetchone()
        return int(row["id"]) if row else None

    def record(
        self, operation_type: str, summary: str, *, actor_user_id: int | None = None,
        title_id: int | None = None, file_id: int | None = None,
        root_id: int | None = None, undo_kind: str | None = None,
        undo_payload: dict[str, Any] | None = None, detail: str = "",
    ) -> int:
        actor_user_id = self._persisted_actor(actor_user_id)
        with self.database.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO operation_history(
                     operation_type,status,summary,detail,actor_user_id,title_id,file_id,
                     root_id,undo_kind,undo_payload
                   ) VALUES (?,'completed',?,?,?,?,?,?,?,?)""",
                (
                    operation_type, summary[:500], detail[:2000], actor_user_id,
                    title_id, file_id, root_id, undo_kind,
                    json.dumps(undo_payload or {}, sort_keys=True, separators=(",", ":")),
                ),
            )
            return int(cursor.lastrowid)

    def record_file_rename(
        self, file_id: int, source: Path | str, destination: Path | str,
        actor_user_id: int | None, *, label: str = "Media file renamed",
    ) -> int:
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT f.title_id,t.root_id,COALESCE(t.metadata_title,t.title) title_name
                   FROM files f JOIN titles t ON t.id=f.title_id WHERE f.id=?""",
                (file_id,),
            ).fetchone()
        if not row:
            raise OperationHistoryError("The renamed file is no longer in the catalog.")
        source_path, destination_path = Path(source), Path(destination)
        return self.record(
            "rename_file", f"{label}: {destination_path.name}",
            actor_user_id=actor_user_id, title_id=row["title_id"], file_id=file_id,
            root_id=row["root_id"], undo_kind="rename_file",
            undo_payload={
                "file_id": file_id, "source": str(source_path),
                "destination": str(destination_path),
            },
            detail=f"{source_path} → {destination_path}",
        )

    def record_folder_rename(
        self, title_id: int, source: Path | str, destination: Path | str,
        actor_user_id: int | None,
    ) -> int:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT root_id,COALESCE(metadata_title,title) title_name FROM titles WHERE id=?",
                (title_id,),
            ).fetchone()
        if not row:
            raise OperationHistoryError("The renamed title is no longer in the catalog.")
        source_path, destination_path = Path(source), Path(destination)
        return self.record(
            "rename_folder", f"Show folder renamed: {row['title_name']}",
            actor_user_id=actor_user_id, title_id=title_id, root_id=row["root_id"],
            undo_kind="rename_folder",
            undo_payload={
                "title_id": title_id, "source": str(source_path),
                "destination": str(destination_path),
            },
            detail=f"{source_path} → {destination_path}",
        )

    def record_trash_move(self, trash_id: int, actor_user_id: int | None) -> int:
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT d.original_file_id,d.title_id,d.root_id,d.original_path,d.trash_path,
                          COALESCE(t.metadata_title,t.title,d.original_path) title_name
                   FROM duplicate_trash d LEFT JOIN titles t ON t.id=d.title_id WHERE d.id=?""",
                (trash_id,),
            ).fetchone()
        if not row:
            raise OperationHistoryError("The managed-trash item could not be recorded.")
        return self.record(
            "managed_trash_move", f"Duplicate copy moved to managed Trash: {row['title_name']}",
            actor_user_id=actor_user_id, title_id=row["title_id"],
            file_id=row["original_file_id"], root_id=row["root_id"],
            undo_kind="managed_trash_restore", undo_payload={"trash_id": trash_id},
            detail=f"{row['original_path']} → {row['trash_path']}",
        )

    def mark_trash_restored(self, trash_id: int, actor_user_id: int | None, path: str) -> int:
        actor_user_id = self._persisted_actor(actor_user_id)
        with self.database.connect() as conn:
            original = conn.execute(
                """SELECT id FROM operation_history
                   WHERE operation_type='managed_trash_move' AND status='completed'
                     AND json_extract(undo_payload,'$.trash_id')=?
                   ORDER BY id DESC LIMIT 1""",
                (trash_id,),
            ).fetchone()
            if original:
                conn.execute(
                    """UPDATE operation_history SET status='undone',undone_at=CURRENT_TIMESTAMP,
                         undone_by=?,undo_error='' WHERE id=?""",
                    (actor_user_id, original["id"]),
                )
            trash = conn.execute(
                "SELECT title_id,root_id,original_file_id FROM duplicate_trash WHERE id=?",
                (trash_id,),
            ).fetchone()
        return self.record(
            "managed_trash_restore", "Managed-trash file restored",
            actor_user_id=actor_user_id,
            title_id=trash["title_id"] if trash else None,
            file_id=trash["original_file_id"] if trash else None,
            root_id=trash["root_id"] if trash else None,
            detail=path,
        )

    def list(
        self, *, status: str = "all", kind: str = "all", limit: int = 200,
    ) -> list[dict[str, Any]]:
        status = status if status in self.ALLOWED_STATUS else "all"
        kind = kind if kind in self.ALLOWED_KIND else "all"
        clauses: list[str] = []
        params: list[Any] = []
        if status != "all":
            clauses.append("o.status=?")
            params.append(status)
        else:
            clauses.append("o.status!='undoing'")
        if kind != "all":
            clauses.append("o.operation_type=?")
            params.append(kind)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(int(limit), 500)))
        with self.database.connect() as conn:
            rows = conn.execute(
                f"""SELECT o.*,u.display_name actor_name,
                            COALESCE(t.metadata_title,t.title) title_name
                     FROM operation_history o
                     LEFT JOIN users u ON u.id=o.actor_user_id
                     LEFT JOIN titles t ON t.id=o.title_id
                     {where}
                     ORDER BY o.created_at DESC,o.id DESC LIMIT ?""",
                tuple(params),
            ).fetchall()
        return [self._view(row) for row in rows]

    def counts(self) -> dict[str, int]:
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) total,
                          SUM(CASE WHEN status='completed' AND undo_kind IS NOT NULL THEN 1 ELSE 0 END) undoable,
                          SUM(CASE WHEN status='undone' THEN 1 ELSE 0 END) undone
                   FROM operation_history"""
            ).fetchone()
        return {
            "total": int(row["total"] or 0),
            "undoable": int(row["undoable"] or 0),
            "undone": int(row["undone"] or 0),
        }

    def undo(self, operation_id: int, actor_user_id: int | None, *, duplicate_trash=None) -> str:
        actor_user_id = self._persisted_actor(actor_user_id)
        with self.database.connect() as conn:
            row = conn.execute("SELECT * FROM operation_history WHERE id=?", (operation_id,)).fetchone()
            if not row:
                raise OperationHistoryError("That operation no longer exists.")
            if row["status"] == "undone":
                raise OperationHistoryError("That operation has already been undone.")
            if row["status"] != "completed" or not row["undo_kind"]:
                raise OperationHistoryError("That operation does not have a safe automatic undo.")
            claimed = conn.execute(
                "UPDATE operation_history SET status='undoing',undo_error='' WHERE id=? AND status='completed'",
                (operation_id,),
            )
            if not claimed.rowcount:
                raise OperationHistoryError("That operation is already being changed. Refresh and try again.")
            payload = json.loads(row["undo_payload"] or "{}")
            undo_kind = row["undo_kind"]
        try:
            if undo_kind == "rename_file":
                message = self._undo_file_rename(payload)
            elif undo_kind == "rename_folder":
                message = self._undo_folder_rename(payload)
            elif undo_kind == "managed_trash_restore":
                if duplicate_trash is None:
                    raise OperationHistoryError("Managed Trash is unavailable, so this operation cannot be undone right now.")
                try:
                    restored = duplicate_trash.restore(int(payload["trash_id"]))
                except (ValueError, OSError, sqlite3.Error) as exc:
                    raise OperationHistoryError(str(exc)) from exc
                message = f"Restored file to {restored}"
            else:
                raise OperationHistoryError("That undo type is not supported.")
        except Exception as exc:
            error = str(exc)[:1000] or "Undo could not be completed."
            with self.database.connect() as conn:
                conn.execute(
                    "UPDATE operation_history SET status='completed',undo_error=? WHERE id=? AND status='undoing'",
                    (error, operation_id),
                )
            if isinstance(exc, OperationHistoryError):
                raise
            raise OperationHistoryError(error) from exc
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE operation_history SET status='undone',undone_at=CURRENT_TIMESTAMP,
                     undone_by=?,undo_error='' WHERE id=? AND status='undoing'""",
                (actor_user_id, operation_id),
            )
        return message

    def _undo_file_rename(self, payload: dict[str, Any]) -> str:
        file_id = int(payload.get("file_id") or 0)
        source = Path(str(payload.get("source") or ""))
        destination = Path(str(payload.get("destination") or ""))
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT f.path,f.title_id,t.root_id,t.folder_path,r.path root_path
                   FROM files f JOIN titles t ON t.id=f.title_id
                   JOIN roots r ON r.id=t.root_id WHERE f.id=?""",
                (file_id,),
            ).fetchone()
        if not row or row["path"] != str(destination):
            raise OperationHistoryError(
                "Undo stopped because the cataloged file path has changed since this rename. Nothing was changed."
            )
        root = Path(row["root_path"])
        self._require_inside(source, root)
        self._require_inside(destination, root)
        if source.exists():
            raise OperationHistoryError(
                f"Undo stopped because another file already exists at the original path: {source}"
            )
        if not destination.is_file():
            raise OperationHistoryError(
                "Undo stopped because the renamed file is no longer present at the expected path."
            )
        if not source.parent.is_dir():
            raise OperationHistoryError(
                "Undo stopped because the original parent folder no longer exists. Nothing was changed."
            )
        destination.rename(source)
        try:
            with self.database.connect() as conn:
                conn.execute(
                    "UPDATE files SET path=?,filename=? WHERE id=?",
                    (str(source), source.name, file_id),
                )
                if row["folder_path"] == str(destination):
                    conn.execute(
                        "UPDATE titles SET folder_path=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (str(source), row["title_id"]),
                    )
        except Exception:
            source.rename(destination)
            raise
        return f"Restored the previous filename: {source.name}"

    def _undo_folder_rename(self, payload: dict[str, Any]) -> str:
        title_id = int(payload.get("title_id") or 0)
        source = Path(str(payload.get("source") or ""))
        destination = Path(str(payload.get("destination") or ""))
        with self.database.connect() as conn:
            title = conn.execute(
                """SELECT t.folder_path,t.root_id,r.path root_path
                   FROM titles t JOIN roots r ON r.id=t.root_id WHERE t.id=?""",
                (title_id,),
            ).fetchone()
            file_rows = conn.execute(
                "SELECT id,path FROM files WHERE title_id=? ORDER BY id", (title_id,)
            ).fetchall()
        if not title or title["folder_path"] != str(destination):
            raise OperationHistoryError(
                "Undo stopped because the show folder has changed since this rename. Nothing was changed."
            )
        root = Path(title["root_path"])
        self._require_inside(source, root)
        self._require_inside(destination, root)
        if source.exists():
            raise OperationHistoryError(
                f"Undo stopped because another folder already exists at the original path: {source}"
            )
        if not destination.is_dir():
            raise OperationHistoryError(
                "Undo stopped because the renamed show folder is no longer present at the expected path."
            )
        if not source.parent.is_dir():
            raise OperationHistoryError(
                "Undo stopped because the original parent folder no longer exists. Nothing was changed."
            )
        relative_paths: list[tuple[int, Path]] = []
        for file_row in file_rows:
            try:
                relative = Path(file_row["path"]).relative_to(destination)
            except ValueError as exc:
                raise OperationHistoryError(
                    "Undo stopped because a cataloged episode is no longer inside the renamed show folder."
                ) from exc
            relative_paths.append((file_row["id"], relative))
        destination.rename(source)
        try:
            with self.database.connect() as conn:
                conn.execute(
                    "UPDATE titles SET folder_path=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (str(source), title_id),
                )
                for file_id, relative in relative_paths:
                    conn.execute(
                        "UPDATE files SET path=? WHERE id=?", (str(source / relative), file_id)
                    )
        except Exception:
            source.rename(destination)
            raise
        return f"Restored the previous show folder name: {source.name}"

    @staticmethod
    def _require_inside(path: Path, parent: Path) -> None:
        try:
            path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        except ValueError as exc:
            raise OperationHistoryError(
                "Undo stopped because the recorded path is outside its configured source. Nothing was changed."
            ) from exc

    @staticmethod
    def _view(row) -> dict[str, Any]:
        item = dict(row)
        try:
            payload = json.loads(item.get("undo_payload") or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        item["can_undo"] = bool(item.get("undo_kind") and item.get("status") == "completed")
        item["source"] = payload.get("source", "")
        item["destination"] = payload.get("destination", "")
        item["display_type"] = {
            "rename_file": "File rename",
            "rename_folder": "Folder rename",
            "managed_trash_move": "Managed Trash",
            "managed_trash_restore": "Trash restore",
        }.get(item.get("operation_type"), str(item.get("operation_type") or "Operation").replace("_", " ").title())
        return item

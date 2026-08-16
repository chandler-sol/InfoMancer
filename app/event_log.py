from __future__ import annotations

import json
from typing import Any

from .db import Database


LEVELS = {"debug", "info", "warning", "error"}
ACTIVITY_CATEGORIES = {
    "scan", "source-guard", "hashing", "duplicates", "metadata", "mie",
    "media", "media-info", "library",
}


class EventLog:
    """Small structured event log intended for people first and diagnostics second."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def write(
        self,
        category: str,
        message: str,
        *,
        level: str = "info",
        detail: str = "",
        context: dict[str, Any] | None = None,
        user_id: int | None = None,
    ) -> None:
        normalized_level = level if level in LEVELS else "info"
        safe_context = {
            str(key): value for key, value in (context or {}).items()
            if key.lower() not in {"password", "token", "secret", "api_key", "pin"}
        }
        try:
            encoded = json.dumps(safe_context, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            encoded = "{}"
        try:
            with self.database.connect() as conn:
                conn.execute(
                    """INSERT INTO event_logs
                       (level,category,message,detail,context_json,user_id)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        normalized_level, category.strip()[:60] or "system",
                        message.strip()[:1000], detail.strip()[:4000],
                        encoded, user_id if user_id and user_id > 0 else None,
                    ),
                )
                # Keep the embedded log useful without allowing it to grow forever.
                conn.execute(
                    """DELETE FROM event_logs WHERE id IN (
                         SELECT id FROM event_logs ORDER BY id DESC LIMIT -1 OFFSET 50000
                       )"""
                )
        except Exception:
            # Logging must never break a scan, rename, or request.
            return

    def query(
        self, *, level: str = "", category: str = "", search: str = "",
        limit: int = 250, before_id: int | None = None,
    ):
        conditions: list[str] = []
        params: list[Any] = []
        if level in LEVELS:
            conditions.append("level=?")
            params.append(level)
        if category:
            conditions.append("category=?")
            params.append(category)
        if search:
            conditions.append("(message LIKE ? OR detail LIKE ?)")
            term = f"%{search.strip()}%"
            params.extend([term, term])
        if before_id:
            conditions.append("id<?")
            params.append(before_id)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(max(1, min(limit, 50000)))
        with self.database.connect() as conn:
            return conn.execute(
                f"""SELECT e.*, COALESCE(u.display_name,'System') user_name
                    FROM event_logs e LEFT JOIN users u ON u.id=e.user_id
                    {where} ORDER BY e.id DESC LIMIT ?""",
                params,
            ).fetchall()

    def categories(self) -> list[str]:
        with self.database.connect() as conn:
            return [
                row["category"] for row in conn.execute(
                    "SELECT DISTINCT category FROM event_logs ORDER BY category"
                )
            ]

    @staticmethod
    def _activity_href(context: dict[str, Any]) -> str:
        if context.get("finding_id"):
            return f"/library-health#finding-{int(context['finding_id'])}"
        if context.get("title_id"):
            return f"/titles/{int(context['title_id'])}"
        if context.get("collection_id"):
            return f"/collections/{int(context['collection_id'])}"
        if context.get("library_id"):
            return f"/libraries/{int(context['library_id'])}"
        if context.get("root_id"):
            return "/sources"
        if context.get("file_id"):
            return f"/files/{int(context['file_id'])}"
        category = str(context.get("category") or "")
        return {
            "duplicates": "/duplicates", "metadata": "/settings/metadata",
            "media": "/settings/system", "hashing": "/settings/system",
            "scan": "/sources", "source-guard": "/sources",
            "authentication": "/logs?category=authentication",
        }.get(category, "/library-health")

    def activity(self, user_id: int, *, unread_only: bool = False, limit: int = 100) -> list[dict]:
        placeholders = ",".join("?" for _ in ACTIVITY_CATEGORIES)
        unread = "AND ur.event_id IS NULL" if unread_only else ""
        params = [user_id, *sorted(ACTIVITY_CATEGORIES), max(1, min(limit, 250))]
        with self.database.connect() as conn:
            rows = conn.execute(
                f"""SELECT e.*,ur.read_at FROM event_logs e
                    LEFT JOIN user_event_reads ur ON ur.event_id=e.id AND ur.user_id=?
                    WHERE e.category IN ({placeholders})
                      AND (e.user_id IS NULL OR e.user_id=?) {unread}
                    ORDER BY e.id DESC LIMIT ?""",
                [user_id, *sorted(ACTIVITY_CATEGORIES), user_id, params[-1]],
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                context = json.loads(item["context_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                context = {}
            item["context"] = context
            context.setdefault("category", item["category"])
            item["href"] = self._activity_href(context)
            item["unread"] = item["read_at"] is None
            result.append(item)
        return result

    def unread_count(self, user_id: int) -> int:
        return len(self.activity(user_id, unread_only=True, limit=250)) if user_id > 0 else 0

    def mark_read(self, user_id: int, event_ids: list[int] | None = None) -> int:
        if user_id <= 0:
            return 0
        events = self.activity(user_id, unread_only=True, limit=250)
        allowed = {item["id"] for item in events}
        selected = allowed if event_ids is None else allowed.intersection(event_ids)
        with self.database.connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO user_event_reads(user_id,event_id) VALUES (?,?)",
                [(user_id, event_id) for event_id in selected],
            )
        return len(selected)

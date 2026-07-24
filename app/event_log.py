from __future__ import annotations

import json
from typing import Any

from .db import Database


LEVELS = {"debug", "info", "warning", "error"}


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

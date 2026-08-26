from __future__ import annotations

import json
from typing import Any

from .db import Database


LEVELS = {"debug", "info", "warning", "error"}
ACTIVITY_CATEGORIES = {
    "scan", "source-guard", "hashing", "duplicates", "metadata", "mie",
    "media", "media-info", "library",
}
SENSITIVE_CONTEXT_KEYS = {
    "password", "passwd", "passphrase", "token", "secret", "api_key", "apikey",
    "pin", "authorization", "cookie", "session", "session_id", "csrf_token",
}
SENSITIVE_CONTEXT_SUFFIXES = (
    "_password", "_passwd", "_passphrase", "_token", "_secret", "_api_key",
    "_apikey", "_pin", "_authorization", "_cookie", "_session", "_session_id",
)


def _sensitive_context_key(key: object) -> bool:
    normalized = str(key).strip().casefold().replace("-", "_").replace(" ", "_")
    return normalized in SENSITIVE_CONTEXT_KEYS or normalized.endswith(SENSITIVE_CONTEXT_SUFFIXES)


def _safe_context_value(value: Any, *, depth: int = 0) -> Any:
    """Recursively redact credentials while keeping useful diagnostic structure."""
    if depth >= 8:
        return "[truncated]"
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _sensitive_context_key(key)
            else _safe_context_value(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_safe_context_value(item, depth=depth + 1) for item in value]
    return value


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
        safe_context = _safe_context_value(context or {})
        try:
            encoded = json.dumps(safe_context, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            encoded = "{}"
        try:
            with self.database.connect() as conn:
                cursor = conn.execute(
                    """INSERT INTO event_logs
                       (level,category,message,detail,context_json,user_id)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        normalized_level, category.strip()[:60] or "system",
                        message.strip()[:1000], detail.strip()[:4000],
                        encoded, user_id if user_id and user_id > 0 else None,
                    ),
                )
                # A scan can emit thousands of events. Pruning the 50k retention
                # window after every insert made each log write progressively more
                # expensive. Amortize that housekeeping while keeping the maximum
                # overshoot small and deterministic.
                event_id = int(cursor.lastrowid or 0)
                if event_id > 0 and event_id % 128 == 0:
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
        """Count unread activity without loading and decoding up to 250 event rows."""
        if user_id <= 0:
            return 0
        placeholders = ",".join("?" for _ in ACTIVITY_CATEGORIES)
        with self.database.connect() as conn:
            row = conn.execute(
                f"""SELECT COUNT(*) count FROM (
                      SELECT e.id FROM event_logs e
                      LEFT JOIN user_event_reads ur
                        ON ur.event_id=e.id AND ur.user_id=?
                      WHERE e.category IN ({placeholders})
                        AND (e.user_id IS NULL OR e.user_id=?)
                        AND ur.event_id IS NULL
                      ORDER BY e.id DESC LIMIT 250
                    )""",
                [user_id, *sorted(ACTIVITY_CATEGORIES), user_id],
            ).fetchone()
        return int(row["count"] if row else 0)

    def mark_read(self, user_id: int, event_ids: list[int] | None = None) -> int:
        if user_id <= 0:
            return 0
        if event_ids is None:
            # "Mark all" means the entire visible Activity inbox, not merely the
            # 250-row window used to keep interactive list/count queries bounded.
            # Keep the visibility predicate in SQL so account-local events cannot
            # be marked by another user and avoid materializing thousands of rows.
            categories = sorted(ACTIVITY_CATEGORIES)
            placeholders = ",".join("?" for _ in categories)
            with self.database.connect() as conn:
                before = conn.total_changes
                conn.execute(
                    f"""INSERT OR IGNORE INTO user_event_reads(user_id,event_id)
                        SELECT ?,e.id FROM event_logs e
                        WHERE e.category IN ({placeholders})
                          AND (e.user_id IS NULL OR e.user_id=?)""",
                    [user_id, *categories, user_id],
                )
                return conn.total_changes - before

        events = self.activity(user_id, unread_only=True, limit=250)
        allowed = {item["id"] for item in events}
        selected = allowed.intersection(event_ids)
        with self.database.connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO user_event_reads(user_id,event_id) VALUES (?,?)",
                [(user_id, event_id) for event_id in selected],
            )
        return len(selected)

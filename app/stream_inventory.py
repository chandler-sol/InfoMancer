from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import Database


class MediaStreamService:
    """Persist and query the complete FFprobe stream inventory for catalog files."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _rows(file_id: int, streams: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
        rows: list[tuple[Any, ...]] = []
        for ordinal, stream in enumerate(streams):
            raw_index = stream.get("index")
            stream_index = ordinal if raw_index is None else int(raw_index)
            rows.append((
                int(file_id),
                stream_index,
                str(stream.get("type") or "unknown")[:20],
                str(stream.get("codec") or "")[:80],
                str(stream.get("language") or "und")[:20],
                str(stream.get("title") or "")[:500],
                int(stream["channels"]) if stream.get("channels") is not None else None,
                str(stream.get("channel_layout") or "")[:80],
                int(stream["sample_rate"]) if stream.get("sample_rate") is not None else None,
                int(bool(stream.get("default"))),
                int(bool(stream.get("forced"))),
                int(bool(stream.get("hearing_impaired"))),
                int(bool(stream.get("visual_impaired"))),
                int(bool(stream.get("commentary"))),
                json.dumps(
                    stream.get("disposition") or {},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ))
        return rows

    def replace(
        self,
        file_id: int,
        streams: list[dict[str, Any]],
        *,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        """Atomically replace one file's inventory.

        When no connection is supplied, Database.connect supplies the transaction so
        a failed replacement rolls the preceding delete back with the failed insert.
        Callers that already update the file's first-track summary may pass that same
        connection so both representations commit together.
        """
        rows = self._rows(file_id, streams)
        if conn is None:
            with self.database.connect() as connection:
                self.replace(file_id, streams, conn=connection)
            return

        conn.execute("DELETE FROM media_streams WHERE file_id=?", (file_id,))
        if rows:
            conn.executemany(
                """INSERT INTO media_streams(
                     file_id,stream_index,stream_type,codec,language,title,channels,
                     channel_layout,sample_rate,default_flag,forced_flag,
                     hearing_impaired,visual_impaired,commentary,disposition_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )

    def file_streams(self, file_id: int) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM media_streams
                   WHERE file_id=? ORDER BY stream_index""",
                (file_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def title_summary(self, title_id: int) -> dict[str, Any]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT s.stream_type,s.language,s.codec,COUNT(*) count
                   FROM media_streams s
                   JOIN files f ON f.id=s.file_id
                   WHERE f.title_id=?
                   GROUP BY s.stream_type,s.language,s.codec
                   ORDER BY s.stream_type,s.language,s.codec""",
                (title_id,),
            ).fetchall()
            inspected = int(conn.execute(
                """SELECT COUNT(DISTINCT s.file_id)
                   FROM media_streams s
                   JOIN files f ON f.id=s.file_id
                   WHERE f.title_id=?""",
                (title_id,),
            ).fetchone()[0])
        return {
            "inspected_files": inspected,
            "groups": [dict(row) for row in rows],
        }

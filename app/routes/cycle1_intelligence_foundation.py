from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter

from ..media_info import MediaInspectionError
from ..stream_inventory import MediaStreamService
from .context import RouteContext


def build_router(ctx: RouteContext):
    """Install Cycle 1 service replacements without adding new HTTP routes."""
    router = APIRouter()
    db = ctx.live("db")
    inspect_media = ctx.live("inspect_media")
    media_info_job = ctx.live("media_info_job")
    media_info_lock = ctx.live("media_info_lock")
    record_event = ctx.live("record_event")

    mie = ctx.live("mie")
    media_streams = MediaStreamService(db)

    def run_media_inspection(file_ids: list[int] | None = None) -> None:
        """Collect legacy media facts and complete stream inventories atomically."""
        with media_info_lock:
            media_info_job.clear()
            media_info_job.update({
                "status": "running", "processed": 0, "total": 0,
                "updated": 0, "errors": 0, "current": "",
            })
        with db.connect() as conn:
            if file_ids is not None:
                if file_ids:
                    placeholders = ",".join("?" for _ in file_ids)
                    rows = conn.execute(
                        f"""SELECT f.id,f.path,f.filename,t.metadata_title,t.title
                            FROM files f JOIN titles t ON t.id=f.title_id
                            WHERE f.id IN ({placeholders}) ORDER BY f.id""",
                        tuple(file_ids),
                    ).fetchall()
                else:
                    rows = []
            else:
                rows = conn.execute(
                    """SELECT f.id,f.path,f.filename,t.metadata_title,t.title
                       FROM files f JOIN titles t ON t.id=f.title_id
                       WHERE f.media_info_at IS NULL OR
                         (f.media_info_error IS NOT NULL AND f.media_info_error!='')
                       ORDER BY f.id"""
                ).fetchall()
        with media_info_lock:
            media_info_job["total"] = len(rows)
        record_event(
            "media", f"Media inspection started for {len(rows):,} files.",
            context={"file_count": len(rows)},
        )
        updated = errors = 0
        for index, row in enumerate(rows, start=1):
            label = f"{row['metadata_title'] or row['title']} · {row['filename']}"
            with media_info_lock:
                media_info_job.update({"processed": index - 1, "current": label})
            try:
                values = inspect_media(Path(row["path"]))
                with db.connect() as conn:
                    conn.execute(
                        """UPDATE files SET runtime_seconds=?,width=?,height=?,
                           video_codec=?,audio_codec=?,audio_channels=?,bitrate=?,
                           container=?,dynamic_range=?,media_info_at=CURRENT_TIMESTAMP,
                           media_info_error=NULL WHERE id=?""",
                        (
                            values["runtime_seconds"], values["width"], values["height"],
                            values["video_codec"], values["audio_codec"],
                            values["audio_channels"], values["bitrate"],
                            values["container"], values["dynamic_range"], row["id"],
                        ),
                    )
                    media_streams.replace(
                        int(row["id"]), values.get("streams") or [], conn=conn,
                    )
                updated += 1
                event_values = {
                    key: value for key, value in values.items() if key != "streams"
                }
                record_event(
                    "media", f"Media details collected for {row['filename']}.",
                    level="verbose",
                    context={
                        "file_id": row["id"],
                        "stream_count": len(values.get("streams") or []),
                        **event_values,
                    },
                )
            except MediaInspectionError as exc:
                errors += 1
                with db.connect() as conn:
                    conn.execute(
                        """UPDATE files SET media_info_at=CURRENT_TIMESTAMP,
                           media_info_error=? WHERE id=?""",
                        (str(exc), row["id"]),
                    )
                record_event(
                    "media",
                    f"{exc.headline}: {row['filename']}",
                    level="warning", detail=exc.log_detail,
                    context={"file_id": row["id"], "path": row["path"]},
                )
            except sqlite3.Error as exc:
                # A catalog persistence failure must not be mislabeled as damaged media.
                # Database.connect rolls the file summary and stream replacement back
                # together, so the previous catalog state remains intact.
                errors += 1
                record_event(
                    "media",
                    f"Media details could not be saved for {row['filename']}.",
                    level="error", detail=str(exc),
                    context={
                        "file_id": row["id"], "path": row["path"],
                        "operation": "media-stream-persistence",
                    },
                )
            with media_info_lock:
                media_info_job.update({
                    "processed": index, "updated": updated, "errors": errors,
                })
        with media_info_lock:
            media_info_job.update({
                "status": "complete", "processed": len(rows), "updated": updated,
                "errors": errors, "current": "",
            })
        record_event(
            "media",
            f"Media inspection finished: {updated:,} files updated and {errors:,} could not be read or saved.",
            level="warning" if errors else "info",
            context={"updated": updated, "errors": errors},
        )

    return router, {
        "mie": mie,
        "media_streams": media_streams,
        "run_media_inspection": run_media_inspection,
    }

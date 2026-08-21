from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ..access import require_librarian
from .context import RouteContext


def _runtime_display(seconds: int | None) -> str:
    duration = int(seconds or 0)
    if duration <= 0:
        return ""
    minutes = max(1, round(duration / 60))
    if minutes >= 60:
        hours, remainder = divmod(minutes, 60)
        return f"{hours}h {remainder}m" if remainder else f"{hours}h"
    return f"{minutes} min"


def _size_gb(value: int | None) -> str:
    size = float(value or 0) / 1_073_741_824
    return f"{size:.2f}".rstrip("0").rstrip(".")


def _bitrate_display(value: int | None) -> str:
    bitrate = int(value or 0)
    if bitrate <= 0:
        return ""
    if bitrate >= 1_000_000:
        mbps = bitrate / 1_000_000
        precision = 1 if mbps < 100 else 0
        return f"{mbps:.{precision}f} Mbps"
    return f"{round(bitrate / 1000):,} kbps"


def _resolution_label(width: int | None, height: int | None) -> str:
    if not width or not height:
        return ""
    width, height = int(width), int(height)
    if width >= 3800 or height >= 2000:
        return "4K UHD"
    if width >= 2500 or height >= 1400:
        return "1440p"
    if width >= 1900 or height >= 1000:
        return "1080p"
    if width >= 1200 or height >= 700:
        return "720p"
    return f"{width} × {height}"


def build_router(ctx: RouteContext):
    """Serve title-scoped media inspection without forcing a detail-page reload."""
    router = APIRouter()
    db = ctx.live("db")
    media_info_job = ctx.live("media_info_job")
    media_info_lock = ctx.live("media_info_lock")
    record_event = ctx.live("record_event")
    redirect = ctx.live("redirect")
    run_media_inspection = ctx.live("run_media_inspection")
    threading = ctx.live("threading")

    def async_request(request: Request) -> bool:
        return (
            request.headers.get("x-infomancer-async") == "1"
            or "application/json" in request.headers.get("accept", "")
        )

    def action_error(
        request: Request, title_id: int, detail: str, *, status_code: int = 400,
        library: bool = False,
    ):
        if async_request(request):
            return JSONResponse(
                {"started": False, "detail": detail}, status_code=status_code,
            )
        return redirect("/library" if library else f"/titles/{title_id}", detail)

    def media_snapshot(title_id: int) -> dict:
        with db.connect() as conn:
            title = conn.execute(
                """SELECT t.id,t.kind,t.root_id,t.metadata_status,r.label source_label
                   FROM titles t JOIN roots r ON r.id=t.root_id WHERE t.id=?""",
                (title_id,),
            ).fetchone()
            if not title:
                raise HTTPException(404, "Title not found")
            rows = conn.execute(
                """SELECT f.* FROM files f
                   WHERE f.title_id=? ORDER BY f.season,f.episode_start,f.filename""",
                (title_id,),
            ).fetchall()
            expected_count = int(conn.execute(
                "SELECT COUNT(*) count FROM expected_episodes WHERE title_id=?",
                (title_id,),
            ).fetchone()["count"])

        files = list(rows)
        inspected_count = sum(1 for row in files if row["media_info_at"])
        error_count = sum(1 for row in files if row["media_info_error"])
        technical_file = next(
            (row for row in files if row["version_preferred"]),
            files[0] if files else None,
        )

        facts: list[dict[str, str]] = []
        if title["source_label"]:
            facts.append({"label": "Source", "value": str(title["source_label"])})

        if error_count:
            facts.append({
                "label": "Media",
                "value": f"{error_count} need{'s' if error_count == 1 else ''} attention",
                "tone": "warning",
            })
        elif files and inspected_count == 0:
            facts.append({"label": "Media", "value": "Not inspected yet", "tone": "muted"})

        if title["metadata_status"]:
            facts.append({"label": "Status", "value": str(title["metadata_status"])})

        runtime_values = [int(row["runtime_seconds"]) for row in files if row["runtime_seconds"]]
        if title["kind"] == "tv":
            seasons = {
                int(row["season"]) for row in files
                if row["season"] is not None and int(row["season"]) > 0
            }
            if seasons:
                facts.append({"label": "Seasons", "value": str(len(seasons))})
            if expected_count:
                facts.append({"label": "Episodes", "value": str(expected_count)})
        elif runtime_values:
            facts.append({"label": "Runtime", "value": _runtime_display(max(runtime_values))})

        if technical_file:
            resolution = _resolution_label(
                technical_file["width"], technical_file["height"],
            )
            if resolution:
                facts.append({"label": "Resolution", "value": resolution})
            if technical_file["video_codec"]:
                facts.append({
                    "label": "Video", "value": str(technical_file["video_codec"]).upper(),
                })
            bitrate = _bitrate_display(technical_file["bitrate"])
            if bitrate:
                facts.append({"label": "Bitrate", "value": bitrate})
            if technical_file["audio_codec"]:
                audio = str(technical_file["audio_codec"]).upper()
                if technical_file["audio_channels"]:
                    channels = float(technical_file["audio_channels"])
                    channel_text = (
                        str(int(channels)) if channels.is_integer()
                        else f"{channels:g}"
                    )
                    audio = f"{audio} · {channel_text}ch"
                facts.append({"label": "Audio", "value": audio})
            if technical_file["dynamic_range"]:
                facts.append({
                    "label": "Range", "value": str(technical_file["dynamic_range"]),
                })
            if technical_file["container"]:
                facts.append({
                    "label": "Container", "value": str(technical_file["container"]).upper(),
                })

        file_views = []
        for row in files:
            container = str(row["container"] or str(row["extension"] or "").lstrip(".")).upper()
            size = f"{_size_gb(row['size_bytes'])} GB"
            runtime = _runtime_display(row["runtime_seconds"])
            resolution = (
                f"{int(row['width'])}×{int(row['height'])}"
                if row["width"] and row["height"] else ""
            )
            video = str(row["video_codec"] or "").upper()
            audio = str(row["audio_codec"] or "").upper()
            bitrate = _bitrate_display(row["bitrate"])
            detail_parts = [size]
            if runtime:
                detail_parts.append(runtime)
            if resolution:
                detail_parts.append(resolution)
            if video:
                detail_parts.append(video)
            if bitrate:
                detail_parts.append(bitrate)
            if audio:
                detail_parts.append(f"/ {audio}")
            if row["dynamic_range"]:
                detail_parts.append(str(row["dynamic_range"]))
            if container:
                detail_parts.append(container)
            detail_parts.append(str(row["path"]))
            summary_parts = [size, container or "FILE"]
            if bitrate:
                summary_parts.append(bitrate)
            if title["kind"] == "movie":
                summary_parts.append("Main feature")
            file_views.append({
                "id": int(row["id"]),
                "filename": str(row["filename"]),
                "path": str(row["path"]),
                "kind": str(title["kind"]),
                "summary": " · ".join(summary_parts),
                "detail": " · ".join(detail_parts),
                "media_info_error": str(row["media_info_error"] or ""),
                "media_info_at": str(row["media_info_at"] or ""),
            })

        return {
            "title_id": title_id,
            "kind": str(title["kind"]),
            "source_href": f"/library?root={int(title['root_id'])}",
            "facts": facts,
            "files": file_views,
            "inspected_count": inspected_count,
            "error_count": error_count,
        }

    @router.post(
        "/titles/{title_id}/media-info",
        dependencies=[Depends(require_librarian)],
    )
    def inspect_title_media_action(request: Request, title_id: int):
        with db.connect() as conn:
            title = conn.execute(
                "SELECT id, metadata_title, title FROM titles WHERE id=?", (title_id,)
            ).fetchone()
            if not title:
                return action_error(
                    request, title_id,
                    "Media inspection could not start because that title no longer exists.",
                    status_code=404, library=True,
                )
            file_rows = conn.execute(
                """SELECT id,modified_at,media_info_at,media_info_error
                   FROM files WHERE title_id=? ORDER BY id""",
                (title_id,),
            ).fetchall()

        if not file_rows:
            return action_error(
                request, title_id,
                "Media inspection found no files for this title. Rescan its source, then try again.",
                status_code=409,
            )

        # A normal Inspect action only touches files whose cataloged mtime is newer
        # than the last successful probe, have never been inspected, or previously
        # failed inspection. This makes a repeated click a cheap freshness check.
        with db.connect() as conn:
            stale_ids = [
                int(row["id"])
                for row in conn.execute(
                    """SELECT id FROM files
                       WHERE title_id=? AND (
                         media_info_at IS NULL
                         OR COALESCE(media_info_error,'') <> ''
                         OR (
                           modified_at IS NOT NULL
                           AND datetime(media_info_at) < datetime(modified_at, 'unixepoch')
                         )
                       )
                       ORDER BY id""",
                    (title_id,),
                ).fetchall()
            ]

        if not stale_ids:
            detail = "Media information is up to date."
            if async_request(request):
                return JSONResponse({
                    "started": False,
                    "up_to_date": True,
                    "title_id": title_id,
                    "total": 0,
                    "detail": detail,
                })
            return redirect(f"/titles/{title_id}", detail)

        with media_info_lock:
            if media_info_job.get("status") in {"starting", "running"}:
                return action_error(
                    request, title_id,
                    "Media inspection is already running. Its progress is available in the task widget.",
                    status_code=409,
                )
            media_info_job.clear()
            media_info_job.update(
                {
                    "status": "starting",
                    "processed": 0,
                    "total": len(stale_ids),
                    "updated": 0,
                    "errors": 0,
                    "current": "",
                    "title_id": title_id,
                }
            )

        def run_scoped_inspection() -> None:
            run_media_inspection(stale_ids)
            # run_media_inspection owns and clears the shared job dictionary. Restore
            # the title scope when it finishes so the detail page can identify the
            # completed request without changing the legacy worker contract.
            with media_info_lock:
                media_info_job["title_id"] = title_id

        threading.Thread(target=run_scoped_inspection, daemon=True).start()
        record_event(
            "media",
            f"Media inspection requested for {title['metadata_title'] or title['title']}.",
            context={"title_id": title_id, "files": len(stale_ids)},
            user_id=request.state.user.id,
        )
        message = (
            f"Media inspection started for {len(stale_ids)} changed "
            f"file{'s' if len(stale_ids) != 1 else ''}."
        )
        if async_request(request):
            return JSONResponse({
                "started": True,
                "title_id": title_id,
                "total": len(stale_ids),
                "detail": message,
            })
        return redirect(
            f"/titles/{title_id}",
            f"{message} Progress is shown in the task widget.",
        )

    @router.get("/api/titles/{title_id}/media-info-state")
    def title_media_info_state(title_id: int, snapshot: str = "1"):
        with media_info_lock:
            task = {
                key: media_info_job.get(key)
                for key in (
                    "status", "processed", "total", "updated", "errors",
                    "current", "title_id", "error", "detail",
                )
                if key in media_info_job
            }
        task.setdefault("status", "idle")
        result = {"task": task}
        if snapshot != "0":
            result["snapshot"] = media_snapshot(title_id)
        return result

    return router, {
        "inspect_title_media_action": inspect_title_media_action,
        "title_media_info_state": title_media_info_state,
    }

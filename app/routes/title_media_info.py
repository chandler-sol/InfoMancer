from fastapi import APIRouter, Depends

from ..access import require_librarian
from .context import RouteContext


def build_router(ctx: RouteContext):
    """Serve the title-scoped media inspection action with shared task state.

    This route intentionally precedes the legacy title route while the title
    bundle is being split up. The older handler rebinds its module-level
    ``media_info_job`` name, which disconnects it from the BackgroundCoordinator
    state and can raise a NameError after the route extraction.
    """
    router = APIRouter()
    db = ctx.live("db")
    media_info_job = ctx.live("media_info_job")
    media_info_lock = ctx.live("media_info_lock")
    record_event = ctx.live("record_event")
    redirect = ctx.live("redirect")
    run_media_inspection = ctx.live("run_media_inspection")
    threading = ctx.live("threading")

    @router.post(
        "/titles/{title_id}/media-info",
        dependencies=[Depends(require_librarian)],
    )
    def inspect_title_media_action(title_id: int):
        with db.connect() as conn:
            title = conn.execute(
                "SELECT id, metadata_title, title FROM titles WHERE id=?", (title_id,)
            ).fetchone()
            if not title:
                return redirect(
                    "/library",
                    "Media inspection could not start because that title no longer exists.",
                )
            file_ids = [
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM files WHERE title_id=? ORDER BY id", (title_id,)
                ).fetchall()
            ]

        if not file_ids:
            return redirect(
                f"/titles/{title_id}",
                "Media inspection found no files for this title. Rescan its source, then try again.",
            )

        with media_info_lock:
            if media_info_job.get("status") in {"starting", "running"}:
                return redirect(
                    f"/titles/{title_id}",
                    "Media inspection is already running. Its progress is available in the task widget.",
                )
            media_info_job.clear()
            media_info_job.update(
                {
                    "status": "starting",
                    "processed": 0,
                    "total": len(file_ids),
                    "updated": 0,
                    "errors": 0,
                    "current": "",
                }
            )

        threading.Thread(
            target=run_media_inspection,
            args=(file_ids,),
            daemon=True,
        ).start()
        record_event(
            "media",
            f"Media inspection requested for {title['metadata_title'] or title['title']}.",
            context={"title_id": title_id, "files": len(file_ids)},
        )
        return redirect(
            f"/titles/{title_id}",
            f"Media inspection started for {len(file_ids)} file{'s' if len(file_ids) != 1 else ''}. Progress is shown in the task widget.",
        )

    return router, {"inspect_title_media_action": inspect_title_media_action}

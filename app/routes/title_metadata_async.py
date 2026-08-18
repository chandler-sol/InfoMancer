from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..access import require_librarian
from .context import RouteContext


def build_router(ctx: RouteContext):
    """Give title-scoped IMDb refresh the same no-navigation contract as media info."""
    router = APIRouter()
    db = ctx.live("db")
    imdb_genre_job = ctx.live("imdb_genre_job")
    imdb_genre_lock = ctx.live("imdb_genre_lock")
    queue_metadata_refresh = ctx.live("queue_metadata_refresh")
    redirect = ctx.live("redirect")

    def async_request(request: Request) -> bool:
        return (
            request.headers.get("x-infomancer-async") == "1"
            or "application/json" in request.headers.get("accept", "")
        )

    @router.post(
        "/titles/{title_id}/imdb-refresh",
        dependencies=[Depends(require_librarian)],
    )
    def refresh_title_metadata(request: Request, title_id: int):
        with db.connect() as conn:
            title = conn.execute(
                """SELECT id,kind,COALESCE(NULLIF(metadata_title,''),title) display_title
                   FROM titles WHERE id=?""",
                (title_id,),
            ).fetchone()
        if not title:
            detail = "Metadata refresh could not start because that title no longer exists."
            if async_request(request):
                return JSONResponse({"started": False, "detail": detail}, status_code=404)
            return redirect("/library", detail)

        label = f"Refreshing metadata for {title['display_title']}"
        detail = queue_metadata_refresh([title_id], request.state.user.id, label)
        with imdb_genre_lock:
            status = str(imdb_genre_job.get("status") or "idle")
            active_ids = imdb_genre_job.get("title_ids")
            started = status in {"starting", "running"} and (
                active_ids is None or title_id in active_ids
            )

        if async_request(request):
            return JSONResponse(
                {
                    "started": started,
                    "title_id": title_id,
                    "detail": detail,
                    "status": status,
                },
                status_code=200 if started else 409,
            )
        return redirect(f"/titles/{title_id}", detail)

    @router.get("/api/titles/{title_id}/metadata-refresh-state")
    def title_metadata_refresh_state(title_id: int):
        with db.connect() as conn:
            title = conn.execute(
                "SELECT id,metadata_refreshed_at,metadata_refresh_error,updated_at FROM titles WHERE id=?",
                (title_id,),
            ).fetchone()
            if not title:
                return JSONResponse({"detail": "Title not found"}, status_code=404)
            queue = conn.execute(
                """SELECT status,requested_at,started_at,completed_at,provider,error
                   FROM metadata_refresh_queue WHERE title_id=?""",
                (title_id,),
            ).fetchone()

        with imdb_genre_lock:
            task = {
                key: imdb_genre_job.get(key)
                for key in (
                    "status", "phase", "scope_label", "title_ids", "records",
                    "matched", "requested", "id_processed", "id_total",
                    "id_found", "id_missing", "id_errors", "error",
                )
                if key in imdb_genre_job
            }
        task.setdefault("status", "idle")
        return {
            "title_id": title_id,
            "task": task,
            "queue": dict(queue) if queue else None,
            "metadata_refreshed_at": title["metadata_refreshed_at"],
            "metadata_refresh_error": title["metadata_refresh_error"],
            "updated_at": title["updated_at"],
        }

    return router, {
        "refresh_title_metadata": refresh_title_metadata,
        "title_metadata_refresh_state": title_metadata_refresh_state,
    }

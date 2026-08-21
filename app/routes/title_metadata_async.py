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
            if started:
                # A one-title refresh is owned by the surface that launched it.
                # The durable worker remains shared, but global task/notification UI
                # must not light up for this small local action.
                imdb_genre_job["ui_scope"] = "local"
                imdb_genre_job["ui_title_id"] = title_id

        if async_request(request):
            return JSONResponse(
                {
                    "started": started,
                    "title_id": title_id,
                    "detail": detail,
                    "status": status,
                    "ui_scope": "local" if started else "",
                },
                status_code=200 if started else 409,
            )
        return redirect(f"/titles/{title_id}", detail)

    @router.get("/api/titles/{title_id}/metadata-refresh-state")
    def title_metadata_refresh_state(title_id: int):
        # The browser already asks this endpoint frequently while a scoped refresh is
        # running. The shared worker state is authoritative during that phase, so do
        # not open SQLite on every progress tick. Once the task leaves the running
        # state, read the durable queue/title row exactly once so completion details
        # and errors are returned from the database.
        with imdb_genre_lock:
            task = {
                key: imdb_genre_job.get(key)
                for key in (
                    "status", "phase", "scope_label", "title_ids", "records",
                    "matched", "requested", "id_processed", "id_total",
                    "id_found", "id_missing", "id_errors", "error",
                    "ui_scope", "ui_title_id",
                )
                if key in imdb_genre_job
            }
        task.setdefault("status", "idle")
        active_ids = task.get("title_ids")
        task_is_this_title = (
            task["status"] in {"starting", "running"}
            and (active_ids is None or title_id in active_ids)
        )
        if task_is_this_title:
            return {
                "title_id": title_id,
                "task": task,
                "queue": None,
                "metadata_refreshed_at": None,
                "metadata_refresh_error": "",
                "updated_at": None,
            }

        with db.connect() as conn:
            row = conn.execute(
                """SELECT t.id,t.metadata_refreshed_at,t.metadata_refresh_error,t.updated_at,
                          q.status queue_status,q.requested_at,q.started_at,q.completed_at,
                          q.provider,q.error queue_error
                   FROM titles t
                   LEFT JOIN metadata_refresh_queue q ON q.title_id=t.id
                   WHERE t.id=?""",
                (title_id,),
            ).fetchone()
        if not row:
            return JSONResponse({"detail": "Title not found"}, status_code=404)

        queue = None
        if row["queue_status"] is not None:
            queue = {
                "status": row["queue_status"],
                "requested_at": row["requested_at"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "provider": row["provider"],
                "error": row["queue_error"],
            }
        return {
            "title_id": title_id,
            "task": task,
            "queue": queue,
            "metadata_refreshed_at": row["metadata_refreshed_at"],
            "metadata_refresh_error": row["metadata_refresh_error"],
            "updated_at": row["updated_at"],
        }

    return router, {
        "refresh_title_metadata": refresh_title_metadata,
        "title_metadata_refresh_state": title_metadata_refresh_state,
    }

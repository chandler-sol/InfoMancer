from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from ..access import require_librarian
from .context import RouteContext


SCOPES = {"fresh", "stale", "artwork", "credits", "failures"}
MATCHED_PREDICATE = (
    "((t.kind='movie' AND t.tvdb_movie_id IS NOT NULL) "
    "OR (t.kind='tv' AND t.tvdb_id IS NOT NULL))"
)


def build_router(ctx: RouteContext):
    """Serve title-level metadata maintenance details only when the UI asks for them."""
    router = APIRouter()
    db = ctx.live("db")
    queue_metadata_refresh = ctx.live("queue_metadata_refresh")

    def scope_where(scope: str) -> str:
        return {
            "fresh": (
                "t.metadata_refreshed_at IS NOT NULL "
                "AND t.metadata_refreshed_at >= datetime('now','-30 days')"
            ),
            "stale": (
                "t.metadata_refreshed_at IS NULL "
                "OR t.metadata_refreshed_at < datetime('now','-30 days')"
            ),
            "artwork": "COALESCE(t.poster_url,'')=''",
            "credits": (
                "NOT EXISTS (SELECT 1 FROM title_credits tc WHERE tc.title_id=t.id)"
            ),
            "failures": (
                "COALESCE(q.status,'')='failed' "
                "OR COALESCE(t.metadata_refresh_error,'')!=''"
            ),
        }[scope]

    @router.get(
        "/api/metadata/maintenance",
        dependencies=[Depends(require_librarian)],
    )
    def metadata_maintenance_titles(
        request: Request,
        scope: str = Query("stale"),
        limit: int = Query(100, ge=1, le=250),
        offset: int = Query(0, ge=0),
    ):
        selected_scope = scope if scope in SCOPES else "stale"
        where = scope_where(selected_scope)
        maintenance_where = f"{MATCHED_PREDICATE} AND ({where})"

        with db.connect() as conn:
            total = int(conn.execute(
                f"""SELECT COUNT(*) count
                    FROM titles t
                    LEFT JOIN metadata_refresh_queue q ON q.title_id=t.id
                    WHERE {maintenance_where}"""
            ).fetchone()["count"])
            rows = conn.execute(
                f"""SELECT t.id,t.kind,
                           COALESCE(NULLIF(t.metadata_title,''),t.title) display_title,
                           COALESCE(t.metadata_year,t.year) display_year,
                           t.metadata_refreshed_at,t.metadata_provider,
                           t.metadata_refresh_error,
                           CASE WHEN COALESCE(t.poster_url,'')='' THEN 1 ELSE 0 END artwork_missing,
                           CASE WHEN NOT EXISTS (
                               SELECT 1 FROM title_credits tc WHERE tc.title_id=t.id
                           ) THEN 1 ELSE 0 END credits_missing,
                           CASE WHEN t.metadata_refreshed_at IS NULL
                                  OR t.metadata_refreshed_at < datetime('now','-30 days')
                                THEN 1 ELSE 0 END stale,
                           q.status queue_status,q.attempts,q.provider queue_provider,
                           q.error queue_error,q.requested_at,q.started_at,q.completed_at
                    FROM titles t
                    LEFT JOIN metadata_refresh_queue q ON q.title_id=t.id
                    WHERE {maintenance_where}
                    ORDER BY display_title COLLATE NOCASE,t.id
                    LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()

        items = []
        for row in rows:
            issue = ""
            if row["queue_status"] == "failed" or row["queue_error"]:
                issue = str(row["queue_error"] or row["metadata_refresh_error"] or "Refresh failed")
            elif row["metadata_refresh_error"]:
                issue = str(row["metadata_refresh_error"])
            items.append({
                "id": int(row["id"]),
                "title": str(row["display_title"] or "Untitled"),
                "kind": str(row["kind"] or ""),
                "year": row["display_year"],
                "refreshed_at": row["metadata_refreshed_at"],
                "provider": str(row["queue_provider"] or row["metadata_provider"] or ""),
                "queue_status": str(row["queue_status"] or ""),
                "attempts": int(row["attempts"] or 0),
                "artwork_missing": bool(row["artwork_missing"]),
                "credits_missing": bool(row["credits_missing"]),
                "stale": bool(row["stale"]),
                "error": issue,
            })

        return {
            "scope": selected_scope,
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": items,
        }

    @router.post(
        "/api/metadata/maintenance/bulk-refresh",
        dependencies=[Depends(require_librarian)],
    )
    def metadata_maintenance_bulk_refresh(
        request: Request,
        scope: str = Query("stale"),
    ):
        if scope not in {"stale", "failures"}:
            return JSONResponse(
                {"started": False, "detail": "Choose stale titles or failures to refresh."},
                status_code=400,
            )

        where = scope_where(scope)
        with db.connect() as conn:
            rows = conn.execute(
                f"""SELECT t.id
                    FROM titles t
                    LEFT JOIN metadata_refresh_queue q ON q.title_id=t.id
                    WHERE {MATCHED_PREDICATE} AND ({where})
                    ORDER BY t.id
                    LIMIT 1000"""
            ).fetchall()
        title_ids = [int(row["id"]) for row in rows]
        if not title_ids:
            return {
                "started": False,
                "queued": 0,
                "detail": (
                    "No matched stale titles need refreshing."
                    if scope == "stale"
                    else "No matched failed refreshes need retrying."
                ),
            }

        label = (
            f"Refreshing {len(title_ids):,} stale metadata record(s)"
            if scope == "stale"
            else f"Retrying {len(title_ids):,} metadata refresh failure(s)"
        )
        detail = queue_metadata_refresh(title_ids, request.state.user.id, label)
        if detail.startswith("Another metadata refresh"):
            return JSONResponse(
                {"started": False, "queued": 0, "detail": detail},
                status_code=409,
            )
        return {
            "started": True,
            "queued": len(title_ids),
            "detail": detail,
        }

    return router, {
        "metadata_maintenance_titles": metadata_maintenance_titles,
        "metadata_maintenance_bulk_refresh": metadata_maintenance_bulk_refresh,
    }

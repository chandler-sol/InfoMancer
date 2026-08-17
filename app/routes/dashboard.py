from fastapi import APIRouter, Depends

from ..access import require_librarian
from ..saved_views import SavedViewService
from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    HTMLResponse = ctx.get("HTMLResponse")
    Request = ctx.get("Request")
    dashboard_counts = ctx.live("dashboard_counts")
    db = ctx.live("db")
    event_log = ctx.live("event_log")
    saved_views = SavedViewService(db)
    format_bytes = ctx.live("format_bytes")
    mie = ctx.live("mie")
    scan_all_job = ctx.live("scan_all_job")
    scan_all_lock = ctx.live("scan_all_lock")
    scan_jobs = ctx.live("scan_jobs")
    templates = ctx.live("templates")

    def librarian_get(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.get(path, dependencies=dependencies, **kwargs)

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

    @router.get("/api/dashboard-metrics")
    def dashboard_metrics(request: Request) -> dict:
        counts = dashboard_counts(request.state.user.id)
        return {
            "movies": {"value": counts["movies"], "display": f"{counts['movies']:,}"},
            "shows": {"value": counts["shows"], "display": f"{counts['shows']:,}"},
            "episodes": {
                "value": counts["episodes"], "display": f"{counts['episodes']:,}",
            },
            "missing": {
                "value": counts["missing"], "display": f"{counts['missing']:,}",
            },
            "bytes": {
                "value": counts["bytes"], "display": format_bytes(counts["bytes"]),
            },
        }

    @router.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        user_id = request.state.user.id
        counts = dashboard_counts(user_id)
        with db.connect() as conn:
            roots = conn.execute(
                """SELECT r.*, COUNT(DISTINCT t.id) title_count, COUNT(f.id) file_count
                   FROM roots r LEFT JOIN titles t ON t.root_id=r.id
                   LEFT JOIN files f ON f.title_id=t.id GROUP BY r.id ORDER BY r.kind, r.label, r.path"""
            ).fetchall()
            recent = conn.execute(
                """SELECT t.*,COALESCE(uts.favorite,0) favorite,
                          (SELECT MIN(f.id) FROM files f WHERE f.title_id=t.id)
                            first_file_id
                   FROM titles t
                   LEFT JOIN user_title_state uts
                     ON uts.title_id=t.id AND uts.user_id=?
                   ORDER BY t.updated_at DESC LIMIT 8""",
                (user_id,),
            ).fetchall()
            favorites = conn.execute(
                """SELECT t.*,1 favorite,
                          (SELECT MIN(f.id) FROM files f WHERE f.title_id=t.id)
                            first_file_id
                   FROM titles t
                   JOIN user_title_state uts
                     ON uts.title_id=t.id AND uts.user_id=? AND uts.favorite=1
                   ORDER BY uts.updated_at DESC,t.title COLLATE NOCASE LIMIT 8""",
                (user_id,),
            ).fetchall()
        with scan_all_lock:
            all_scan_job = dict(scan_all_job)
        mie_summary = mie.summary()

        activity_unread = (
            event_log.activity(user_id, unread_only=True, limit=250)
            if user_id > 0 else []
        )
        activity_unread_count = len(activity_unread)
        activity_unread_display = (
            "250+" if activity_unread_count >= 250 else f"{activity_unread_count:,}"
        )

        requested_layout = request.query_params.get("layout", "").strip().casefold()
        stored_layout = getattr(request.state.user, "home_layout", "modern")
        if requested_layout == "old":
            home_template = "dashboard_old_test.html"
            dashboard_layout = "old"
        elif requested_layout == "classic" or (
            not requested_layout and stored_layout == "classic"
        ):
            home_template = "dashboard_classic.html"
            dashboard_layout = "classic"
        else:
            # During the 0.8 alpha comparison, the operational dashboard is the
            # modern/default experience. The immediately previous design stays
            # available through ?layout=old for side-by-side usefulness testing.
            home_template = "dashboard_command.html"
            dashboard_layout = "new"

        return templates.TemplateResponse(request, home_template, {
            "counts": counts,
            "roots": roots,
            "recent": recent,
            "favorites": favorites,
            "saved_views": saved_views.list_for_user(user_id, pinned_only=True),
            "jobs": scan_jobs,
            "scan_all_job": all_scan_job,
            "mie_summary": mie_summary,
            "activity_unread": activity_unread,
            "activity_unread_count": activity_unread_count,
            "activity_unread_display": activity_unread_display,
            "dashboard_layout": dashboard_layout,
            "message": request.query_params.get("message", ""),
        })

    return router, {
        "dashboard_metrics": dashboard_metrics,
        "dashboard": dashboard,
    }

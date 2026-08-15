from fastapi import APIRouter, Depends

from ..access import require_librarian
from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    HTMLResponse = ctx.get("HTMLResponse")
    Request = ctx.get("Request")
    all_scan_job = ctx.live("all_scan_job")
    app = ctx.live("app")
    conn = ctx.live("conn")
    counts = ctx.live("counts")
    dashboard_counts = ctx.live("dashboard_counts")
    db = ctx.live("db")
    favorites = ctx.live("favorites")
    format_bytes = ctx.live("format_bytes")
    home_layout = ctx.live("home_layout")
    home_template = ctx.live("home_template")
    mie = ctx.live("mie")
    mie_summary = ctx.live("mie_summary")
    recent = ctx.live("recent")
    request = ctx.live("request")
    requested_layout = ctx.live("requested_layout")
    roots = ctx.live("roots")
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
        counts = dashboard_counts(request.state.user.id)
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
                (request.state.user.id,),
            ).fetchall()
            favorites = conn.execute(
                """SELECT t.*,1 favorite,
                          (SELECT MIN(f.id) FROM files f WHERE f.title_id=t.id)
                            first_file_id
                   FROM titles t
                   JOIN user_title_state uts
                     ON uts.title_id=t.id AND uts.user_id=? AND uts.favorite=1
                   ORDER BY uts.updated_at DESC,t.title COLLATE NOCASE LIMIT 8""",
                (request.state.user.id,),
            ).fetchall()
        with scan_all_lock:
            all_scan_job = dict(scan_all_job)
        mie_summary = mie.summary()
        requested_layout = request.query_params.get("layout", "")
        home_layout = (
            requested_layout if requested_layout in {"modern", "classic"}
            else getattr(request.state.user, "home_layout", "modern")
        )
        home_template = (
            "dashboard_classic.html" if home_layout == "classic" else "dashboard.html"
        )
        return templates.TemplateResponse(request, home_template, {
            "counts": counts, "roots": roots, "recent": recent, "favorites": favorites,
            "jobs": scan_jobs,
            "scan_all_job": all_scan_job,
            "mie_summary": mie_summary,
            "message": request.query_params.get("message", ""),
        })

    return router, {
        "dashboard_metrics": dashboard_metrics,
        "dashboard": dashboard,
    }

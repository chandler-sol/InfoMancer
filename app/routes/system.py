from fastapi import APIRouter, Depends

from ..access import require_librarian
from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    EngagementError = ctx.get("EngagementError")
    Form = ctx.get("Form")
    HTMLResponse = ctx.get("HTMLResponse")
    JSONResponse = ctx.get("JSONResponse")
    Request = ctx.get("Request")
    announcement_page_context = ctx.live("announcement_page_context")
    engagement = ctx.live("engagement")
    event_log = ctx.live("event_log")
    redirect = ctx.live("redirect")
    templates = ctx.live("templates")
    db = ctx.live("db")
    scan_all_job = ctx.live("scan_all_job")
    scan_all_lock = ctx.live("scan_all_lock")
    scan_jobs = ctx.live("scan_jobs")
    scan_lock = ctx.live("scan_lock")
    title_scan_jobs = ctx.live("title_scan_jobs")
    title_scan_lock = ctx.live("title_scan_lock")
    imdb_genre_job = ctx.live("imdb_genre_job")
    imdb_genre_lock = ctx.live("imdb_genre_lock")
    movie_match_job = ctx.live("movie_match_job")
    movie_match_lock = ctx.live("movie_match_lock")
    tv_match_job = ctx.live("tv_match_job")
    tv_match_lock = ctx.live("tv_match_lock")
    media_info_job = ctx.live("media_info_job")
    media_info_lock = ctx.live("media_info_lock")
    media_hash_job = ctx.live("media_hash_job")
    media_hash_lock = ctx.live("media_hash_lock")
    duplicate_verify_job = ctx.live("duplicate_verify_job")
    duplicate_verify_lock = ctx.live("duplicate_verify_lock")
    trash_cleanup_job = ctx.live("trash_cleanup_job")
    trash_cleanup_lock = ctx.live("trash_cleanup_lock")

    @router.post("/engagement/announcements/{announcement_id}/seen")
    def mark_announcement_seen(request: Request, announcement_id: int):
        user = request.state.user
        try:
            engagement.mark_seen(announcement_id, user.id, user.role)
        except EngagementError:
            return JSONResponse(
                {"detail": "That announcement is no longer available. Refresh and try again."},
                status_code=404,
            )
        return JSONResponse({"saved": True})

    @router.get("/announcements", response_class=HTMLResponse)
    def announcements_page(request: Request):
        user = request.state.user
        rows = engagement.list_for_user(user.id, user.role)
        for row in rows:
            if row["due_now"]:
                engagement.mark_seen(row["id"], user.id, user.role)
        return templates.TemplateResponse(
            request, "announcements.html", announcement_page_context(request)
        )

    @router.get("/help", response_class=HTMLResponse)
    def help_page(request: Request):
        return templates.TemplateResponse(request, "help.html", {
            "message": request.query_params.get("message", ""),
        })

    @router.get("/about", response_class=HTMLResponse)
    def about_page(request: Request):
        return templates.TemplateResponse(request, "about.html", {
            "message": request.query_params.get("message", ""),
        })

    @router.get("/activity", response_class=HTMLResponse)
    def activity_page(request: Request, unread: str = ""):
        items = event_log.activity(
            request.state.user.id, unread_only=unread == "1", limit=150,
        )
        return templates.TemplateResponse(request, "activity.html", {
            "items": items, "unread_only": unread == "1",
            "message": request.query_params.get("message", ""),
        })

    @router.post("/activity/read")
    def mark_activity_read(
        request: Request, event_ids: list[int] = Form(default=[]), all_events: str = Form(""),
    ):
        # Read state is account-local and EventLog intersects requested IDs with
        # notifications visible to this user, so members can safely manage their own inbox.
        changed = event_log.mark_read(
            request.state.user.id, None if all_events == "1" else event_ids,
        )
        return redirect("/activity", f"Marked {changed:,} notification{'s' if changed != 1 else ''} as read.")

    @router.get("/api/task-failures", dependencies=[Depends(require_librarian)])
    def task_failures() -> dict:
        """Return current failed background jobs without exposing scheduled backlog work."""
        failures: list[dict[str, str]] = []

        def add_failure(task_id: str, label: str, job: dict, href: str = "/activity") -> None:
            if job.get("status") not in {"error", "failed"}:
                return
            detail = str(job.get("error") or job.get("detail") or "The task stopped unexpectedly.")
            failures.append({"id": task_id, "label": label, "detail": detail[:1200], "href": href})

        with scan_all_lock:
            all_scan = dict(scan_all_job)
        with scan_lock:
            failed_scans = {
                int(root_id): dict(job) for root_id, job in scan_jobs.items()
                if job.get("status") in {"error", "failed"}
            }
        if all_scan.get("status") in {"error", "failed"} or (
            all_scan.get("status") == "complete" and int(all_scan.get("errors") or 0) > 0
        ):
            error_count = int(all_scan.get("errors") or 0)
            all_scan.setdefault(
                "error",
                f"{error_count:,} source{'s' if error_count != 1 else ''} could not be scanned. Open Activity for the individual source errors.",
            )
            all_scan["status"] = "error"
            add_failure("scan-all", "Scanning all sources", all_scan)
        elif failed_scans:
            placeholders = ",".join("?" for _ in failed_scans)
            with db.connect() as conn:
                names = {
                    int(row["id"]): (row["label"] or row["path"])
                    for row in conn.execute(
                        f"SELECT id,label,path FROM roots WHERE id IN ({placeholders})",
                        tuple(failed_scans),
                    )
                }
            for root_id, job in failed_scans.items():
                add_failure(
                    f"scan-{root_id}", f"Scanning {names.get(root_id, f'source {root_id}')}", job,
                )

        with title_scan_lock:
            title_failures = {
                int(title_id): dict(job) for title_id, job in title_scan_jobs.items()
                if job.get("status") in {"error", "failed"}
            }
        for title_id, job in title_failures.items():
            add_failure(
                f"title-scan-{title_id}",
                f"Rescanning {job.get('label') or 'TV series'}",
                job,
            )

        jobs = (
            ("imdb-metadata", "Updating IMDb metadata", imdb_genre_lock, imdb_genre_job, "/activity"),
            ("movie-match-analysis", "Analyzing movie matches", movie_match_lock, movie_match_job, "/activity"),
            ("tv-match-analysis", "Analyzing TV series matches", tv_match_lock, tv_match_job, "/activity"),
            ("media-info", "Inspecting media files", media_info_lock, media_info_job, "/media-info/failures"),
            ("media-fingerprints", "Fingerprinting media files", media_hash_lock, media_hash_job, "/activity"),
            ("duplicate-verification", "Verifying duplicate files", duplicate_verify_lock, duplicate_verify_job, "/activity"),
            ("duplicate-trash-cleanup", "Cleaning managed trash", trash_cleanup_lock, trash_cleanup_job, "/activity"),
        )
        for task_id, label, lock, source, href in jobs:
            with lock:
                job = dict(source)
            add_failure(task_id, label, job, href)
        return {"failures": failures}

    @router.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return router, {
        "mark_announcement_seen": mark_announcement_seen,
        "announcements_page": announcements_page,
        "help_page": help_page,
        "about_page": about_page,
        "activity_page": activity_page,
        "mark_activity_read": mark_activity_read,
        "task_failures": task_failures,
        "health": health,
    }

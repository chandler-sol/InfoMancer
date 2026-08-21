from fastapi import APIRouter, Depends

from ..access import require_librarian
from ..file_protection import FileProtectionService, MediaWriteBlocked
from ..operation_history import OperationHistoryError, OperationHistoryService
from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    Form = ctx.get("Form")
    HTMLResponse = ctx.get("HTMLResponse")
    HTTPException = ctx.get("HTTPException")
    Path = ctx.get("Path")
    Request = ctx.get("Request")
    _other_background_work_running = ctx.live("_other_background_work_running")
    app_settings = ctx.live("app_settings")
    db = ctx.live("db")
    duplicate_trash = ctx.live("duplicate_trash")
    operation_history = OperationHistoryService(db)
    file_protection = FileProtectionService(app_settings)
    duplicate_verify_job = ctx.live("duplicate_verify_job")
    duplicate_verify_lock = ctx.live("duplicate_verify_lock")
    imdb_genre_job = ctx.live("imdb_genre_job")
    imdb_genre_lock = ctx.live("imdb_genre_lock")
    media_hash_job = ctx.live("media_hash_job")
    media_hash_lock = ctx.live("media_hash_lock")
    media_hash_pause = ctx.live("media_hash_pause")
    media_hashes = ctx.live("media_hashes")
    media_info_job = ctx.live("media_info_job")
    media_info_lock = ctx.live("media_info_lock")
    movie_match_job = ctx.live("movie_match_job")
    movie_match_lock = ctx.live("movie_match_lock")
    queue_metadata_refresh = ctx.live("queue_metadata_refresh")
    record_event = ctx.live("record_event")
    redirect = ctx.live("redirect")
    run_imdb_genre_sync = ctx.live("run_imdb_genre_sync")
    run_media_inspection = ctx.live("run_media_inspection")
    run_scan_all = ctx.live("run_scan_all")
    scan_all_job = ctx.live("scan_all_job")
    scan_all_lock = ctx.live("scan_all_lock")
    scan_jobs = ctx.live("scan_jobs")
    scan_lock = ctx.live("scan_lock")
    start_scoped_imdb_sync = ctx.live("start_scoped_imdb_sync")
    templates = ctx.live("templates")
    threading = ctx.live("threading")
    title_scan_jobs = ctx.live("title_scan_jobs")
    title_scan_lock = ctx.live("title_scan_lock")
    trash_cleanup_job = ctx.live("trash_cleanup_job")
    trash_cleanup_lock = ctx.live("trash_cleanup_lock")
    tv_match_job = ctx.live("tv_match_job")
    tv_match_lock = ctx.live("tv_match_lock")
    urlencode = ctx.live("urlencode")

    def librarian_get(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.get(path, dependencies=dependencies, **kwargs)

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

    @librarian_get("/operations", response_class=HTMLResponse)
    def operation_history_page(
        request: Request, status: str = "all", kind: str = "all",
    ):
        return templates.TemplateResponse(request, "operations.html", {
            "operations": operation_history.list(status=status, kind=kind),
            "counts": operation_history.counts(),
            "status": status if status in operation_history.ALLOWED_STATUS else "all",
            "kind": kind if kind in operation_history.ALLOWED_KIND else "all",
            "message": request.query_params.get("message", ""),
        })

    @librarian_post("/operations/{operation_id}/undo")
    def undo_operation(request: Request, operation_id: int):
        try:
            file_protection.require_media_write("undo filesystem operations")
            message = operation_history.undo(
                operation_id, request.state.user.id, duplicate_trash=duplicate_trash,
            )
        except MediaWriteBlocked as exc:
            return redirect("/operations", str(exc))
        except OperationHistoryError as exc:
            record_event(
                "filesystem", "Operation undo was refused safely.", level="warning",
                detail=str(exc), context={"operation_id": operation_id},
                user_id=request.state.user.id,
            )
            return redirect("/operations", str(exc))
        record_event(
            "filesystem", "Operation undone safely.", detail=message,
            context={"operation_id": operation_id}, user_id=request.state.user.id,
        )
        return redirect("/operations", message)

    @librarian_get("/api/scans/{root_id}")
    def scan_status(root_id: int) -> dict:
        with scan_lock:
            return dict(scan_jobs.get(root_id, {"status": "idle"}))

    @librarian_get("/api/scan-all")
    def scan_all_status() -> dict:
        with scan_all_lock:
            return dict(scan_all_job)

    @librarian_post("/scan-all")
    def start_scan_all():
        with scan_all_lock:
            if scan_all_job.get("status") in {"starting", "running"}:
                return redirect("/sources", "All sources are already being scanned")
        with scan_lock:
            if any(job.get("status") in {"starting", "running"} for job in scan_jobs.values()):
                return redirect("/sources", "Wait for the current source scan to finish")
        with db.connect() as conn:
            root_rows = conn.execute(
                "SELECT id, label, path FROM roots WHERE enabled=1 ORDER BY kind, label, path"
            ).fetchall()
        roots = [
            (row["id"], row["label"] or Path(row["path"]).name or row["path"])
            for row in root_rows
        ]
        if not roots:
            return redirect("/sources", "No enabled sources are configured")
        with scan_all_lock:
            scan_all_job.clear()
            scan_all_job.update({"status": "starting", "total": len(roots), "completed": 0})
        threading.Thread(target=run_scan_all, args=(roots,), daemon=True).start()
        return redirect("/sources", f"Scanning all {len(roots)} sources")

    @router.get("/api/imdb-genres")
    def imdb_genre_status() -> dict:
        with imdb_genre_lock:
            return dict(imdb_genre_job)

    @router.get("/api/tasks")
    def active_tasks() -> dict:
        tasks = []
        scheduled = []
        with scan_all_lock:
            all_scan = dict(scan_all_job)
        with scan_lock:
            scans = {
                root_id: dict(job) for root_id, job in scan_jobs.items()
                if job.get("status") in {"starting", "running"}
            }
        if all_scan.get("status") in {"starting", "running"}:
            current = all_scan.get("current_label") or "Preparing sources"
            tasks.append({
                "id": "scan-all", "label": "Scanning all sources",
                "detail": (
                    f"{all_scan.get('completed', 0):,} of {all_scan.get('total', 0):,} "
                    f"complete · {current}"
                ),
            })
            scans = {}
        if scans:
            placeholders = ",".join("?" for _ in scans)
            with db.connect() as conn:
                roots = {
                    row["id"]: row["label"] or Path(row["path"]).name or row["path"]
                    for row in conn.execute(
                        f"SELECT id, label, path FROM roots WHERE id IN ({placeholders})",
                        tuple(scans),
                    )
                }
            for root_id, job in scans.items():
                tasks.append({
                    "id": f"scan-{root_id}",
                    "label": f"Scanning {roots.get(root_id, f'library {root_id}')}",
                    "detail": (
                        f"{job.get('files', 0):,} video files · "
                        f"{job.get('titles', 0):,} titles"
                    ),
                })
        with title_scan_lock:
            title_scans = {
                title_id: dict(job) for title_id, job in title_scan_jobs.items()
                if job.get("status") in {"starting", "running"}
            }
        for title_id, job in title_scans.items():
            tasks.append({
                "id": f"title-scan-{title_id}",
                "label": f"Rescanning {job.get('label') or 'series'}",
                "detail": f"{job.get('files', 0):,} video files found",
            })
        with imdb_genre_lock:
            genre_job = dict(imdb_genre_job)
        if genre_job.get("status") in {"starting", "running"}:
            if genre_job.get("phase") == "ids":
                label = "Backfilling IMDb IDs"
                detail = (
                    f"{genre_job.get('id_processed', 0):,} of "
                    f"{genre_job.get('id_total', 0):,} older matches checked"
                )
            elif genre_job.get("phase") == "basics":
                label = "Updating IMDb genres and types"
                detail = (
                    f"{genre_job.get('records', 0):,} IMDb records checked · "
                    f"{genre_job.get('matched', 0):,} titles found"
                )
            elif genre_job.get("phase") == "ratings":
                label = "Updating IMDb ratings"
                detail = (
                    f"{genre_job.get('records', 0):,} rating records checked · "
                    f"{genre_job.get('matched', 0):,} titles found"
                )
            elif genre_job.get("phase") == "episodes":
                label = "Linking IMDb episode records"
                detail = (
                    f"{genre_job.get('records', 0):,} episode records checked · "
                    f"{genre_job.get('matched', 0):,} library episodes found"
                )
            elif genre_job.get("phase") == "crew":
                label = "Updating title and episode credits"
                detail = (
                    f"{genre_job.get('records', 0):,} crew records checked · "
                    f"{genre_job.get('matched', 0):,} titles found"
                )
            elif genre_job.get("phase") == "principals":
                label = "Updating top-billed cast"
                detail = (
                    f"{genre_job.get('records', 0):,} billing records checked · "
                    f"{genre_job.get('matched', 0):,} titles found"
                )
            elif genre_job.get("phase") == "names":
                label = "Resolving IMDb people"
                detail = (
                    f"{genre_job.get('records', 0):,} name records checked · "
                    f"{genre_job.get('matched', 0):,} people found"
                )
            else:
                label = "Preparing IMDb metadata update"
                detail = "Starting background task"
            if genre_job.get("scope_label"):
                label = f"{label}: {genre_job['scope_label']}"
            tasks.append({"id": "imdb-metadata", "label": label, "detail": detail})
        with movie_match_lock:
            match_job = dict(movie_match_job)
        if match_job.get("status") in {"starting", "running"}:
            match_mode = match_job.get("mode")
            match_label = (
                "Analyzing selected movie matches"
                if match_mode == "selected"
                else "Analyzing movie matches"
            )
            tasks.append({
                "id": "movie-match-analysis",
                "label": match_label,
                "detail": (
                    f"{match_job.get('processed', 0):,} of {match_job.get('total', 0):,} checked"
                    + (f" · {match_job.get('matched', 0):,} suggestions found" if match_job.get("processed") else "")
                ),
            })
        with tv_match_lock:
            show_match_job = dict(tv_match_job)
        if show_match_job.get("status") in {"starting", "running"}:
            tasks.append({
                "id": "tv-match-analysis",
                "label": "Analyzing TV series matches",
                "detail": (
                    f"{show_match_job.get('processed', 0):,} of {show_match_job.get('total', 0):,} checked"
                    + (f" · {show_match_job.get('matched', 0):,} suggestions found" if show_match_job.get("processed") else "")
                ),
            })
        with media_info_lock:
            media_job = dict(media_info_job)
        if media_job.get("status") in {"starting", "running"}:
            tasks.append({
                "id": "media-info",
                "label": "Inspecting media files",
                "detail": (
                    f"{media_job.get('processed', 0):,} of "
                    f"{media_job.get('total', 0):,} checked"
                    + (
                        f" · {media_job.get('current', '')}"
                        if media_job.get("current") else ""
                    )
                ),
            })
        with media_hash_lock:
            hash_job = dict(media_hash_job)
        if hash_job.get("status") in {"starting", "running"}:
            hash_counts = media_hashes.counts()
            paused = media_hash_pause.is_set() or (
                app_settings.get("hash_pause_for_activity") == "1"
                and _other_background_work_running()
            )
            tasks.append({
                "id": "media-fingerprints",
                "label": "File fingerprinting paused" if paused else "Fingerprinting media files",
                "detail": (
                    f"{hash_job.get('processed', 0):,} of {hash_job.get('total', 0):,} checked"
                    f" · {hash_counts.get('queued', 0):,} queued"
                ),
            })
        elif app_settings.get("hash_mode") in {"automatic", "scheduled"}:
            hash_counts = media_hashes.counts()
            if hash_counts.get("queued", 0):
                frequency = app_settings.get("hash_schedule_frequency").capitalize()
                scheduled.append({
                    "id": "media-fingerprints-queued",
                    "label": "File fingerprints scheduled",
                    "detail": (
                        f"{hash_counts['queued']:,} files queued · {frequency} at "
                        f"{app_settings.get('hash_schedule_time')}"
                    ),
                })
        with duplicate_verify_lock:
            duplicate_job = dict(duplicate_verify_job)
        if duplicate_job.get("status") in {"starting", "running"}:
            tasks.append({
                "id": "duplicate-verification",
                "label": "Verifying duplicate files",
                "detail": duplicate_job.get("detail") or "Reading both files byte for byte",
            })
        with trash_cleanup_lock:
            cleanup_job = dict(trash_cleanup_job)
        if cleanup_job.get("status") in {"starting", "running"}:
            tasks.append({
                "id": "duplicate-trash-cleanup",
                "label": "Cleaning managed trash",
                "detail": cleanup_job.get("detail") or "Checking retention dates",
            })
        return {"tasks": tasks, "scheduled": scheduled}

    @librarian_get("/api/movie-match-analysis")
    def movie_match_analysis_status() -> dict:
        with movie_match_lock:
            return dict(movie_match_job)

    @router.get("/api/duplicate-verification")
    def duplicate_verification_status() -> dict:
        """Report duplicate hash-verification completion to the review page."""
        with duplicate_verify_lock:
            return dict(duplicate_verify_job)

    @librarian_get("/api/tv-match-analysis")
    def tv_match_analysis_status() -> dict:
        with tv_match_lock:
            return dict(tv_match_job)

    @router.get("/api/media-info")
    def media_info_status() -> dict:
        with media_info_lock:
            return dict(media_info_job)

    @librarian_get("/media-info/failures", response_class=HTMLResponse)
    def media_info_failures(request: Request):
        with db.connect() as conn:
            rows = conn.execute(
                """SELECT f.id, f.title_id, f.filename, f.path, f.media_info_error,
                          t.kind, COALESCE(t.metadata_title, t.title) display_title,
                          COALESCE(r.label, r.path) source_name
                   FROM files f
                   JOIN titles t ON t.id=f.title_id
                   JOIN roots r ON r.id=t.root_id
                   WHERE f.media_info_error IS NOT NULL
                     AND f.media_info_error!=''
                   ORDER BY display_title COLLATE NOCASE, f.filename COLLATE NOCASE"""
            ).fetchall()
        failures = []
        for row in rows:
            failure = dict(row)
            explanation, separator, _technical = failure["media_info_error"].partition(
                " Technical detail:"
            )
            failure["explanation"] = explanation.strip() or (
                "InfoMancer could not read technical information from this file."
            )
            failure["logs_url"] = "/logs?" + urlencode({
                "category": "media", "search": failure["filename"],
            })
            failures.append(failure)
        return templates.TemplateResponse(request, "media_info_failures.html", {
            "failures": failures,
            "message": request.query_params.get("message", ""),
        })

    @librarian_post("/media-info/scan")
    def start_media_info_scan(request: Request, scope: str = Form("missing")):
        with media_info_lock:
            if media_info_job.get("status") in {"starting", "running"}:
                return redirect(
                    "/settings/system",
                    "Media inspection is already running. Progress remains visible in the task widget.",
                )
            media_info_job.clear()
            media_info_job.update({"status": "starting", "processed": 0, "total": 0})
        file_ids = None
        if scope == "all":
            with db.connect() as conn:
                file_ids = [
                    row["id"] for row in conn.execute("SELECT id FROM files ORDER BY id")
                ]
        threading.Thread(target=run_media_inspection, args=(file_ids,), daemon=True).start()
        record_event(
            "media", "Media inspection was requested from System Settings.",
            user_id=request.state.user.id, context={"scope": scope},
        )
        return redirect(
            "/settings/system",
            "Media inspection started. You can continue using InfoMancer while it runs.",
        )

    @librarian_post("/imdb-genres/sync")
    def start_imdb_genre_sync(return_to: str = Form("")):
        destination = "/settings/metadata" if return_to == "/settings/metadata" else "/sources"
        with imdb_genre_lock:
            if imdb_genre_job.get("status") in {"starting", "running"}:
                return redirect(destination, "IMDb metadata update is already running.")
            imdb_genre_job.clear()
            imdb_genre_job.update({"status": "starting"})
        threading.Thread(target=run_imdb_genre_sync, daemon=True).start()
        return redirect(destination, "IMDb metadata update started.")

    @librarian_post("/metadata/queue")
    def enqueue_metadata_refresh(
        request: Request, selected: list[int] = Form(default=[]), scope: str = Form("selected"),
        return_to: str = Form("/settings/metadata"),
    ):
        if scope == "stale":
            with db.connect() as conn:
                selected = [row["id"] for row in conn.execute(
                    """SELECT id FROM titles WHERE metadata_refreshed_at IS NULL
                       OR metadata_refreshed_at < datetime('now','-30 days') ORDER BY id LIMIT 1000"""
                )]
        message = queue_metadata_refresh(selected, request.state.user.id, "Incremental metadata refresh")
        destination = return_to if return_to.startswith("/") and not return_to.startswith("//") else "/settings/metadata"
        return redirect(destination, message)

    @librarian_post("/metadata/retry-failed")
    def retry_failed_metadata(request: Request):
        with db.connect() as conn:
            ids = [row["title_id"] for row in conn.execute(
                "SELECT title_id FROM metadata_refresh_queue WHERE status='failed' ORDER BY requested_at LIMIT 1000"
            )]
        message = queue_metadata_refresh(ids, request.state.user.id, "Retry failed metadata")
        return redirect("/settings/metadata", message)

    @librarian_post("/titles/{title_id}/imdb-refresh")
    def refresh_title_imdb_metadata(title_id: int):
        with db.connect() as conn:
            title = conn.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
        if not title:
            raise HTTPException(404, "Title not found")
        if not (title["imdb_id"] or title["tvdb_id"] or title["tvdb_movie_id"]):
            return redirect(f"/titles/{title_id}", "Match this title before pulling IMDb metadata")
        label = title["metadata_title"] or title["title"]
        error = start_scoped_imdb_sync([title_id], None, label)
        return redirect(
            f"/titles/{title_id}",
            error or f"IMDb metadata refresh started for {label}",
        )

    @librarian_post("/files/{file_id}/imdb-refresh")
    def refresh_episode_imdb_metadata(file_id: int):
        with db.connect() as conn:
            file_row = conn.execute(
                """SELECT f.*, t.id title_id, t.kind, t.title, t.metadata_title,
                          t.imdb_id, t.tvdb_id
                   FROM files f JOIN titles t ON t.id=f.title_id WHERE f.id=?""",
                (file_id,),
            ).fetchone()
            if not file_row:
                raise HTTPException(404, "Media file not found")
            if file_row["kind"] != "tv" or file_row["season"] is None:
                return redirect(f"/titles/{file_row['title_id']}", "IMDb episode metadata is available for parsed TV episodes")
            episode_ids = [
                row["id"] for row in conn.execute(
                    """SELECT id FROM expected_episodes
                       WHERE title_id=? AND season=? AND episode BETWEEN ? AND ?
                       ORDER BY episode""",
                    (
                        file_row["title_id"], file_row["season"],
                        file_row["episode_start"],
                        file_row["episode_end"] or file_row["episode_start"],
                    ),
                )
            ]
        if not episode_ids:
            return redirect(f"/titles/{file_row['title_id']}", "Match this series and load its episode list first")
        label = (
            f"{file_row['metadata_title'] or file_row['title']} "
            f"S{file_row['season']:02d}E{file_row['episode_start']:02d}"
        )
        error = start_scoped_imdb_sync(
            [file_row["title_id"]], episode_ids, label,
        )
        return redirect(
            f"/titles/{file_row['title_id']}",
            error or f"IMDb metadata refresh started for {label}",
        )

    return router, {
        "operation_history_page": operation_history_page,
        "undo_operation": undo_operation,
        "scan_status": scan_status,
        "scan_all_status": scan_all_status,
        "start_scan_all": start_scan_all,
        "imdb_genre_status": imdb_genre_status,
        "active_tasks": active_tasks,
        "movie_match_analysis_status": movie_match_analysis_status,
        "duplicate_verification_status": duplicate_verification_status,
        "tv_match_analysis_status": tv_match_analysis_status,
        "media_info_status": media_info_status,
        "media_info_failures": media_info_failures,
        "start_media_info_scan": start_media_info_scan,
        "start_imdb_genre_sync": start_imdb_genre_sync,
        "enqueue_metadata_refresh": enqueue_metadata_refresh,
        "retry_failed_metadata": retry_failed_metadata,
        "refresh_title_imdb_metadata": refresh_title_imdb_metadata,
        "refresh_episode_imdb_metadata": refresh_episode_imdb_metadata,
    }

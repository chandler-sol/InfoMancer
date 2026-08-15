from fastapi import APIRouter, Depends

from ..access import require_librarian
from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    APP_VERSION = ctx.get("APP_VERSION")
    AppSettingError = ctx.get("AppSettingError")
    File = ctx.get("File")
    FileResponse = ctx.get("FileResponse")
    Form = ctx.get("Form")
    HTMLResponse = ctx.get("HTMLResponse")
    HTTPException = ctx.get("HTTPException")
    MaintenanceError = ctx.get("MaintenanceError")
    Path = ctx.get("Path")
    RedirectResponse = ctx.get("RedirectResponse")
    Request = ctx.get("Request")
    Response = ctx.get("Response")
    SETTINGS_SECTIONS = ctx.get("SETTINGS_SECTIONS")
    SourceBrowserError = ctx.get("SourceBrowserError")
    TVDBError = ctx.get("TVDBError")
    UploadFile = ctx.get("UploadFile")
    app_settings = ctx.live("app_settings")
    auth_error_response = ctx.live("auth_error_response")
    base64 = ctx.live("base64")
    check_source_health = ctx.live("check_source_health")
    create_database_backup = ctx.live("create_database_backup")
    csv = ctx.live("csv")
    csv_safe_row = ctx.live("csv_safe_row")
    datetime = ctx.live("datetime")
    db = ctx.live("db")
    engagement = ctx.live("engagement")
    event_log = ctx.live("event_log")
    install_database_backup = ctx.live("install_database_backup")
    io = ctx.live("io")
    json = ctx.live("json")
    list_database_backups = ctx.live("list_database_backups")
    list_folders = ctx.live("list_folders")
    media_hash_cancel = ctx.live("media_hash_cancel")
    media_hash_job = ctx.live("media_hash_job")
    media_hash_lock = ctx.live("media_hash_lock")
    media_hash_pause = ctx.live("media_hash_pause")
    media_hashes = ctx.live("media_hashes")
    mie = ctx.live("mie")
    os = ctx.live("os")
    preview_folder = ctx.live("preview_folder")
    re = ctx.live("re")
    read_update_status = ctx.live("read_update_status")
    record_event = ctx.live("record_event")
    redirect = ctx.live("redirect")
    release_version_key = ctx.live("release_version_key")
    render_settings = ctx.live("render_settings")
    resolve_backup = ctx.live("resolve_backup")
    restart_after_restore = ctx.live("restart_after_restore")
    run_scan = ctx.live("run_scan")
    run_title_scan = ctx.live("run_title_scan")
    scan_all_job = ctx.live("scan_all_job")
    scan_all_lock = ctx.live("scan_all_lock")
    scan_jobs = ctx.live("scan_jobs")
    scan_lock = ctx.live("scan_lock")
    settings = ctx.live("settings")
    sqlite3 = ctx.live("sqlite3")
    start_media_hashing = ctx.live("start_media_hashing")
    tempfile = ctx.live("tempfile")
    templates = ctx.live("templates")
    threading = ctx.live("threading")
    time = ctx.live("time")
    timezone = ctx.live("timezone")
    title_scan_jobs = ctx.live("title_scan_jobs")
    title_scan_lock = ctx.live("title_scan_lock")
    tvdb = ctx.live("tvdb")
    urllib = ctx.live("urllib")
    validate_database_backup = ctx.live("validate_database_backup")
    write_update_request = ctx.live("write_update_request")
    write_update_status = ctx.live("write_update_status")
    zipfile = ctx.live("zipfile")

    def librarian_get(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.get(path, dependencies=dependencies, **kwargs)

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

    @librarian_get("/settings/export")
    def export_application_settings(request: Request):
        payload = {
            "format": "infomancer-settings",
            "format_version": 1,
            "app_version": APP_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "settings": app_settings.values(),
            "excluded": [
                "passwords", "sessions", "provider credentials",
                "encryption keys", "media sources", "accounts",
            ],
        }
        record_event(
            "settings", "Portable application settings exported.",
            user_id=request.state.user.id,
        )
        return Response(
            json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"),
            media_type="application/json",
            headers={
                "Content-Disposition": (
                    "attachment; filename="
                    f'"infomancer-settings-{datetime.now().strftime("%Y%m%d-%H%M%S")}.json"'
                )
            },
        )

    @librarian_post("/settings/import/preview", response_class=HTMLResponse)
    async def preview_application_settings(request: Request, settings_file: UploadFile = File(...)):
        try:
            content = await settings_file.read(262145)
            if len(content) > 262144:
                raise AppSettingError(
                    "The settings file is larger than 256 KB. Choose the JSON settings "
                    "file exported by InfoMancer."
                )
            payload = json.loads(content.decode("utf-8-sig"))
            if not isinstance(payload, dict) or payload.get("format") != "infomancer-settings":
                raise AppSettingError(
                    "That is not an InfoMancer settings export. Choose a JSON file "
                    "downloaded from Export Settings."
                )
            imported = app_settings.validate_import(payload.get("settings"))
            current = app_settings.values()
            changes = [
                {"key": key, "old": current[key], "new": value}
                for key, value in imported.items() if current[key] != value
            ]
            encoded = base64.urlsafe_b64encode(json.dumps(imported).encode()).decode()
        except (UnicodeDecodeError, json.JSONDecodeError):
            return render_settings(
                request, "system",
                "The selected settings file is not valid JSON. No settings were changed.",
                status_code=400,
            )
        except AppSettingError as exc:
            return render_settings(request, "system", str(exc), status_code=400)
        return templates.TemplateResponse(request, "settings_import_preview.html", {
            "changes": changes, "encoded_settings": encoded,
            "message": "", "error": "",
        })

    @librarian_post("/settings/import")
    def apply_application_settings(
        request: Request, encoded_settings: str = Form(...), confirm: str = Form(""),
    ):
        if confirm != "IMPORT":
            return redirect(
                "/settings/system",
                "Settings import cancelled; no settings were changed.",
            )
        try:
            raw = base64.urlsafe_b64decode(encoded_settings.encode())
            imported = app_settings.validate_import(json.loads(raw))
            create_database_backup(db.path, "before-settings-import")
            changed = app_settings.update(imported, request.state.user.id)
        except (ValueError, json.JSONDecodeError, AppSettingError, MaintenanceError) as exc:
            record_event(
                "settings", "Application settings import failed.",
                level="error", detail=str(exc), user_id=request.state.user.id,
            )
            return redirect(
                "/settings/system",
                f"Settings were not imported. {exc}",
            )
        record_event(
            "settings", "Portable application settings imported.",
            context={"changed": changed}, user_id=request.state.user.id,
        )
        return redirect(
            "/settings/system",
            f"Settings imported successfully. {changed} setting"
            f"{'s' if changed != 1 else ''} changed.",
        )

    @librarian_post("/maintenance/backups")
    def create_backup_from_ui(request: Request):
        try:
            path = create_database_backup(db.path)
        except MaintenanceError as exc:
            record_event(
                "backup", "Database backup could not be created.",
                level="error", detail=str(exc), user_id=request.state.user.id,
            )
            return redirect("/settings/system", str(exc))
        record_event(
            "backup", "Database backup created from App Settings.",
            context={"name": path.name}, user_id=request.state.user.id,
        )
        return redirect(
            "/settings/system",
            f"Database backup {path.name} was created successfully.",
        )

    @librarian_post("/maintenance/backups/verify")
    def verify_all_backups(request: Request):
        backups = list_database_backups(db.path)
        failures: list[str] = []
        for item in backups:
            try:
                validate_database_backup(resolve_backup(db.path, item["name"]))
            except MaintenanceError:
                failures.append(item["name"])
        if failures:
            message = (
                f"{len(failures)} backup{'s' if len(failures) != 1 else ''} could not be read. "
                "The live catalog was not changed. Create a fresh backup before restoring anything."
            )
            record_event("backup", message, level="error", context={"failed": failures}, user_id=request.state.user.id)
            return redirect("/settings/system", message)
        message = f"Verified {len(backups)} database backup{'s' if len(backups) != 1 else ''}. Each is readable and contains the required InfoMancer tables."
        record_event("backup", message, context={"verified": len(backups)}, user_id=request.state.user.id)
        return redirect("/settings/system", message)

    @librarian_get("/maintenance/diagnostics")
    def download_diagnostics(request: Request):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("summary.json", json.dumps({
                "app_version": APP_VERSION,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "database_size": db.path.stat().st_size if db.path.exists() else 0,
                "library_health": mie.summary(),
                "backup_count": len(list_database_backups(db.path)),
            }, indent=2, default=str))
            events = []
            for row in event_log.query(limit=1000):
                item = dict(row)
                item.pop("user_id", None)
                events.append(item)
            archive.writestr("recent-events.json", json.dumps(events, indent=2, default=str))
            archive.writestr("README.txt", (
                "InfoMancer diagnostic bundle\n\nThis bundle contains application status and recent "
                "events. It excludes passwords, sessions, API credentials, provider secrets, and the media database.\n"
            ))
        record_event("diagnostics", "A diagnostic bundle was exported.", user_id=request.state.user.id)
        return Response(output.getvalue(), media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="infomancer-diagnostics-{datetime.now().strftime("%Y%m%d-%H%M%S")}.zip"'
        })

    @librarian_get("/maintenance/backups/{name}")
    def download_database_backup(name: str):
        try:
            path = resolve_backup(db.path, name)
        except MaintenanceError as exc:
            raise HTTPException(404, str(exc)) from exc
        return FileResponse(
            path, media_type="application/vnd.sqlite3", filename=path.name,
        )

    @librarian_post("/maintenance/restore/server", response_class=HTMLResponse)
    def restore_server_database(
        request: Request, backup_name: str = Form(...), confirm: str = Form(""),
    ):
        if confirm != "RESTORE":
            return redirect(
                "/settings/system",
                "Database restore cancelled; the live database was not changed.",
            )
        try:
            candidate = resolve_backup(db.path, backup_name)
            safety = install_database_backup(db.path, candidate, settings.media_browse_roots)
        except MaintenanceError as exc:
            record_event(
                "restore", "Database restore was rejected.",
                level="error", detail=str(exc), user_id=request.state.user.id,
            )
            return redirect("/settings/system", str(exc))
        threading.Thread(target=restart_after_restore, daemon=True).start()
        return templates.TemplateResponse(request, "restore_pending.html", {
            "safety_backup": safety.name, "message": "",
        })

    @librarian_post("/maintenance/restore/upload", response_class=HTMLResponse)
    async def restore_uploaded_database(
        request: Request, database_file: UploadFile = File(...),
        confirm: str = Form(""),
    ):
        if confirm != "RESTORE":
            return redirect(
                "/settings/system",
                "Database restore cancelled; the live database was not changed.",
            )
        candidate_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=db.path.parent, prefix="restore-upload-", suffix=".db", delete=False
            ) as candidate:
                candidate_path = Path(candidate.name)
                total = 0
                while chunk := await database_file.read(1024 * 1024):
                    total += len(chunk)
                    if total > 2 * 1024 * 1024 * 1024:
                        raise MaintenanceError(
                            "The uploaded database is larger than the 2 GB restore limit."
                        )
                    candidate.write(chunk)
            safety = install_database_backup(db.path, candidate_path, settings.media_browse_roots)
        except (MaintenanceError, OSError) as exc:
            record_event(
                "restore", "Uploaded database restore was rejected.",
                level="error", detail=str(exc), user_id=request.state.user.id,
            )
            detail = (
                str(exc) if isinstance(exc, MaintenanceError)
                else "InfoMancer could not save the uploaded database for validation. "
                     "The live database was not changed. Check available disk space "
                     "and application-data permissions, then try again."
            )
            return redirect("/settings/system", detail)
        finally:
            if candidate_path:
                candidate_path.unlink(missing_ok=True)
        threading.Thread(target=restart_after_restore, daemon=True).start()
        return templates.TemplateResponse(request, "restore_pending.html", {
            "safety_backup": safety.name, "message": "",
        })

    @librarian_post("/maintenance/updates/check")
    def check_for_updates(request: Request):
        repository = os.getenv(
            "INFOMANCER_UPDATE_REPOSITORY", "chandler-sol/InfoMancer"
        ).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            return redirect(
                "/settings/system",
                "Update checking is unavailable because the configured GitHub "
                "repository name is invalid.",
            )
        # GitHub's /releases/latest endpoint deliberately excludes prereleases.
        # InfoMancer is currently distributed as an alpha prerelease, so inspect
        # the ordered release list and choose the newest non-draft release.
        url = f"https://api.github.com/repos/{repository}/releases?per_page=20"
        try:
            request_headers = urllib.request.Request(
                url, headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"InfoMancer/{APP_VERSION}",
                },
            )
            with urllib.request.urlopen(request_headers, timeout=10) as response:
                releases = json.loads(response.read(1024 * 1024))
            if not isinstance(releases, list):
                raise ValueError("GitHub returned an unexpected releases response.")
            release = next(
                (
                    item for item in releases
                    if isinstance(item, dict) and not item.get("draft")
                ),
                None,
            )
            if release is None:
                status = {
                    "status": "no_releases",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "current_version": APP_VERSION,
                    "message": (
                        "GitHub is reachable, but this repository has no published "
                        "releases yet. InfoMancer was not changed."
                    ),
                }
                write_update_status(db.path, status)
                record_event(
                    "update",
                    "Update check completed; no published releases were found.",
                    user_id=request.state.user.id,
                )
                return redirect(
                    "/settings/system",
                    "GitHub is reachable, but no InfoMancer releases have been "
                    "published yet. The application was not changed.",
                )
            tag = str(release.get("tag_name") or "")
            if not tag:
                raise ValueError("GitHub returned a release without a version tag.")
            available = release_version_key(tag) > release_version_key(APP_VERSION)
            status = {
                "status": "available" if available else "current",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "current_version": APP_VERSION,
                "latest_version": tag,
                "release_name": release.get("name") or tag,
                "release_url": release.get("html_url") or "",
                "release_notes": str(release.get("body") or "")[:4000],
            }
            write_update_status(db.path, status)
        except (
            urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            ValueError, json.JSONDecodeError, OSError,
        ) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                explanation = (
                    "GitHub could not find the configured InfoMancer repository. "
                    "Check the update repository name in the server configuration."
                )
            elif isinstance(exc, urllib.error.HTTPError) and exc.code == 403:
                explanation = (
                    "GitHub temporarily refused the update check, usually because "
                    "its anonymous API limit was reached. Wait and try again later."
                )
            else:
                explanation = (
                    "GitHub could not be reached or returned an unreadable release "
                    "list. Check the server internet connection and try again."
                )
            record_event(
                "update", explanation,
                level="error", detail=str(exc), user_id=request.state.user.id,
            )
            return redirect(
                "/settings/system",
                f"{explanation} InfoMancer was not changed.",
            )
        return redirect(
            "/settings/system",
            (
                f"InfoMancer {tag} is available."
                if available else f"InfoMancer {APP_VERSION} is up to date."
            ),
        )

    @librarian_post("/maintenance/updates/apply")
    def request_application_update(
        request: Request, tag: str = Form(...), confirm: str = Form(""),
    ):
        if confirm != "UPDATE":
            return redirect(
                "/settings/system",
                "Update cancelled; InfoMancer was not changed.",
            )
        status = read_update_status(db.path)
        if status.get("status") != "available" or status.get("latest_version") != tag:
            return redirect(
                "/settings/system",
                "That update is no longer the verified available release. Check for "
                "updates again before applying it.",
            )
        try:
            safety = create_database_backup(db.path, "before-update")
            write_update_request(db.path, tag, request.state.user.username)
            write_update_status(db.path, {
                "status": "requested",
                "current_version": APP_VERSION,
                "latest_version": tag,
                "message": (
                    f"Update {tag} is queued. The restricted host updater will "
                    "begin it when that helper is running."
                ),
                "requested_at": datetime.now(timezone.utc).isoformat(),
            })
        except MaintenanceError as exc:
            return redirect("/settings/system", str(exc))
        record_event(
            "update", f"Application update {tag} requested.",
            context={"backup": safety.name}, user_id=request.state.user.id,
        )
        return redirect(
            "/settings/system",
            f"Update {tag} was queued and database backup {safety.name} was created. "
            "The restricted host updater will rebuild and restart InfoMancer.",
        )

    @librarian_get("/logs", response_class=HTMLResponse)
    def logs_page(
        request: Request, level: str = "", category: str = "", search: str = "",
    ):
        return templates.TemplateResponse(request, "logs.html", {
            "events": event_log.query(level=level, category=category, search=search),
            "categories": event_log.categories(), "level": level,
            "category": category, "search": search,
            "message": request.query_params.get("message", ""),
        })

    @librarian_get("/api/logs")
    def logs_api(level: str = "", category: str = "", search: str = "", limit: int = 250):
        return {
            "events": [
                dict(row) for row in event_log.query(
                    level=level, category=category, search=search, limit=limit
                )
            ]
        }

    @librarian_get("/logs/export")
    def export_logs():
        rows = event_log.query(limit=50000)
        output = io.StringIO(newline="")
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "id", "created_at", "level", "category", "message",
                "detail", "context_json", "user_name",
            ],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(csv_safe_row(row) for row in rows)
        filename = f"infomancer-logs-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
        return Response(
            output.getvalue().encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @librarian_get("/settings")
    def settings_index():
        return RedirectResponse("/settings/general", status_code=303)

    @librarian_get("/settings/{section}", response_class=HTMLResponse)
    def settings_section(request: Request, section: str):
        if section not in SETTINGS_SECTIONS:
            return auth_error_response(
                request, 404, "Settings page not found",
                "That Settings section does not exist. Choose one of the available sections.",
            )
        return render_settings(request, section)

    @librarian_post("/settings/general")
    def save_general_settings(
        request: Request, timezone_name: str = Form(...),
        default_library_view: str = Form(...), default_cover_size: str = Form(...),
    ):
        submitted = {
            "timezone": timezone_name,
            "default_library_view": default_library_view,
            "default_cover_size": default_cover_size,
        }
        try:
            validated = app_settings.validate_general(
                app_settings.get("installation_name"), timezone_name,
                default_library_view, default_cover_size,
            )
            changed = app_settings.update(validated, request.state.user.id)
        except AppSettingError as exc:
            return render_settings(request, "general", str(exc), submitted, 400)
        message = (
            f"General settings saved. {changed} setting{'s' if changed != 1 else ''} changed."
            if changed else "General settings were already up to date; nothing changed."
        )
        return redirect("/settings/general", message)

    @librarian_post("/settings/external-search")
    def save_external_search_settings(
        request: Request, search_provider_name: str = Form(...),
        search_url_template: str = Form(...),
    ):
        submitted = {
            "search_provider_name": search_provider_name,
            "search_url_template": search_url_template,
        }
        try:
            validated = app_settings.validate_external_search(
                search_provider_name, search_url_template,
            )
            changed = app_settings.update(validated, request.state.user.id)
        except AppSettingError as exc:
            return render_settings(request, "external-search", str(exc), submitted, 400)
        message = (
            f"External-search settings saved. {changed} setting{'s' if changed != 1 else ''} changed."
            if changed else "External-search settings were already up to date; nothing changed."
        )
        return redirect("/settings/external-search", message)

    @librarian_post("/settings/logging")
    def save_logging_settings(request: Request, log_level: str = Form(...)):
        try:
            validated = app_settings.validate_logging(log_level)
            changed = app_settings.update(validated, request.state.user.id)
        except AppSettingError as exc:
            return render_settings(
                request, "system", str(exc), {"log_level": log_level}, 400
            )
        label = {"info": "Standard", "verbose": "Verbose", "debug": "Debug"}[
            validated["log_level"]
        ]
        record_event(
            "settings", f"Application logging changed to {label}.",
            user_id=request.state.user.id,
        )
        message = (
            f"Logging changed to {label}. New events will use this level."
            if changed else f"Logging was already set to {label}; nothing changed."
        )
        return redirect("/settings/system", message)

    @librarian_post("/settings/hashing")
    def save_hashing_settings(
        request: Request,
        hash_mode: str = Form(...),
        hash_immediate_limit: str = Form(...),
        hash_schedule_frequency: str = Form(...),
        hash_schedule_day: str = Form(...),
        hash_schedule_time: str = Form(...),
        hash_io_intensity: str = Form(...),
        hash_pause_for_activity: str = Form("0"),
    ):
        submitted = {
            "hash_mode": hash_mode, "hash_immediate_limit": hash_immediate_limit,
            "hash_schedule_frequency": hash_schedule_frequency,
            "hash_schedule_day": hash_schedule_day,
            "hash_schedule_time": hash_schedule_time,
            "hash_io_intensity": hash_io_intensity,
            "hash_pause_for_activity": hash_pause_for_activity,
        }
        try:
            validated = app_settings.validate_hashing(
                hash_mode, hash_immediate_limit, hash_schedule_frequency,
                hash_schedule_day, hash_schedule_time, hash_io_intensity,
                hash_pause_for_activity,
            )
            changed = app_settings.update(validated, request.state.user.id)
        except AppSettingError as exc:
            return render_settings(request, "system", str(exc), submitted, 400)
        return redirect(
            "/settings/system",
            "Fingerprint settings saved." if changed else
            "Fingerprint settings were already up to date; nothing changed.",
        )

    @librarian_post("/hashes/run")
    def run_hashes_now():
        ids = media_hashes.eligible_ids()
        if not ids:
            return redirect("/settings/system", "Every current media file already has a fingerprint.")
        if not start_media_hashing(ids, "Manual file fingerprinting"):
            return redirect("/settings/system", "Fingerprinting is already running. Progress remains visible in the task widget.")
        return redirect("/settings/system", f"Fingerprinting started for {len(ids):,} files. You can continue using InfoMancer while it runs.")

    @librarian_post("/hashes/pause")
    def pause_hashes():
        with media_hash_lock:
            running = media_hash_job.get("status") in {"starting", "running"}
        if not running:
            return redirect("/settings/system", "There is no fingerprinting task to pause.")
        media_hash_pause.set()
        return redirect("/settings/system", "Fingerprinting paused after the current file. Select Resume when you are ready.")

    @librarian_post("/hashes/resume")
    def resume_hashes():
        with media_hash_lock:
            running = media_hash_job.get("status") in {"starting", "running"}
        if not running:
            return redirect("/settings/system", "There is no paused fingerprinting task to resume.")
        media_hash_pause.clear()
        return redirect("/settings/system", "Fingerprinting resumed.")

    @librarian_post("/hashes/cancel")
    def cancel_hashes():
        with media_hash_lock:
            running = media_hash_job.get("status") in {"starting", "running"}
        if not running:
            return redirect("/settings/system", "There is no fingerprinting task to cancel.")
        media_hash_cancel.set()
        media_hash_pause.clear()
        return redirect("/settings/system", "Fingerprinting is stopping after the current file. Unfinished files remain available for the next run.")

    @librarian_post("/settings/metadata/tvdb-test")
    def test_tvdb_settings_connection():
        if not tvdb.api_key:
            return redirect(
                "/settings/metadata",
                "TVDB connection was not tested because TVDB_API_KEY is not configured on the server.",
            )
        try:
            tvdb.test_connection()
        except TVDBError:
            return redirect(
                "/settings/metadata",
                "TVDB could not verify the configured key and PIN. Check the server configuration and TVDB account, then try again.",
            )
        except Exception:
            return redirect(
                "/settings/metadata",
                "TVDB could not be reached. Check the InfoMancer server's internet connection and try again.",
            )
        return redirect("/settings/metadata", "TVDB connection verified successfully.")

    @librarian_get("/sources", response_class=HTMLResponse)
    def sources(request: Request):
        with db.connect() as conn:
            roots = conn.execute(
                """SELECT r.*, COUNT(DISTINCT t.id) title_count, COUNT(f.id) file_count
                   FROM roots r LEFT JOIN titles t ON t.root_id=r.id
                   LEFT JOIN files f ON f.title_id=t.id GROUP BY r.id
                   ORDER BY r.kind, r.label, r.path"""
            ).fetchall()
        setup_state = engagement.setup_state(request.state.user.id)
        return templates.TemplateResponse(request, "sources.html", {
            "roots": roots, "jobs": scan_jobs,
            "setup_assistant_active": bool(
                setup_state and setup_state["mode"] == "guided"
                and not setup_state["completed_at"]
                and setup_state["current_step"] == "sources"
            ),
            "message": request.query_params.get("message", ""),
        })

    @librarian_get("/api/source-browser")
    def source_browser(path: str = ""):
        try:
            return list_folders(path, settings.media_browse_roots)
        except SourceBrowserError as exc:
            raise HTTPException(400, str(exc)) from exc

    @librarian_get("/api/source-preview")
    def source_preview(path: str):
        try:
            return preview_folder(path, settings.media_browse_roots)
        except SourceBrowserError as exc:
            raise HTTPException(400, str(exc)) from exc

    @librarian_post("/maintenance/optimize-database")
    def optimize_database(return_to: str = Form("")):
        destination = "/settings/system" if return_to == "/settings/system" else "/sources"
        try:
            with db.connect() as conn:
                conn.execute("ANALYZE")
                conn.execute("PRAGMA optimize")
            with db.connect() as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error as exc:
            record_event(
                "database", "Database optimization could not finish.",
                level="error", detail=str(exc),
            )
            return redirect(
                destination,
                "Database optimization could not finish. The catalog was not deleted; check the application logs for the technical cause.",
            )
        record_event("database", "Database indexes and query statistics optimized successfully.")
        return redirect(destination, "Database indexes and query statistics optimized successfully.")

    @librarian_post("/maintenance/restart")
    def restart_application(confirm: str = Form(""), return_to: str = Form("")):
        destination = "/settings/system" if return_to == "/settings/system" else "/sources"
        if confirm != "RESTART":
            return redirect(destination, "Restart cancelled; InfoMancer was not interrupted.")

        def exit_for_container_restart() -> None:
            time.sleep(1.0)
            os._exit(0)

        threading.Thread(target=exit_for_container_restart, daemon=True).start()
        return redirect(destination, "Restart requested; InfoMancer will be available again shortly.")

    @librarian_post("/roots")
    def add_root(
        path: str = Form(...), kind: str = Form(...), label: str = Form(""),
        scan_after: str = Form(""), return_to: str = Form(""),
    ):
        destination = (
            "/getting-started/sources"
            if return_to == "/getting-started/sources" else "/sources"
        )
        if kind not in {"movie", "tv"}:
            return redirect(
                destination,
                "Choose Movies or TV Shows as the library type, then try again.",
            )
        # Librarian-only, CSRF-protected configuration intentionally accepts an
        # absolute media root; existence is verified below before it is stored.
        candidate = Path(path).expanduser()
        if not candidate.is_absolute() or "\x00" in path:
            return redirect(
                destination,
                "Enter a complete absolute folder path, then try again.",
            )
        resolved = candidate.resolve()
        if not resolved.is_dir():
            return redirect(
                destination,
                f"InfoMancer cannot access {resolved}. Check the folder and server permissions, then try again.",
            )
        try:
            with db.connect() as conn:
                cursor = conn.execute(
                    "INSERT INTO roots(path, kind, label) VALUES (?, ?, ?)",
                    (str(resolved), kind, label.strip()),
                )
                root_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            return redirect(
                destination,
                "That folder is already configured as a media source; nothing was added.",
            )
        if scan_after and root_id:
            with scan_lock:
                scan_jobs[root_id] = {"status": "starting", "files": 0, "titles": 0}
            threading.Thread(target=run_scan, args=(root_id,), daemon=True).start()
            return redirect(
                destination, "Media source added successfully; its first scan has started."
            )
        return redirect(destination, "Media source added successfully.")

    @librarian_post("/roots/{root_id}/scan")
    def start_scan(root_id: int):
        with scan_all_lock:
            if scan_all_job.get("status") in {"starting", "running"}:
                return redirect("/sources", "That source is already included in Scan all")
        with scan_lock:
            if scan_jobs.get(root_id, {}).get("status") in {"starting", "running"}:
                return redirect("/sources", "That library is already scanning")
            scan_jobs[root_id] = {"status": "starting", "files": 0, "titles": 0}
        thread = threading.Thread(target=run_scan, args=(root_id,), daemon=True)
        thread.start()
        return redirect("/sources", "Scan started")

    @librarian_post("/roots/{root_id}/check")
    def check_root_connection(request: Request, root_id: int):
        try:
            result = check_source_health(root_id)
        except ValueError as exc:
            return redirect("/sources", str(exc))
        mie.analyze()
        record_event(
            "source-guard", f"Source connection check completed: {result['status']}.",
            level="warning" if result["status"] != "healthy" else "info",
            context={"root_id": root_id, **result}, user_id=request.state.user.id,
        )
        if result["status"] == "healthy":
            return redirect("/sources", "Connection confirmed. The source is available; nothing was changed.")
        return redirect(
            "/sources",
            "The source is unavailable or incomplete. Source Guard is preserving its catalog records. Check the NAS, mount, and permissions.",
        )

    @librarian_post("/roots/{root_id}/label")
    def update_root_label(root_id: int, label: str = Form("")):
        cleaned = " ".join(label.split())[:120]
        with db.connect() as conn:
            result = conn.execute(
                "UPDATE roots SET label=? WHERE id=?", (cleaned, root_id)
            )
        if result.rowcount == 0:
            return redirect("/sources", "That library root no longer exists")
        return redirect(
            "/sources", "Source name updated" if cleaned else "Source name cleared"
        )

    @librarian_post("/titles/{title_id}/scan")
    def start_title_scan(title_id: int):
        with scan_all_lock:
            if scan_all_job.get("status") in {"starting", "running"}:
                return redirect(f"/titles/{title_id}", "Wait for Scan all to finish")
        with scan_lock:
            if any(job.get("status") in {"starting", "running"} for job in scan_jobs.values()):
                return redirect(f"/titles/{title_id}", "Wait for the library scan to finish")
        with title_scan_lock:
            if title_scan_jobs.get(title_id, {}).get("status") in {"starting", "running"}:
                return redirect(f"/titles/{title_id}", "This series is already scanning")
            title_scan_jobs[title_id] = {"status": "starting", "files": 0, "label": "Series"}
        threading.Thread(target=run_title_scan, args=(title_id,), daemon=True).start()
        return redirect(f"/titles/{title_id}", "Series rescan started")

    @librarian_post("/roots/{root_id}/delete")
    def delete_root(root_id: int, confirm: str = Form("")):
        if confirm != "REMOVE":
            return redirect("/sources", "Type REMOVE to remove a catalog root")
        with db.connect() as conn:
            conn.execute("DELETE FROM roots WHERE id=?", (root_id,))
        return redirect("/sources", "Catalog root removed; media files were untouched")

    return router, {
        "export_application_settings": export_application_settings,
        "preview_application_settings": preview_application_settings,
        "apply_application_settings": apply_application_settings,
        "create_backup_from_ui": create_backup_from_ui,
        "verify_all_backups": verify_all_backups,
        "download_diagnostics": download_diagnostics,
        "download_database_backup": download_database_backup,
        "restore_server_database": restore_server_database,
        "restore_uploaded_database": restore_uploaded_database,
        "check_for_updates": check_for_updates,
        "request_application_update": request_application_update,
        "logs_page": logs_page,
        "logs_api": logs_api,
        "export_logs": export_logs,
        "settings_index": settings_index,
        "settings_section": settings_section,
        "save_general_settings": save_general_settings,
        "save_external_search_settings": save_external_search_settings,
        "save_logging_settings": save_logging_settings,
        "save_hashing_settings": save_hashing_settings,
        "run_hashes_now": run_hashes_now,
        "pause_hashes": pause_hashes,
        "resume_hashes": resume_hashes,
        "cancel_hashes": cancel_hashes,
        "test_tvdb_settings_connection": test_tvdb_settings_connection,
        "sources": sources,
        "source_browser": source_browser,
        "source_preview": source_preview,
        "optimize_database": optimize_database,
        "restart_application": restart_application,
        "add_root": add_root,
        "start_scan": start_scan,
        "check_root_connection": check_root_connection,
        "update_root_label": update_root_label,
        "start_title_scan": start_title_scan,
        "delete_root": delete_root,
    }

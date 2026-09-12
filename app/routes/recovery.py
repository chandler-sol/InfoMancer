from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from ..access import require_librarian
from ..recovery_inventory import (
    MAX_INVENTORY_PACKAGES,
    recommend_recovery_build,
    recovery_search_directories,
    scan_recovery_packages,
)
from ..recovery_package import RecoveryPackageError, RecoveryPackageService
from ..update_channels import (
    UPDATE_CHANNELS,
    channel_label,
    read_update_channel,
    validate_channel_manifest,
)
from .context import RouteContext


_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,96}$")
_STAGED_MAX_AGE = 24 * 60 * 60
_ACTIVE_STATES = {"starting", "running"}
_MAX_UPDATE_RESPONSE = 2 * 1024 * 1024
_DEFAULT_MANIFEST_BASE_URL = (
    "https://github.com/chandler-sol/InfoMancer/releases/download/update-channels"
)


def build_router(ctx: RouteContext):
    router = APIRouter()
    APP_VERSION = ctx.get("APP_VERSION")
    db = ctx.live("db")
    settings = ctx.live("settings")
    templates = ctx.live("templates")
    redirect = ctx.live("redirect")
    record_event = ctx.live("record_event")
    restart_after_restore = ctx.live("restart_after_restore")
    other_background_work_running = ctx.live("_other_background_work_running")
    imdb_genre_job = ctx.live("imdb_genre_job")
    duplicate_verify_job = ctx.live("duplicate_verify_job")
    media_hash_job = ctx.live("media_hash_job")
    trash_cleanup_job = ctx.live("trash_cleanup_job")
    restore_lock = threading.Lock()

    def recovery_service() -> RecoveryPackageService:
        # Resolve the live database path per request so test/runtime data-dir swaps
        # do not leave this router pinned to the process-start path.
        return RecoveryPackageService(Path(db.path), APP_VERSION)

    def restore_work_running() -> bool:
        if other_background_work_running():
            return True
        return any(
            job.get("status") in _ACTIVE_STATES
            for job in (
                imdb_genre_job,
                duplicate_verify_job,
                media_hash_job,
                trash_cleanup_job,
            )
        )

    def librarian_get(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.get(path, dependencies=dependencies, **kwargs)

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

    def staging_dir() -> Path:
        directory = Path(db.path).parent / "restore-staging"
        directory.mkdir(parents=True, exist_ok=True)
        try:
            directory.chmod(0o700)
        except OSError:
            pass
        return directory

    def cleanup_staging() -> None:
        cutoff = time.time() - _STAGED_MAX_AGE
        try:
            for candidate in staging_dir().glob("*.infomancer-backup"):
                try:
                    if candidate.stat().st_mtime < cutoff:
                        candidate.unlink(missing_ok=True)
                except OSError:
                    continue
        except OSError:
            return

    def staged_path(token: str) -> Path:
        if not _TOKEN_RE.fullmatch(token):
            raise RecoveryPackageError("That recovery preview is no longer valid. Upload the package again.")
        return staging_dir() / f"{token}.infomancer-backup"

    def manifest_base_url() -> str:
        configured = os.getenv("INFOMANCER_UPDATE_MANIFEST_BASE_URL")
        if configured is None:
            return _DEFAULT_MANIFEST_BASE_URL
        configured = configured.strip()
        if configured.casefold() in {"off", "disabled", "none"}:
            return ""
        return configured.rstrip("/")

    def fetch_json(url: str) -> object:
        parsed = urlparse(url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError("Update metadata URL must use HTTP or HTTPS.")
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json, application/vnd.github+json",
                "User-Agent": f"InfoMancer/{APP_VERSION}",
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = response.read(_MAX_UPDATE_RESPONSE + 1)
        if len(payload) > _MAX_UPDATE_RESPONSE:
            raise ValueError("Update metadata response is unexpectedly large.")
        return json.loads(payload)

    def qualified_recovery_manifests() -> tuple[list[dict], list[str]]:
        base = manifest_base_url()
        if not base:
            return [], ["Qualified update-channel metadata is disabled for this installation."]
        manifests: list[dict] = []
        errors: list[str] = []
        for channel in UPDATE_CHANNELS:
            url = f"{base}/{channel}.json"
            try:
                manifests.append(validate_channel_manifest(fetch_json(url), channel))
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    errors.append(f"{channel_label(channel)} metadata could not be loaded.")
            except (
                urllib.error.URLError,
                TimeoutError,
                ValueError,
                json.JSONDecodeError,
                OSError,
            ):
                errors.append(f"{channel_label(channel)} metadata could not be validated.")
        return manifests, errors

    def recovery_page_context(request: Request) -> dict:
        scan_requested = request.query_params.get("scan") == "1"
        directories = recovery_search_directories(Path(db.path))
        inventory: list[dict] = []
        manifest_errors: list[str] = []
        manifests: list[dict] = []
        selected_channel = read_update_channel(Path(db.path))
        if scan_requested:
            inventory = scan_recovery_packages(
                recovery_service(), directories, MAX_INVENTORY_PACKAGES
            )
            manifests, manifest_errors = qualified_recovery_manifests()
            for item in inventory:
                item["recommendation"] = recommend_recovery_build(
                    item, manifests, selected_channel
                )
        return {
            "message": request.query_params.get("message", ""),
            "scan_requested": scan_requested,
            "recovery_inventory": inventory,
            "recovery_locations": [str(directory) for directory in directories],
            "recovery_scan_limit": MAX_INVENTORY_PACKAGES,
            "recovery_manifest_errors": manifest_errors,
            "recovery_manifest_count": len(manifests),
            "update_channel": selected_channel,
            "update_channel_label": channel_label(selected_channel),
        }

    @librarian_get("/settings/recovery", response_class=HTMLResponse)
    def recovery_page(request: Request):
        cleanup_staging()
        return templates.TemplateResponse(
            request,
            "recovery_restore.html",
            recovery_page_context(request),
        )

    @librarian_post("/settings/recovery/preview", response_class=HTMLResponse)
    async def preview_recovery_package(
        request: Request,
        recovery_file: UploadFile = File(...),
    ):
        cleanup_staging()
        package_service = recovery_service()
        token = __import__("secrets").token_urlsafe(32)
        candidate = staged_path(token)
        try:
            with candidate.open("xb") as handle:
                total = 0
                while chunk := await recovery_file.read(1024 * 1024):
                    total += len(chunk)
                    if total > package_service.MAX_PACKAGE_BYTES:
                        raise RecoveryPackageError(
                            "The uploaded recovery package is larger than the 4 GB restore limit."
                        )
                    handle.write(chunk)
            try:
                candidate.chmod(0o600)
            except OSError:
                pass
            summary = package_service.verify(candidate)
        except (RecoveryPackageError, OSError) as exc:
            candidate.unlink(missing_ok=True)
            record_event(
                "restore", "Portable recovery preview was rejected.",
                level="error", detail=str(exc), user_id=request.state.user.id,
            )
            message = (
                str(exc) if isinstance(exc, RecoveryPackageError)
                else "InfoMancer could not stage that recovery package. Check free disk space and application-data permissions."
            )
            return redirect("/settings/recovery", message)

        record_event(
            "restore", "Portable recovery package verified for preview.",
            context={
                "source_version": summary["app_version"],
                "artwork_files": summary["artwork_files"],
                "database_size": summary["database_size"],
            },
            user_id=request.state.user.id,
        )
        return templates.TemplateResponse(request, "recovery_restore_preview.html", {
            "summary": summary,
            "staged_token": token,
            "source_name": recovery_file.filename or "recovery package",
            "message": "",
        })

    @librarian_post("/settings/recovery/apply", response_class=HTMLResponse)
    def apply_recovery_package(
        request: Request,
        staged_token: str = Form(...),
        confirm: str = Form(""),
    ):
        if confirm != "RESTORE":
            return redirect(
                "/settings/recovery",
                "Portable recovery cancelled; the live installation was not changed.",
            )
        if not restore_lock.acquire(blocking=False):
            return redirect(
                "/settings/recovery",
                "Another portable recovery is already in progress.",
            )
        try:
            candidate = staged_path(staged_token)
            if not candidate.is_file():
                raise RecoveryPackageError(
                    "That verified recovery package is no longer staged. Upload it again before restoring."
                )
            if restore_work_running():
                raise RecoveryPackageError(
                    "Wait for active scans, metadata, fingerprint, duplicate, trash-cleanup, or maintenance work to finish before restoring."
                )
            record_event(
                "restore", "Portable recovery restore started.",
                user_id=request.state.user.id,
            )
            # Check once more after the event write so a task that was already
            # transitioning to running cannot overlap the live database swap.
            if restore_work_running():
                raise RecoveryPackageError(
                    "Background work started while recovery was preparing. Wait for it to finish and try again."
                )
            result = recovery_service().restore(candidate, settings.media_browse_roots)
        except RecoveryPackageError as exc:
            record_event(
                "restore", "Portable recovery restore failed.",
                level="error", detail=str(exc), user_id=request.state.user.id,
            )
            return redirect("/settings/recovery", str(exc))
        finally:
            restore_lock.release()
            try:
                staged_path(staged_token).unlink(missing_ok=True)
            except (RecoveryPackageError, OSError):
                pass

        record_event(
            "restore", "Portable recovery restore completed.",
            context={
                "source_version": result["app_version"],
                "artwork_files": result["restored_artwork_files"],
                "safety_package": result["safety_package"],
            },
        )
        threading.Thread(target=restart_after_restore, daemon=True).start()
        return templates.TemplateResponse(request, "recovery_restore_pending.html", {
            "source_version": result["app_version"],
            "artwork_files": result["restored_artwork_files"],
            "safety_package": result["safety_package"],
            "message": "",
        })

    return router, {}

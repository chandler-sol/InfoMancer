from __future__ import annotations

import re
import secrets
import tempfile
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from ..access import require_librarian
from ..recovery_package import RecoveryPackageError, RecoveryPackageService
from .context import RouteContext


_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,96}$")
_STAGED_MAX_AGE = 24 * 60 * 60


def build_router(ctx: RouteContext):
    router = APIRouter()
    APP_VERSION = ctx.get("APP_VERSION")
    db = ctx.live("db")
    settings = ctx.live("settings")
    templates = ctx.live("templates")
    redirect = ctx.live("redirect")
    record_event = ctx.live("record_event")
    restart_after_restore = ctx.live("restart_after_restore")
    service = RecoveryPackageService(db.path, APP_VERSION)

    def librarian_get(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.get(path, dependencies=dependencies, **kwargs)

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

    def staging_dir() -> Path:
        directory = db.path.parent / "restore-staging"
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

    @librarian_get("/settings/recovery", response_class=HTMLResponse)
    def recovery_page(request: Request):
        cleanup_staging()
        return templates.TemplateResponse(request, "recovery_restore.html", {
            "message": request.query_params.get("message", ""),
        })

    @librarian_post("/settings/recovery/preview", response_class=HTMLResponse)
    async def preview_recovery_package(
        request: Request,
        recovery_file: UploadFile = File(...),
    ):
        cleanup_staging()
        token = secrets.token_urlsafe(32)
        candidate = staged_path(token)
        try:
            with candidate.open("xb") as handle:
                total = 0
                while chunk := await recovery_file.read(1024 * 1024):
                    total += len(chunk)
                    if total > service.MAX_PACKAGE_BYTES:
                        raise RecoveryPackageError(
                            "The uploaded recovery package is larger than the 4 GB restore limit."
                        )
                    handle.write(chunk)
            try:
                candidate.chmod(0o600)
            except OSError:
                pass
            summary = service.verify(candidate)
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
        try:
            candidate = staged_path(staged_token)
            if not candidate.is_file():
                raise RecoveryPackageError(
                    "That verified recovery package is no longer staged. Upload it again before restoring."
                )
            # This event lands before the safety package is created, so the rollback
            # package itself records that a restore was intentionally started.
            record_event(
                "restore", "Portable recovery restore started.",
                user_id=request.state.user.id,
            )
            result = service.restore(candidate, settings.media_browse_roots)
        except RecoveryPackageError as exc:
            record_event(
                "restore", "Portable recovery restore failed.",
                level="error", detail=str(exc), user_id=request.state.user.id,
            )
            return redirect("/settings/recovery", str(exc))
        finally:
            try:
                staged_path(staged_token).unlink(missing_ok=True)
            except (RecoveryPackageError, OSError):
                pass

        # The restored database may contain a different user set, so do not attach
        # the old request user id to the post-commit event.
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

    return router

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request

from ..access import require_librarian
from ..media_identity.fast import FastIdentityScanError, FastIdentityService
from ..media_identity.service import (
    MediaIdentityDecisionError,
    MediaIdentityDecisionService,
)
from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    db = ctx.live("db")
    templates = ctx.live("templates")
    HTMLResponse = ctx.get("HTMLResponse")
    HTTPException = ctx.get("HTTPException")
    redirect = ctx.live("redirect")
    record_event = ctx.live("record_event")
    analyze_library_health_with_activity = ctx.live(
        "analyze_library_health_with_activity"
    )

    fast = FastIdentityService(db)
    decisions = MediaIdentityDecisionService(db)

    def librarian_get(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.get(path, dependencies=dependencies, **kwargs)

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

    def _refresh_findings(user_id: int | None = None) -> bool:
        try:
            analyze_library_health_with_activity()
        except sqlite3.Error as exc:
            record_event(
                "mie",
                "Library Health could not refresh after an Episode Identity decision.",
                level="warning",
                detail=str(exc),
                context={"operation": "episode-identity-refresh"},
                user_id=user_id,
            )
            return False
        return True

    @librarian_post("/files/{file_id}/episode-identity/fast")
    def verify_episode_identity(request: Request, file_id: int):
        with db.connect() as conn:
            row = conn.execute(
                "SELECT title_id FROM files WHERE id=?", (int(file_id),)
            ).fetchone()
        if not row:
            raise HTTPException(404, "Media file not found.")
        title_id = int(row["title_id"])
        try:
            scan = fast.scan_file(
                int(file_id), requested_by=request.state.user.id
            )
            resolution = decisions.resolve_scan(scan.scan_id)
            findings_refreshed = _refresh_findings(request.state.user.id)
        except (FastIdentityScanError, MediaIdentityDecisionError) as exc:
            record_event(
                "mie",
                f"Episode Identity verification could not complete for file {file_id}.",
                level="warning",
                detail=str(exc),
                context={"file_id": int(file_id), "title_id": title_id},
                user_id=request.state.user.id,
            )
            return redirect(
                f"/titles/{title_id}",
                f"Episode Identity could not complete: {exc}",
            )
        record_event(
            "mie",
            f"Episode Identity Fast verification completed for file {file_id}.",
            context={
                "file_id": int(file_id),
                "title_id": title_id,
                "scan_id": scan.scan_id,
                "result_state": resolution.state.value,
            },
            user_id=request.state.user.id,
        )
        message = (
            "Episode Identity verification completed. Review the evidence before making any correction."
        )
        if not scan.provider_cache_used:
            message += (
                " TVDB episode identity metadata was unavailable, so this scan used "
                "reduced evidence. Refresh TVDB metadata and verify again to include "
                "provider synopsis and episode-order evidence."
            )
        if not findings_refreshed:
            message += " Library Health will catch up on the next successful analysis."
        return redirect(
            f"/episode-identity/scans/{scan.scan_id}",
            message,
        )

    @librarian_get(
        "/episode-identity/scans/{scan_id}",
        response_class=HTMLResponse,
    )
    def episode_identity_detail(request: Request, scan_id: int):
        try:
            detail = decisions.scan_detail(int(scan_id))
        except MediaIdentityDecisionError as exc:
            raise HTTPException(404, str(exc)) from exc
        response = templates.TemplateResponse(
            request,
            "episode_identity.html",
            {
                "identity": detail,
                "message": request.query_params.get("message", ""),
            },
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @librarian_get(
        "/episode-identity/scans/{scan_id}/rename-preview",
        response_class=HTMLResponse,
    )
    def episode_identity_rename_preview(request: Request, scan_id: int):
        try:
            preview = decisions.rename_preview(int(scan_id))
        except MediaIdentityDecisionError as exc:
            raise HTTPException(404, str(exc)) from exc
        response = templates.TemplateResponse(
            request,
            "episode_identity_rename_preview.html",
            {"preview": preview, "identity": preview["scan"]},
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @librarian_post(
        "/episode-identity/scans/{scan_id}/confirm-current"
    )
    def confirm_current_identity(request: Request, scan_id: int):
        try:
            decisions.confirm_current(int(scan_id), request.state.user.id)
            findings_refreshed = _refresh_findings(request.state.user.id)
        except MediaIdentityDecisionError as exc:
            return redirect(
                f"/episode-identity/scans/{scan_id}",
                f"Identity confirmation was not saved: {exc}",
            )
        record_event(
            "mie",
            f"Episode Identity scan {scan_id}: current filename marked correct.",
            context={"scan_id": int(scan_id), "decision": "confirm-current"},
            user_id=request.state.user.id,
        )
        message = (
            "Marked correct for this exact media snapshot. A changed file will require confirmation again."
        )
        if not findings_refreshed:
            message += " Library Health will catch up on the next successful analysis."
        return redirect(
            f"/episode-identity/scans/{scan_id}",
            message,
        )

    @librarian_post(
        "/episode-identity/scans/{scan_id}/confirm-best"
    )
    def confirm_best_identity(request: Request, scan_id: int):
        try:
            decisions.confirm_best(int(scan_id), request.state.user.id)
            findings_refreshed = _refresh_findings(request.state.user.id)
        except MediaIdentityDecisionError as exc:
            return redirect(
                f"/episode-identity/scans/{scan_id}",
                f"Suggested identity was not confirmed: {exc}",
            )
        record_event(
            "mie",
            f"Episode Identity scan {scan_id}: suggested content identity confirmed.",
            context={"scan_id": int(scan_id), "decision": "confirm-best"},
            user_id=request.state.user.id,
        )
        message = (
            "Suggested content identity confirmed. The catalog claim and filename were not changed."
        )
        if not findings_refreshed:
            message += " Library Health will catch up on the next successful analysis."
        return redirect(
            f"/episode-identity/scans/{scan_id}",
            message,
        )

    return router, {
        "episode_identity_fast": fast,
        "episode_identity_decisions": decisions,
        "verify_episode_identity": verify_episode_identity,
        "episode_identity_detail": episode_identity_detail,
        "episode_identity_rename_preview": episode_identity_rename_preview,
        "confirm_current_identity": confirm_current_identity,
        "confirm_best_identity": confirm_best_identity,
    }

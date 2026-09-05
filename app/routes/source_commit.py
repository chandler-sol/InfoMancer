from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request

from ..access import require_librarian
from ..source_browser import SourceBrowserError, allowed_roots, validate_browse_path
from .context import RouteContext


def _validated_source_path(path: str, configured_roots) -> str:
    """Validate a submitted source with the same Windows-safe path logic as Browse.

    The folder browser deliberately tolerates WinError 1272 when Windows final-path
    resolution fails but the mapped NFS/SMB directory can still be opened directly.
    The source commit path must use that same contract. Calling Path.resolve() here
    would reintroduce the exact failure the browser already worked around.
    """
    if not path.strip() or "\x00" in path:
        raise SourceBrowserError("Choose a complete media folder path, then try again.")
    roots = allowed_roots(tuple(configured_roots))
    if not roots:
        raise SourceBrowserError(
            "None of the configured media locations are currently accessible to InfoMancer."
        )
    return str(validate_browse_path(path, roots))


def build_router(ctx: RouteContext):
    router = APIRouter()
    db = ctx.live("db")
    record_event = ctx.live("record_event")
    redirect = ctx.live("redirect")
    run_scan = ctx.live("run_scan")
    scan_jobs = ctx.live("scan_jobs")
    scan_lock = ctx.live("scan_lock")
    settings = ctx.live("settings")
    threading = ctx.live("threading")

    @router.post("/roots", dependencies=[Depends(require_librarian)])
    def add_root_safe(
        request: Request,
        path: str = Form(...),
        kind: str = Form(...),
        label: str = Form(""),
        scan_after: str = Form(""),
        return_to: str = Form(""),
    ):
        destination = (
            "/getting-started/sources"
            if return_to == "/getting-started/sources"
            else "/sources"
        )
        if kind not in {"movie", "tv"}:
            return redirect(
                destination,
                "Choose Movies or TV Shows as the library type, then try again.",
            )

        try:
            validated_path = _validated_source_path(path, settings.media_browse_roots)
        except SourceBrowserError as exc:
            record_event(
                "source",
                "Media source was not added because its folder could not be validated.",
                level="warning",
                detail=str(exc),
                context={"operation": "add_source"},
                user_id=request.state.user.id,
            )
            return redirect(destination, f"Media source was not added. {exc}")

        cleaned_label = " ".join(label.split())[:120]
        try:
            with db.connect() as conn:
                cursor = conn.execute(
                    "INSERT INTO roots(path, kind, label) VALUES (?, ?, ?)",
                    (validated_path, kind, cleaned_label),
                )
                root_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            return redirect(
                destination,
                "That folder is already configured as a media source; nothing was added.",
            )

        record_event(
            "source",
            "Media source added to InfoMancer.",
            context={"root_id": root_id, "kind": kind, "operation": "add_source"},
            user_id=request.state.user.id,
        )

        if scan_after and root_id:
            with scan_lock:
                scan_jobs[root_id] = {
                    "status": "starting", "files": 0, "titles": 0,
                }
            threading.Thread(target=run_scan, args=(root_id,), daemon=True).start()
            return redirect(
                destination,
                "Media source added successfully; its first scan has started.",
            )
        return redirect(destination, "Media source added successfully.")

    return router, {"add_root_safe": add_root_safe}

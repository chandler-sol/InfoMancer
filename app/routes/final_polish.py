from __future__ import annotations

import threading

from fastapi import APIRouter, Depends

from ..access import require_librarian
from .context import RouteContext


_SCAN_ALL_CANCEL = threading.Event()


def build_router(ctx: RouteContext):
    router = APIRouter()
    JSONResponse = ctx.get("JSONResponse")
    handle_import_hashing = ctx.live("handle_import_hashing")
    media_hash_cancel = ctx.live("media_hash_cancel")
    media_hash_job = ctx.live("media_hash_job")
    media_hash_lock = ctx.live("media_hash_lock")
    media_hash_pause = ctx.live("media_hash_pause")
    record_event = ctx.live("record_event")
    run_scan = ctx.live("run_scan")
    scan_all_job = ctx.live("scan_all_job")
    scan_all_lock = ctx.live("scan_all_lock")
    scan_jobs = ctx.live("scan_jobs")
    scan_lock = ctx.live("scan_lock")

    def cancellable_run_scan_all(roots: list[tuple[int, str]]) -> None:
        """Run Scan All with cooperative cancellation between source roots.

        A source scan is allowed to finish once it has started so Source Guard and
        catalog transactions are never interrupted halfway through. A cancellation
        request prevents the next source from starting. A request made while the
        worker is still starting is preserved rather than cleared by thread startup.
        """
        total = len(roots)
        with scan_all_lock:
            scan_all_job.clear()
            scan_all_job.update({
                "status": "running", "total": total, "completed": 0,
                "errors": 0, "protected": 0, "current_root_id": None,
                "current_label": "", "files": 0, "titles": 0,
            })

        errors = 0
        protected = 0
        completed_count = 0
        changed_files: list[int] = []
        record_event("scan", f"Scan all started for {total:,} sources.")

        try:
            for root_id, label in roots:
                if _SCAN_ALL_CANCEL.is_set():
                    break
                with scan_all_lock:
                    scan_all_job.update({
                        "current_root_id": root_id, "current_label": label,
                        "completed": completed_count, "files": 0, "titles": 0,
                    })
                changed_files.extend(run_scan(root_id, hash_after=False))
                completed_count += 1
                with scan_lock:
                    job = scan_jobs.get(root_id, {})
                    if job.get("status") == "error":
                        errors += 1
                    elif job.get("source_status") == "degraded":
                        protected += 1
                with scan_all_lock:
                    scan_all_job.update({
                        "completed": completed_count, "errors": errors,
                        "protected": protected,
                    })

            cancelled = _SCAN_ALL_CANCEL.is_set()
            with scan_all_lock:
                scan_all_job.update({
                    "status": "cancelled" if cancelled else "complete",
                    "completed": completed_count,
                    "errors": errors,
                    "current_root_id": None,
                    "current_label": "",
                })

            if changed_files:
                handle_import_hashing(
                    changed_files,
                    "Fingerprinting new or changed media from all sources",
                )

            if cancelled:
                record_event(
                    "scan",
                    f"Scan all stopped after {completed_count:,} of {total:,} sources.",
                    level="warning",
                    context={
                        "operation": "scan_all_cancel",
                        "completed": completed_count,
                        "total": total,
                    },
                )
        finally:
            # A completed worker owns and clears the request. This also allows a
            # cancellation submitted during the 'starting' state to survive until
            # the worker sees it for the first time.
            _SCAN_ALL_CANCEL.clear()

    # Main's existing /scan-all handler resolves this global at execution time, so
    # replacing it here adds cancellation without duplicating the public scan route.
    ctx.set("run_scan_all", cancellable_run_scan_all)

    @router.post(
        "/api/tasks/{task_id}/cancel",
        dependencies=[Depends(require_librarian)],
    )
    def cancel_background_task(task_id: str):
        if task_id == "scan-all":
            with scan_all_lock:
                running = scan_all_job.get("status") in {"starting", "running"}
            if not running:
                return JSONResponse(
                    {"ok": False, "detail": "Scan All is no longer running."},
                    status_code=409,
                )
            _SCAN_ALL_CANCEL.set()
            record_event(
                "scan",
                "Scan All cancellation requested.",
                context={"operation": "task_cancel", "task_id": task_id},
            )
            return {
                "ok": True,
                "task_id": task_id,
                "detail": "Stopping after the current source finishes.",
            }

        if task_id == "media-fingerprints":
            with media_hash_lock:
                status = str(media_hash_job.get("status") or "")
            if status == "starting":
                return JSONResponse(
                    {
                        "ok": False,
                        "detail": "Fingerprinting is still starting. Try cancel again in a moment.",
                    },
                    status_code=409,
                )
            if status != "running":
                return JSONResponse(
                    {"ok": False, "detail": "Fingerprinting is no longer running."},
                    status_code=409,
                )
            media_hash_cancel.set()
            media_hash_pause.clear()
            record_event(
                "media",
                "Fingerprint cancellation requested.",
                context={"operation": "task_cancel", "task_id": task_id},
            )
            return {
                "ok": True,
                "task_id": task_id,
                "detail": "Stopping after the current file finishes.",
            }

        return JSONResponse(
            {
                "ok": False,
                "detail": "This task cannot be cancelled safely while it is running.",
            },
            status_code=409,
        )

    return router, {
        "cancel_background_task": cancel_background_task,
        "cancellable_run_scan_all": cancellable_run_scan_all,
    }

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import APIRouter, Depends

from ..access import require_librarian
from ..path_reconciliation import (
    clear_missing_path_failures,
    missing_file_ids,
    reconcile_root_paths,
)
from .context import RouteContext


_SCAN_ALL_CANCEL = threading.Event()


def build_router(ctx: RouteContext):
    router = APIRouter()
    JSONResponse = ctx.get("JSONResponse")
    base_run_media_inspection = ctx.get("run_media_inspection")
    base_run_scan = ctx.get("run_scan")
    db = ctx.live("db")
    handle_import_hashing = ctx.live("handle_import_hashing")
    media_hash_cancel = ctx.live("media_hash_cancel")
    media_hash_job = ctx.live("media_hash_job")
    media_hash_lock = ctx.live("media_hash_lock")
    media_hash_pause = ctx.live("media_hash_pause")
    record_event = ctx.live("record_event")
    scan_all_job = ctx.live("scan_all_job")
    scan_all_lock = ctx.live("scan_all_lock")
    scan_jobs = ctx.live("scan_jobs")
    scan_lock = ctx.live("scan_lock")

    def reconciling_run_scan(
        root_id: int, *, hash_after: bool = True, force_cleanup: bool = False,
    ) -> list[int]:
        """Reconcile confident path changes before the normal guarded scan."""
        before_missing = set(missing_file_ids(db, root_id))
        reconciliation = reconcile_root_paths(db, root_id) if before_missing else {
            "available": True, "reconciled": 0,
        }
        changed = base_run_scan(
            root_id, hash_after=hash_after, force_cleanup=force_cleanup,
        )

        with scan_lock:
            job = dict(scan_jobs.get(root_id, {}))
        protected = (
            job.get("status") == "error"
            or job.get("source_status") == "degraded"
            or reconciliation.get("available") is False
        )
        if protected:
            cleared = clear_missing_path_failures(db, root_id)
            if cleared:
                record_event(
                    "source-guard",
                    f"Suppressed {cleared:,} per-file path alert{'s' if cleared != 1 else ''} while the source is unavailable.",
                    level="warning",
                    context={
                        "root_id": root_id,
                        "operation": "path_alert_suppression",
                        "suppressed": cleared,
                    },
                )
            return changed

        reconciled = int(reconciliation.get("reconciled") or 0)
        if reconciled:
            record_event(
                "scan",
                f"Reconciled {reconciled:,} media path change{'s' if reconciled != 1 else ''} during the source scan.",
                context={
                    "root_id": root_id,
                    "operation": "path_reconciliation",
                    "reconciled": reconciled,
                },
            )

        if before_missing:
            placeholders = ",".join("?" for _ in before_missing)
            with db.connect() as conn:
                remaining = {
                    int(row["id"])
                    for row in conn.execute(
                        f"SELECT id FROM files WHERE id IN ({placeholders})",
                        tuple(sorted(before_missing)),
                    ).fetchall()
                }
            disappeared = len(before_missing - remaining)
            if disappeared:
                record_event(
                    "scan",
                    f"{disappeared:,} cataloged media file{'s were' if disappeared != 1 else ' was'} no longer present after source reconciliation.",
                    level="warning",
                    context={
                        "root_id": root_id,
                        "operation": "media_disappeared",
                        "missing_count": disappeared,
                    },
                )
        return changed

    # Replace the live scan helper so manual scans, Scan All, scheduled work, and
    # inspection preflight all use the same rename-vs-missing distinction.
    ctx.set("run_scan", reconciling_run_scan)
    run_scan = ctx.live("run_scan")

    def _inspection_rows(file_ids: list[int] | None):
        with db.connect() as conn:
            if file_ids:
                placeholders = ",".join("?" for _ in file_ids)
                return conn.execute(
                    f"""SELECT f.id, f.path, t.root_id
                        FROM files f JOIN titles t ON t.id=f.title_id
                        WHERE f.id IN ({placeholders}) ORDER BY f.id""",
                    tuple(file_ids),
                ).fetchall()
            return conn.execute(
                """SELECT f.id, f.path, t.root_id
                   FROM files f JOIN titles t ON t.id=f.title_id
                   WHERE f.media_info_at IS NULL OR f.size_bytes<=0
                      OR f.media_info_error IS NOT NULL
                   ORDER BY f.id"""
            ).fetchall()

    def reconciling_run_media_inspection(file_ids: list[int] | None = None):
        """Resolve missing paths at source level before FFprobe sees them."""
        rows = _inspection_rows(file_ids)
        roots_to_reconcile: set[int] = set()
        for row in rows:
            try:
                path = Path(row["path"])
                available = path.exists() and path.is_file()
            except OSError:
                available = False
            if not available:
                roots_to_reconcile.add(int(row["root_id"]))

        # One source reconciliation replaces what used to become N identical
        # missing-path FFprobe alerts during a bulk rename or storage outage.
        for root_id in sorted(roots_to_reconcile):
            reconciling_run_scan(root_id, hash_after=False)

        safe_ids: list[int] = []
        for row in _inspection_rows(file_ids):
            try:
                path = Path(row["path"])
                if path.exists() and path.is_file():
                    safe_ids.append(int(row["id"]))
            except OSError:
                continue

        # The original helper treats [] like "inspect everything". A sentinel id
        # keeps an intentionally empty preflight empty while still letting the
        # worker update its normal task state.
        return base_run_media_inspection(safe_ids or [-1])

    ctx.set("run_media_inspection", reconciling_run_media_inspection)

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

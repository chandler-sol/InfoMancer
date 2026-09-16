from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..access import require_librarian
from ..maintenance_gate import APPLICATION_MAINTENANCE_GATE
from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    Form = ctx.get("Form")
    duplicate_verify_job = ctx.live("duplicate_verify_job")
    duplicate_verify_lock = ctx.live("duplicate_verify_lock")
    duplicates = ctx.live("duplicates")
    re = ctx.live("re")
    record_event = ctx.live("record_event")
    redirect = ctx.live("redirect")
    threading = ctx.live("threading")

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

    @librarian_post("/duplicates/bulk-action")
    def bulk_duplicate_action(
        request: Request, pairs: list[str] = Form(default=[]), action: str = Form(...),
    ):
        allowed = {"ignored", "not_duplicate", "active", "verify"}
        if action not in allowed:
            return redirect(
                "/duplicates",
                "That bulk review choice was not recognized. Nothing changed.",
            )
        parsed: list[tuple[int, int]] = []
        for value in list(dict.fromkeys(pairs))[:500]:
            if not re.fullmatch(r"\d+:\d+", value):
                continue
            left, right = (int(part) for part in value.split(":", 1))
            if left != right:
                parsed.append((left, right))
        if not parsed:
            return redirect("/duplicates", "Select at least one duplicate candidate first.")

        user_id = request.state.user.id
        if action != "verify":
            changed = sum(
                duplicates.decide(left, right, action, user_id)
                for left, right in parsed
            )
            labels = {
                "ignored": "ignored for now",
                "not_duplicate": "kept as intentional alternatives",
                "active": "returned to review",
            }
            message = (
                f"{changed:,} duplicate candidate pair"
                f"{'s' if changed != 1 else ''} {labels[action]}. "
                "No media files were changed."
            )
            record_event(
                "duplicates", message,
                context={"pairs": changed, "action": action}, user_id=user_id,
            )
            return redirect("/duplicates", message)

        with duplicate_verify_lock:
            if duplicate_verify_job.get("status") in {"starting", "running"}:
                return redirect(
                    "/duplicates",
                    "A duplicate verification is already running. Its progress is shown in the task panel.",
                )
            duplicate_verify_job.clear()
            duplicate_verify_job.update({
                "status": "starting", "total": len(parsed), "processed": 0,
                "detail": "Preparing selected file comparisons",
            })

        def run_bulk_verification() -> None:
            with APPLICATION_MAINTENANCE_GATE.operation_lease() as admitted:
                if not admitted:
                    with duplicate_verify_lock:
                        duplicate_verify_job.update({
                            "status": "paused", "processed": 0,
                            "detail": "Recovery is in progress. Duplicate verification was not started.",
                        })
                    return
                exact = different = failed = 0
                for index, (left, right) in enumerate(parsed, 1):
                    with duplicate_verify_lock:
                        duplicate_verify_job.update({
                            "status": "running", "processed": index - 1,
                            "detail": f"Verifying pair {index:,} of {len(parsed):,}",
                        })
                    try:
                        result = duplicates.verify(left, right, user_id)
                        exact += result == "exact"
                        different += result != "exact"
                    except (OSError, ValueError):
                        failed += 1
                    with duplicate_verify_lock:
                        duplicate_verify_job["processed"] = index
                message = (
                    f"Verified {len(parsed):,} pairs: {exact:,} exact, "
                    f"{different:,} different, {failed:,} unavailable. No files were changed."
                )
                record_event(
                    "duplicates", message,
                    context={
                        "pairs": len(parsed), "exact": exact,
                        "different": different, "failed": failed,
                    },
                    user_id=user_id,
                )
                with duplicate_verify_lock:
                    duplicate_verify_job.update({"status": "complete", "detail": message})

        threading.Thread(
            target=run_bulk_verification,
            daemon=True,
            name="infomancer-duplicate-verification-bulk",
        ).start()
        return redirect(
            "/duplicates",
            f"Verification started for {len(parsed):,} selected pairs. Progress is shown in the task panel.",
        )

    @librarian_post("/duplicates/{file_a_id}/{file_b_id}/verify")
    def verify_duplicate(request: Request, file_a_id: int, file_b_id: int):
        with duplicate_verify_lock:
            if duplicate_verify_job.get("status") in {"starting", "running"}:
                return redirect(
                    "/duplicates",
                    "A duplicate verification is already running. Its progress is shown in the task panel.",
                )
            duplicate_verify_job.clear()
            duplicate_verify_job.update({
                "status": "starting",
                "detail": "Preparing to read both files byte for byte",
            })

        user_id = request.state.user.id

        def run_verification() -> None:
            with APPLICATION_MAINTENANCE_GATE.operation_lease() as admitted:
                if not admitted:
                    with duplicate_verify_lock:
                        duplicate_verify_job.update({
                            "status": "paused",
                            "detail": "Recovery is in progress. Duplicate verification was not started.",
                        })
                    return
                try:
                    with duplicate_verify_lock:
                        duplicate_verify_job.update({
                            "status": "running",
                            "detail": "Reading both files byte for byte; large files may take several minutes",
                        })
                    result = duplicates.verify(file_a_id, file_b_id, user_id)
                    message = (
                        "Verification finished: the files are byte-for-byte identical. "
                        "InfoMancer did not delete or move either file."
                        if result == "exact" else
                        "Verification finished: the files contain different bytes. They may be "
                        "different encodes or editions, and InfoMancer did not change either file."
                    )
                    record_event(
                        "duplicates", message,
                        context={
                            "file_a_id": file_a_id, "file_b_id": file_b_id,
                            "result": result,
                        },
                        user_id=user_id,
                    )
                    with duplicate_verify_lock:
                        duplicate_verify_job.update({
                            "status": "complete", "detail": message, "result": result,
                        })
                except (OSError, ValueError) as exc:
                    message = str(exc)
                    record_event(
                        "duplicates", "Duplicate verification could not be completed.",
                        level="error", detail=message,
                        context={"file_a_id": file_a_id, "file_b_id": file_b_id},
                        user_id=user_id,
                    )
                    with duplicate_verify_lock:
                        duplicate_verify_job.update({
                            "status": "error", "detail": message, "error": message,
                        })

        threading.Thread(
            target=run_verification,
            daemon=True,
            name="infomancer-duplicate-verification",
        ).start()
        return redirect(
            "/duplicates",
            "Verification started in the background. InfoMancer will read both files without changing them; progress is shown in the task panel.",
        )

    return router, {
        "bulk_duplicate_action": bulk_duplicate_action,
        "verify_duplicate": verify_duplicate,
    }

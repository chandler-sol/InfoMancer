from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse

from ..access import require_librarian
from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    db = ctx.live("db")
    job_registry = ctx.live("job_registry")
    record_event = ctx.live("record_event")
    redirect = ctx.live("redirect")
    store_movie_match = ctx.live("store_movie_match")
    store_tv_match = ctx.live("store_tv_match")
    apply_jobs = job_registry.mapping("bulk-match-apply")
    apply_jobs_lock = job_registry.lock("bulk-match-apply")

    def librarian_get(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.get(path, dependencies=dependencies, **kwargs)

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

    def clean_job_id(value: str) -> str:
        return "".join(
            character for character in str(value or "")[:100]
            if character.isalnum() or character in {"-", "_"}
        )[:80]

    def apply_job_key(request: Request, kind: str, job_id: str) -> str:
        return f"{request.state.user.id}:{kind}:{job_id}"

    def update_apply_job(
        request: Request, kind: str, job_id: str, **values,
    ) -> None:
        if not job_id:
            return
        key = apply_job_key(request, kind, job_id)
        with apply_jobs_lock:
            snapshot = dict(apply_jobs.get(key) or {})
            snapshot.update(values)
            snapshot["updated_at"] = time.monotonic()
            apply_jobs[key] = snapshot
            # Apply progress is process-local and intentionally short lived. Bound
            # stale snapshots so repeated QA/apply sessions cannot grow this mapping.
            if len(apply_jobs) > 64:
                oldest = sorted(
                    apply_jobs,
                    key=lambda candidate: float(
                        (apply_jobs.get(candidate) or {}).get("updated_at", 0)
                    ),
                )
                for stale_key in oldest[:-48]:
                    apply_jobs.pop(stale_key, None)

    def apply_progress(request: Request, kind: str, job_id: str):
        safe_job_id = clean_job_id(job_id)
        if not safe_job_id:
            return JSONResponse({
                "status": "pending", "processed": 0, "total": 0,
                "applied": 0, "failed": 0,
            })
        key = apply_job_key(request, kind, safe_job_id)
        with apply_jobs_lock:
            snapshot = dict(apply_jobs.get(key) or {})
        if not snapshot:
            snapshot = {
                "status": "pending", "processed": 0, "total": 0,
                "applied": 0, "failed": 0,
            }
        snapshot.pop("updated_at", None)
        return JSONResponse(snapshot)

    def apply_matches(
        request: Request,
        matches: list[str],
        *,
        kind: str,
        selected_scope: str,
        apply_job_id: str,
    ):
        applied = 0
        applied_items: list[dict[str, int]] = []
        failures: list[dict[str, object]] = []
        store = store_movie_match if kind == "movie" else store_tv_match
        suggestion_table = (
            "movie_match_suggestions" if kind == "movie" else "tv_match_suggestions"
        )
        item_label = "movie" if kind == "movie" else "TV series"
        safe_job_id = clean_job_id(apply_job_id)
        total = len(matches)
        update_apply_job(
            request, kind, safe_job_id,
            status="running", processed=0, total=total,
            applied=0, failed=0, current_title_id=None,
        )

        for index, value in enumerate(matches, start=1):
            title_id = provider_id = None
            try:
                title_id, provider_id = (
                    int(part) for part in value.split(":", 1)
                )
                update_apply_job(
                    request, kind, safe_job_id,
                    status="running", current_title_id=title_id,
                )
                store(title_id, provider_id)
                applied += 1
                applied_items.append({
                    "title_id": title_id,
                    "provider_id": provider_id,
                })
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}".strip()[:500]
                failures.append({
                    "title_id": title_id,
                    "provider_id": provider_id,
                    "detail": detail or type(exc).__name__,
                })
                record_event(
                    "metadata",
                    f"Bulk match could not apply one {item_label}.",
                    level="warning",
                    detail=detail,
                    context={
                        "operation": "bulk-match-apply",
                        "kind": kind,
                        "title_id": title_id,
                        "provider_id": provider_id,
                    },
                    user_id=request.state.user.id,
                )
            else:
                # Current store helpers already remove their saved suggestion. Keep
                # this cleanup as a best-effort compatibility guard for older helper
                # behavior, but never turn a saved match into a failed batch item.
                try:
                    with db.connect() as conn:
                        conn.execute(
                            f"DELETE FROM {suggestion_table} WHERE title_id=?",
                            (title_id,),
                        )
                except Exception as exc:
                    record_event(
                        "metadata",
                        f"Applied {item_label} match but could not clear its cached suggestion.",
                        level="warning",
                        detail=f"{type(exc).__name__}: {exc}"[:500],
                        context={
                            "operation": "bulk-match-suggestion-cleanup",
                            "kind": kind,
                            "title_id": title_id,
                        },
                        user_id=request.state.user.id,
                    )
            finally:
                update_apply_job(
                    request, kind, safe_job_id,
                    status="running", processed=index, total=total,
                    applied=applied, failed=len(failures),
                    current_title_id=title_id,
                )

        failed = len(failures)
        noun = "movies" if kind == "movie" else "TV series"
        message = f"Matched {applied} {noun}"
        if failed:
            message += f"; {failed} failed"
            message += f". First error: {failures[0]['detail']}"
        update_apply_job(
            request, kind, safe_job_id,
            status="complete", processed=total, total=total,
            applied=applied, failed=failed, current_title_id=None,
        )
        record_event(
            "metadata",
            f"Bulk match apply finished: {applied} applied, {failed} failed.",
            level="warning" if failed else "info",
            context={
                "operation": "bulk-match-apply",
                "kind": kind,
                "requested": len(matches),
                "applied": applied,
                "failed": failed,
            },
            user_id=request.state.user.id,
        )
        base = "/movies/bulk-match" if kind == "movie" else "/shows/bulk-match"
        destination = (
            f"{base}?review=true&selected=true"
            if selected_scope else f"{base}?review=true"
        )

        # Bulk Match's desktop/browser controller asks for a compact result so it can
        # remove only the successfully applied rows in place. Avoiding a complete
        # document navigation keeps the review queue, global shell, poster images,
        # and task controllers from being torn down and rebuilt after every apply.
        if request.headers.get("x-requested-with") == "InfoMancerAsync":
            return JSONResponse({
                "ok": failed == 0,
                "kind": kind,
                "requested": len(matches),
                "applied": applied,
                "failed": failed,
                "applied_title_ids": [item["title_id"] for item in applied_items],
                "failures": failures[:10],
                "message": message,
                "redirect_url": destination,
            })

        # Keep native/no-JavaScript form submission behavior intact.
        return redirect(destination, message)

    @librarian_get("/api/movies/bulk-match/apply-progress")
    def bulk_movie_match_apply_progress(request: Request, job_id: str = ""):
        return apply_progress(request, "movie", job_id)

    @librarian_get("/api/shows/bulk-match/apply-progress")
    def bulk_tv_match_apply_progress(request: Request, job_id: str = ""):
        return apply_progress(request, "tv", job_id)

    @librarian_post("/movies/bulk-match")
    def bulk_movie_match_apply(
        request: Request,
        matches: list[str] = Form(default=[]),
        selected_scope: str = Form(""),
        apply_job_id: str = Form(""),
    ):
        return apply_matches(
            request, matches, kind="movie", selected_scope=selected_scope,
            apply_job_id=apply_job_id,
        )

    @librarian_post("/shows/bulk-match")
    def bulk_tv_match_apply(
        request: Request,
        matches: list[str] = Form(default=[]),
        selected_scope: str = Form(""),
        apply_job_id: str = Form(""),
    ):
        return apply_matches(
            request, matches, kind="tv", selected_scope=selected_scope,
            apply_job_id=apply_job_id,
        )

    return router, {
        "bulk_movie_match_apply": bulk_movie_match_apply,
        "bulk_tv_match_apply": bulk_tv_match_apply,
        "bulk_movie_match_apply_progress": bulk_movie_match_apply_progress,
        "bulk_tv_match_apply_progress": bulk_tv_match_apply_progress,
    }

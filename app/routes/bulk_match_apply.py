from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse

from ..access import require_librarian
from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    db = ctx.live("db")
    record_event = ctx.live("record_event")
    redirect = ctx.live("redirect")
    store_movie_match = ctx.live("store_movie_match")
    store_tv_match = ctx.live("store_tv_match")

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

    def apply_matches(
        request: Request,
        matches: list[str],
        *,
        kind: str,
        selected_scope: str,
    ):
        applied = 0
        applied_items: list[dict[str, int]] = []
        failures: list[dict[str, object]] = []
        store = store_movie_match if kind == "movie" else store_tv_match
        suggestion_table = (
            "movie_match_suggestions" if kind == "movie" else "tv_match_suggestions"
        )
        item_label = "movie" if kind == "movie" else "TV series"

        for value in matches:
            title_id = provider_id = None
            try:
                title_id, provider_id = (
                    int(part) for part in value.split(":", 1)
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
                continue

            # Current store helpers already remove their saved suggestion. Keep this
            # cleanup as a best-effort compatibility guard for older helper behavior,
            # but never turn a successfully saved match into a failed batch item.
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

        failed = len(failures)
        noun = "movies" if kind == "movie" else "TV series"
        message = f"Matched {applied} {noun}"
        if failed:
            message += f"; {failed} failed"
            message += f". First error: {failures[0]['detail']}"
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

    @librarian_post("/movies/bulk-match")
    def bulk_movie_match_apply(
        request: Request,
        matches: list[str] = Form(default=[]),
        selected_scope: str = Form(""),
    ):
        return apply_matches(
            request, matches, kind="movie", selected_scope=selected_scope,
        )

    @librarian_post("/shows/bulk-match")
    def bulk_tv_match_apply(
        request: Request,
        matches: list[str] = Form(default=[]),
        selected_scope: str = Form(""),
    ):
        return apply_matches(
            request, matches, kind="tv", selected_scope=selected_scope,
        )

    return router, {
        "bulk_movie_match_apply": bulk_movie_match_apply,
        "bulk_tv_match_apply": bulk_tv_match_apply,
    }

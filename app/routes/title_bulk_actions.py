from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    db = ctx.live("db")
    record_event = ctx.live("record_event")

    @router.post("/titles/favorite-bulk")
    def favorite_titles_bulk(
        request: Request,
        selected: list[int] = Form(default=[]),
    ):
        user_id = int(getattr(request.state.user, "id", 0) or 0)
        if user_id <= 0:
            return JSONResponse(
                {"ok": False, "detail": "Favorites require a signed-in user account."},
                status_code=403,
            )

        requested = list(dict.fromkeys(title_id for title_id in selected if title_id > 0))[:1000]
        if len(requested) < 2:
            return JSONResponse(
                {"ok": False, "detail": "Select at least two titles to use the bulk favorite action."},
                status_code=400,
            )

        placeholders = ",".join("?" for _ in requested)
        with db.connect() as conn:
            valid_ids = {
                row["id"]
                for row in conn.execute(
                    f"SELECT id FROM titles WHERE id IN ({placeholders})",
                    requested,
                )
            }
            title_ids = [title_id for title_id in requested if title_id in valid_ids]
            if not title_ids:
                return JSONResponse(
                    {"ok": False, "detail": "None of the selected titles still exist."},
                    status_code=404,
                )

            conn.executemany(
                """INSERT INTO user_title_state(user_id,title_id,favorite,updated_at)
                   VALUES (?,?,1,CURRENT_TIMESTAMP)
                   ON CONFLICT(user_id,title_id) DO UPDATE SET
                     favorite=1,
                     updated_at=CURRENT_TIMESTAMP""",
                [(user_id, title_id) for title_id in title_ids],
            )

        record_event(
            "library",
            f"Added {len(title_ids)} selected titles to Favorites.",
            user_id=user_id,
            context={"titles": len(title_ids), "operation": "bulk_favorite"},
        )
        return JSONResponse({
            "ok": True,
            "title_ids": title_ids,
            "count": len(title_ids),
            "detail": f"Added {len(title_ids)} selected title{'s' if len(title_ids) != 1 else ''} to Favorites.",
        })

    return router, {"favorite_titles_bulk": favorite_titles_bulk}

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    db = ctx.live("db")
    record_event = ctx.live("record_event")

    def signed_in_user_id(request: Request) -> int:
        return int(getattr(request.state.user, "id", 0) or 0)

    def set_title_favorites(conn, user_id: int, title_ids: list[int], favorite: bool) -> None:
        conn.executemany(
            """INSERT INTO user_title_state(user_id,title_id,favorite,updated_at)
               VALUES (?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(user_id,title_id) DO UPDATE SET
                 favorite=excluded.favorite,
                 updated_at=CURRENT_TIMESTAMP""",
            [(user_id, title_id, 1 if favorite else 0) for title_id in title_ids],
        )

    @router.post("/api/titles/{title_id}/favorite")
    def toggle_title_favorite_api(request: Request, title_id: int):
        """Toggle one Library title without navigating away from the current view."""
        user_id = signed_in_user_id(request)
        if user_id <= 0:
            return JSONResponse(
                {"ok": False, "detail": "Favorites require a signed-in user account."},
                status_code=403,
            )

        with db.connect() as conn:
            title = conn.execute(
                """SELECT id,COALESCE(NULLIF(metadata_title,''),title) name
                   FROM titles WHERE id=?""",
                (title_id,),
            ).fetchone()
            if not title:
                return JSONResponse(
                    {"ok": False, "detail": "That title no longer exists."},
                    status_code=404,
                )
            current = conn.execute(
                "SELECT favorite FROM user_title_state WHERE user_id=? AND title_id=?",
                (user_id, title_id),
            ).fetchone()
            favorite = not bool(current and current["favorite"])
            set_title_favorites(conn, user_id, [title_id], favorite)

        action = "added to" if favorite else "removed from"
        record_event(
            "library",
            "Title added to favorites." if favorite else "Title removed from favorites.",
            user_id=user_id,
            context={"title_id": title_id, "operation": "favorite_toggle_async"},
        )
        return JSONResponse({
            "ok": True,
            "title_id": title_id,
            "favorite": favorite,
            "detail": f'"{title["name"]}" has been {action} Favorites.',
        })

    @router.post("/titles/favorite-bulk")
    def favorite_titles_bulk(
        request: Request,
        selected: list[int] = Form(default=[]),
        favorite: str = Form("toggle"),
    ):
        user_id = signed_in_user_id(request)
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

            requested_state = str(favorite).strip().casefold()
            if requested_state in {"1", "true", "on", "yes"}:
                should_favorite = True
            elif requested_state in {"0", "false", "off", "no"}:
                should_favorite = False
            else:
                current_favorites = {
                    row["title_id"]
                    for row in conn.execute(
                        f"""SELECT title_id FROM user_title_state
                            WHERE user_id=? AND favorite=1
                              AND title_id IN ({','.join('?' for _ in title_ids)})""",
                        (user_id, *title_ids),
                    ).fetchall()
                }
                # Mixed or entirely unfavorited selections become favorites. If the
                # whole working set is already favorited, the same command removes it.
                should_favorite = not all(title_id in current_favorites for title_id in title_ids)

            set_title_favorites(conn, user_id, title_ids, should_favorite)

        action = "Added" if should_favorite else "Removed"
        destination = "to" if should_favorite else "from"
        record_event(
            "library",
            f"{action} {len(title_ids)} selected titles {destination} Favorites.",
            user_id=user_id,
            context={
                "titles": len(title_ids),
                "operation": "bulk_favorite" if should_favorite else "bulk_unfavorite",
                "favorite": should_favorite,
            },
        )
        return JSONResponse({
            "ok": True,
            "title_ids": title_ids,
            "count": len(title_ids),
            "favorite": should_favorite,
            "detail": (
                f"{action} {len(title_ids)} selected title"
                f"{'s' if len(title_ids) != 1 else ''} {destination} Favorites."
            ),
        })

    return router, {
        "toggle_title_favorite_api": toggle_title_favorite_api,
        "favorite_titles_bulk": favorite_titles_bulk,
    }

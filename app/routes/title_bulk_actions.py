from __future__ import annotations

import re
from difflib import SequenceMatcher

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..access import require_librarian
from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    db = ctx.live("db")
    record_event = ctx.live("record_event")
    redirect = ctx.live("redirect")
    templates = ctx.live("templates")

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

    @router.get("/api/collections/{collection_id}/search")
    def search_collection_titles(request: Request, collection_id: int, q: str = ""):
        """Search local titles for a manual collection without punctuation sensitivity."""
        if not getattr(request.state.user, "is_librarian", False):
            return JSONResponse(
                {"ok": False, "detail": "Librarian access is required to add collection items."},
                status_code=403,
            )

        query = " ".join(q.strip().split())[:120]
        if not query:
            return JSONResponse({"ok": True, "results": []})

        def normalized(value: str) -> str:
            return re.sub(r"[^a-z0-9]+", "", value.casefold())

        normalized_query = normalized(query)
        query_words = re.findall(r"[a-z0-9]+", query.casefold())
        with db.connect() as conn:
            collection = conn.execute(
                "SELECT id,collection_type FROM collections WHERE id=?",
                (collection_id,),
            ).fetchone()
            if not collection:
                return JSONResponse(
                    {"ok": False, "detail": "That collection no longer exists."},
                    status_code=404,
                )
            if collection["collection_type"] != "manual":
                return JSONResponse(
                    {"ok": False, "detail": "Smart Collections manage their contents automatically."},
                    status_code=400,
                )
            rows = conn.execute(
                """SELECT t.id,t.kind,t.poster_url,t.title,t.metadata_title,
                          COALESCE(NULLIF(t.metadata_title,''),t.title) display_title,
                          COALESCE(t.metadata_year,t.year) display_year
                   FROM titles t
                   WHERE NOT EXISTS (
                     SELECT 1 FROM collection_titles ct
                     WHERE ct.collection_id=? AND ct.title_id=t.id
                   )
                   ORDER BY display_title COLLATE NOCASE""",
                (collection_id,),
            ).fetchall()

        ranked: list[tuple[tuple[float, str], dict]] = []
        for row in rows:
            candidate = dict(row)
            names = [candidate.get("display_title") or "", candidate.get("title") or ""]
            best_rank: float | None = None
            for name in names:
                normalized_title = normalized(name)
                title_words = re.findall(r"[a-z0-9]+", name.casefold())
                if not normalized_title:
                    continue
                if normalized_title == normalized_query:
                    rank = 0.0
                elif normalized_title.startswith(normalized_query):
                    rank = 1.0
                elif normalized_query and normalized_query in normalized_title:
                    rank = 2.0
                elif query_words and all(
                    any(query_word in title_word for title_word in title_words)
                    for query_word in query_words
                ):
                    rank = 3.0
                else:
                    similarity = SequenceMatcher(
                        None, normalized_query, normalized_title
                    ).ratio() if normalized_query else 0.0
                    if similarity < 0.72:
                        continue
                    rank = 4.0 + (1.0 - similarity)
                best_rank = rank if best_rank is None else min(best_rank, rank)
            if best_rank is None:
                continue
            ranked.append((
                (best_rank, str(candidate["display_title"]).casefold()),
                {
                    "id": candidate["id"],
                    "kind": candidate["kind"],
                    "poster_url": candidate["poster_url"] or "",
                    "display_title": candidate["display_title"],
                    "display_year": candidate["display_year"],
                },
            ))

        ranked.sort(key=lambda item: item[0])
        return JSONResponse({
            "ok": True,
            "query": query,
            "results": [item for _rank, item in ranked[:20]],
        })

    @router.post(
        "/collections/{collection_id}/reorder",
        dependencies=[Depends(require_librarian)],
    )
    def reorder_collection_items(
        request: Request,
        collection_id: int,
        order: list[str] = Form(default=[]),
    ):
        """Persist the exact visible order of a manual mixed-content collection."""
        if not getattr(request.state.user, "is_librarian", False):
            return JSONResponse(
                {"ok": False, "detail": "Librarian access is required to reorder collections."},
                status_code=403,
            )

        requested: list[tuple[str, int]] = []
        seen: set[tuple[str, int]] = set()
        for token in order[:2000]:
            try:
                item_type, raw_id = token.split(":", 1)
                item_id = int(raw_id)
            except (TypeError, ValueError):
                return JSONResponse(
                    {"ok": False, "detail": "The collection order contained an invalid item."},
                    status_code=400,
                )
            if item_type not in {"title", "episode"} or item_id <= 0:
                return JSONResponse(
                    {"ok": False, "detail": "The collection order contained an invalid item."},
                    status_code=400,
                )
            key = (item_type, item_id)
            if key in seen:
                return JSONResponse(
                    {"ok": False, "detail": "The collection order contained the same item twice."},
                    status_code=400,
                )
            seen.add(key)
            requested.append(key)

        collection_name = ""
        with db.connect() as conn:
            collection = conn.execute(
                "SELECT name,collection_type FROM collections WHERE id=?",
                (collection_id,),
            ).fetchone()
            if not collection:
                return JSONResponse(
                    {"ok": False, "detail": "That collection no longer exists."},
                    status_code=404,
                )
            if collection["collection_type"] != "manual":
                return JSONResponse(
                    {"ok": False, "detail": "Smart Collections manage their order automatically."},
                    status_code=400,
                )
            collection_name = collection["name"]
            current = {
                ("title", int(row["title_id"]))
                for row in conn.execute(
                    "SELECT title_id FROM collection_titles WHERE collection_id=?",
                    (collection_id,),
                ).fetchall()
            }
            current.update({
                ("episode", int(row["expected_episode_id"]))
                for row in conn.execute(
                    "SELECT expected_episode_id FROM collection_episodes WHERE collection_id=?",
                    (collection_id,),
                ).fetchall()
            })
            if set(requested) != current or len(requested) != len(current):
                return JSONResponse(
                    {
                        "ok": False,
                        "detail": (
                            "The collection changed while you were reordering it. "
                            "Reload the page and try again."
                        ),
                    },
                    status_code=409,
                )
            for position, (item_type, item_id) in enumerate(requested):
                if item_type == "title":
                    conn.execute(
                        """UPDATE collection_titles SET position=?
                           WHERE collection_id=? AND title_id=?""",
                        (position, collection_id, item_id),
                    )
                else:
                    conn.execute(
                        """UPDATE collection_episodes SET position=?
                           WHERE collection_id=? AND expected_episode_id=?""",
                        (position, collection_id, item_id),
                    )
            conn.execute(
                "UPDATE collections SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (collection_id,),
            )

        record_event(
            "library",
            f'Collection order updated for "{collection_name}".',
            user_id=signed_in_user_id(request),
            context={
                "collection_id": collection_id,
                "items": len(requested),
                "operation": "collection_reorder",
            },
        )
        return JSONResponse({
            "ok": True,
            "count": len(requested),
            "detail": f"Saved the order of {len(requested)} collection item{'s' if len(requested) != 1 else ''}.",
        })

    @router.post("/titles/organize-bulk", response_class=HTMLResponse)
    def organize_titles_bulk_action(
        request: Request,
        selected: list[int] = Form(default=[]),
        apply: str = Form(""),
        selected_tags: list[int] = Form(default=[]),
        tag_names: str = Form(""),
        selected_collections: list[int] = Form(default=[]),
    ):
        """Render or apply the Library multi-title organization workflow.

        This bulk-action router is registered before the legacy title route, so it
        owns this endpoint. Keeping the event-log write outside the catalog write
        transaction is intentional: SQLite cannot service a second writer while the
        first connection is still holding the transaction open.
        """
        title_ids = list(dict.fromkeys(title_id for title_id in selected if title_id > 0))[:1000]
        if not title_ids:
            return redirect(
                "/library",
                "Select at least one movie or TV series before organizing tags.",
            )

        user_id = signed_in_user_id(request)
        tag_count = 0
        collection_count = 0
        with db.connect() as conn:
            placeholders = ",".join("?" for _ in title_ids)
            titles = conn.execute(
                f"""SELECT id,kind,COALESCE(metadata_title,title) display_title
                    FROM titles WHERE id IN ({placeholders})
                    ORDER BY display_title COLLATE NOCASE""",
                title_ids,
            ).fetchall()
            valid_ids = {row["id"] for row in titles}
            title_ids = [title_id for title_id in title_ids if title_id in valid_ids]
            if not title_ids:
                return redirect("/library", "The selected titles are no longer in the Library.")

            tags = conn.execute(
                """SELECT ut.*,COUNT(tt.title_id) usage_count
                   FROM user_tags ut LEFT JOIN title_tags tt ON tt.tag_id=ut.id
                   WHERE ut.user_id=? GROUP BY ut.id ORDER BY ut.name COLLATE NOCASE""",
                (user_id,),
            ).fetchall()
            collections = conn.execute(
                """SELECT c.*,COUNT(ct.title_id) title_count
                   FROM collections c LEFT JOIN collection_titles ct
                     ON ct.collection_id=c.id
                   WHERE c.collection_type='manual' GROUP BY c.id ORDER BY c.name COLLATE NOCASE"""
            ).fetchall()

            if apply != "1":
                return templates.TemplateResponse(request, "organize_bulk.html", {
                    "titles": titles,
                    "title_ids": title_ids,
                    "tags": tags,
                    "collections": collections,
                    "message": "",
                })

            allowed_tags = {row["id"] for row in tags}
            tag_ids = {tag_id for tag_id in selected_tags if tag_id in allowed_tags}
            new_names: list[str] = []
            for raw_name in tag_names.split(","):
                name = " ".join(raw_name.strip().split())[:40]
                if name and name.casefold() not in {item.casefold() for item in new_names}:
                    new_names.append(name)
            if len(new_names) > 20:
                return redirect(
                    "/library",
                    "Tags were not changed. Add no more than 20 new tags at one time.",
                )

            for name in new_names:
                conn.execute(
                    """INSERT INTO user_tags(user_id,name) VALUES (?,?)
                       ON CONFLICT(user_id,name) DO NOTHING""",
                    (user_id, name),
                )
                row = conn.execute(
                    "SELECT id FROM user_tags WHERE user_id=? AND name=? COLLATE NOCASE",
                    (user_id, name),
                ).fetchone()
                if row:
                    tag_ids.add(row["id"])

            conn.executemany(
                "INSERT OR IGNORE INTO title_tags(title_id,tag_id) VALUES (?,?)",
                [(title_id, tag_id) for title_id in title_ids for tag_id in tag_ids],
            )
            tag_count = len(tag_ids)

            collection_ids: set[int] = set()
            if request.state.user.is_librarian:
                allowed_collections = {row["id"] for row in collections}
                collection_ids = {
                    collection_id for collection_id in selected_collections
                    if collection_id in allowed_collections
                }
                for collection_id in collection_ids:
                    next_position = conn.execute(
                        """SELECT COALESCE(MAX(position),-1)+1 next_position
                           FROM collection_titles WHERE collection_id=?""",
                        (collection_id,),
                    ).fetchone()["next_position"]
                    for offset, title_id in enumerate(title_ids):
                        conn.execute(
                            """INSERT OR IGNORE INTO collection_titles
                               (collection_id,title_id,position) VALUES (?,?,?)""",
                            (collection_id, title_id, next_position + offset),
                        )
            collection_count = len(collection_ids)

        # Do not log until the catalog transaction above has committed. EventLog
        # writes through its own SQLite connection and would otherwise wait on the
        # transaction that is waiting for it, leaving the dialog spinning.
        record_event(
            "library",
            f"Organization saved for {len(title_ids)} selected titles.",
            user_id=user_id,
            context={
                "titles": len(title_ids),
                "tags": tag_count,
                "collections": collection_count,
                "operation": "bulk_organize",
            },
        )
        return redirect(
            "/library",
            f"Organization saved for {len(title_ids)} selected "
            f"title{'s' if len(title_ids) != 1 else ''}.",
        )

    return router, {
        "toggle_title_favorite_api": toggle_title_favorite_api,
        "favorite_titles_bulk": favorite_titles_bulk,
        "search_collection_titles": search_collection_titles,
        "reorder_collection_items": reorder_collection_items,
        "organize_titles_bulk_action": organize_titles_bulk_action,
    }

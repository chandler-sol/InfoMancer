from fastapi import APIRouter, Depends

from ..access import require_librarian
from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    COLLECTION_ART_DIR = ctx.live("COLLECTION_ART_DIR")
    File = ctx.get("File")
    FileResponse = ctx.get("FileResponse")
    Form = ctx.get("Form")
    HTMLResponse = ctx.get("HTMLResponse")
    HTTPException = ctx.get("HTTPException")
    MIE_CATEGORIES = ctx.get("MIE_CATEGORIES")
    Request = ctx.get("Request")
    UploadFile = ctx.get("UploadFile")
    collection_artwork_url = ctx.live("collection_artwork_url")
    collection_items = ctx.live("collection_items")
    db = ctx.live("db")
    decode_filters = ctx.live("decode_filters")
    encode_filters = ctx.live("encode_filters")
    matching_titles = ctx.live("matching_titles")
    move_collection_item = ctx.live("move_collection_item")
    next_collection_position = ctx.live("next_collection_position")
    normalize_collection_positions = ctx.live("normalize_collection_positions")
    normalize_filters = ctx.live("normalize_filters")
    re = ctx.live("re")
    record_event = ctx.live("record_event")
    redirect = ctx.live("redirect")
    safe_next = ctx.live("safe_next")
    save_collection_artwork = ctx.live("save_collection_artwork")
    smart_filter_form = ctx.live("smart_filter_form")
    sqlite3 = ctx.live("sqlite3")
    templates = ctx.live("templates")
    title_return_path = ctx.live("title_return_path")

    def librarian_get(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.get(path, dependencies=dependencies, **kwargs)

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

    @librarian_get("/titles/{title_id}/collections", response_class=HTMLResponse)
    def title_collections_page(request: Request, title_id: int):
        with db.connect() as conn:
            title = conn.execute(
                """SELECT id,kind,COALESCE(NULLIF(metadata_title,''),title) display_title
                   FROM titles WHERE id=?""",
                (title_id,),
            ).fetchone()
            if not title:
                raise HTTPException(404, "Title not found")
            collections = conn.execute(
                """SELECT c.id,c.name,
                          EXISTS(SELECT 1 FROM collection_titles ct
                                 WHERE ct.collection_id=c.id AND ct.title_id=?) selected
                   FROM collections c WHERE c.collection_type='manual' ORDER BY c.name COLLATE NOCASE""",
                (title_id,),
            ).fetchall()
        return templates.TemplateResponse(request, "title_collections.html", {
            "title": title, "collections": collections,
            "return_to": request.query_params.get("return_to", f"/titles/{title_id}"),
            "message": request.query_params.get("message", ""),
        })

    @librarian_post("/titles/{title_id}/collections")
    def save_title_collections(
        request: Request, title_id: int,
        selected_collections: list[int] = Form([]), return_to: str = Form(""),
    ):
        selected = set(selected_collections)
        with db.connect() as conn:
            title = conn.execute(
                "SELECT COALESCE(NULLIF(metadata_title,''),title) name FROM titles WHERE id=?",
                (title_id,),
            ).fetchone()
            if not title:
                return redirect("/collections", "That library title no longer exists.")
            valid = {
                row["id"] for row in conn.execute("SELECT id FROM collections WHERE collection_type='manual'").fetchall()
            }
            selected &= valid
            previous = {
                row["collection_id"] for row in conn.execute(
                    """SELECT ct.collection_id FROM collection_titles ct JOIN collections c ON c.id=ct.collection_id
                       WHERE ct.title_id=? AND c.collection_type='manual'""",
                    (title_id,),
                ).fetchall()
            }
            for collection_id in previous - selected:
                conn.execute(
                    "DELETE FROM collection_titles WHERE collection_id=? AND title_id=?",
                    (collection_id, title_id),
                )
                normalize_collection_positions(conn, collection_id)
            for collection_id in selected - previous:
                conn.execute(
                    """INSERT INTO collection_titles(collection_id,title_id,position)
                       VALUES (?,?,?)""",
                    (collection_id, title_id, next_collection_position(conn, collection_id)),
                )
        return redirect(
            title_return_path(title_id, return_to),
            f'Collections for "{title["name"]}" updated.',
        )

    @librarian_get("/files/{file_id}/collections", response_class=HTMLResponse)
    def episode_collections_page(request: Request, file_id: int):
        with db.connect() as conn:
            file_row = conn.execute(
                """SELECT f.id,f.title_id,f.season,f.episode_start,f.episode_end,
                          COALESCE(NULLIF(t.metadata_title,''),t.title) show_name
                   FROM files f JOIN titles t ON t.id=f.title_id
                   WHERE f.id=? AND t.kind='tv'""",
                (file_id,),
            ).fetchone()
            if not file_row:
                raise HTTPException(404, "TV episode file not found")
            final_episode = file_row["episode_end"] or file_row["episode_start"]
            episodes = conn.execute(
                """SELECT e.id,e.season,e.episode,e.name
                   FROM expected_episodes e
                   WHERE e.title_id=? AND e.season=?
                     AND e.episode BETWEEN ? AND ?
                   ORDER BY e.episode""",
                (
                    file_row["title_id"], file_row["season"],
                    file_row["episode_start"], final_episode,
                ),
            ).fetchall()
            collections = conn.execute(
                "SELECT id,name FROM collections WHERE collection_type='manual' ORDER BY name COLLATE NOCASE"
            ).fetchall()
            memberships = {
                (row["expected_episode_id"], row["collection_id"])
                for row in conn.execute(
                    """SELECT ce.expected_episode_id,ce.collection_id
                       FROM collection_episodes ce
                       JOIN expected_episodes e ON e.id=ce.expected_episode_id
                       WHERE e.title_id=? AND e.season=?
                         AND e.episode BETWEEN ? AND ?""",
                    (
                        file_row["title_id"], file_row["season"],
                        file_row["episode_start"], final_episode,
                    ),
                ).fetchall()
            }
        return templates.TemplateResponse(request, "episode_collections.html", {
            "file": file_row, "episodes": episodes, "collections": collections,
            "memberships": memberships, "message": request.query_params.get("message", ""),
        })

    @librarian_post("/files/{file_id}/collections")
    def save_episode_collections(
        request: Request, file_id: int, assignments: list[str] = Form([]),
    ):
        requested = set()
        for assignment in assignments:
            try:
                episode_id, collection_id = (int(value) for value in assignment.split(":", 1))
                requested.add((episode_id, collection_id))
            except (TypeError, ValueError):
                continue
        with db.connect() as conn:
            file_row = conn.execute(
                """SELECT f.title_id,f.season,f.episode_start,f.episode_end
                   FROM files f JOIN titles t ON t.id=f.title_id
                   WHERE f.id=? AND t.kind='tv'""",
                (file_id,),
            ).fetchone()
            if not file_row:
                return redirect("/shows", "That TV episode file no longer exists.")
            final_episode = file_row["episode_end"] or file_row["episode_start"]
            episode_ids = {
                row["id"] for row in conn.execute(
                    """SELECT id FROM expected_episodes
                       WHERE title_id=? AND season=? AND episode BETWEEN ? AND ?""",
                    (
                        file_row["title_id"], file_row["season"],
                        file_row["episode_start"], final_episode,
                    ),
                ).fetchall()
            }
            collection_ids = {
                row["id"] for row in conn.execute("SELECT id FROM collections").fetchall()
            }
            requested = {
                pair for pair in requested
                if pair[0] in episode_ids and pair[1] in collection_ids
            }
            affected_collections = {
                row["collection_id"] for row in conn.execute(
                    f"""SELECT DISTINCT collection_id FROM collection_episodes
                        WHERE expected_episode_id IN ({','.join('?' for _ in episode_ids)})""",
                    tuple(episode_ids),
                ).fetchall()
            } if episode_ids else set()
            if episode_ids:
                conn.execute(
                    f"""DELETE FROM collection_episodes
                        WHERE expected_episode_id IN ({','.join('?' for _ in episode_ids)})""",
                    tuple(episode_ids),
                )
            for episode_id, collection_id in sorted(requested):
                conn.execute(
                    """INSERT INTO collection_episodes
                       (collection_id,expected_episode_id,position) VALUES (?,?,?)""",
                    (
                        collection_id, episode_id,
                        next_collection_position(conn, collection_id),
                    ),
                )
                affected_collections.add(collection_id)
            for collection_id in affected_collections:
                normalize_collection_positions(conn, collection_id)
        return redirect(
            f"/titles/{file_row['title_id']}",
            "Episode collection selections updated. No media files were changed.",
        )

    @router.get("/collections", response_class=HTMLResponse)
    def collections_page(request: Request):
        with db.connect() as conn:
            rows = conn.execute(
                """SELECT c.*,
                          ((SELECT COUNT(*) FROM collection_titles ct
                            WHERE ct.collection_id=c.id) +
                           (SELECT COUNT(*) FROM collection_episodes ce
                            WHERE ce.collection_id=c.id)) title_count,
                          COALESCE(
                            (SELECT t.poster_url FROM collection_titles ct
                             JOIN titles t ON t.id=ct.title_id
                             WHERE ct.collection_id=c.id ORDER BY ct.position LIMIT 1),
                            (SELECT t.poster_url FROM collection_episodes ce
                             JOIN expected_episodes e ON e.id=ce.expected_episode_id
                             JOIN titles t ON t.id=e.title_id
                             WHERE ce.collection_id=c.id ORDER BY ce.position LIMIT 1)
                          ) fallback_poster
                   FROM collections c ORDER BY c.name COLLATE NOCASE"""
            ).fetchall()
            roots = conn.execute("SELECT id,label,path FROM roots ORDER BY label,path").fetchall()
            genres = sorted({genre.strip() for row in conn.execute("SELECT genres FROM titles WHERE genres IS NOT NULL") for genre in (row["genres"] or "").split(",") if genre.strip()}, key=str.casefold)
            collections = []
            for row in rows:
                item = dict(row)
                if item.get("collection_type") == "smart":
                    matches = matching_titles(conn, decode_filters(item["filter_json"]), request.state.user.id)
                    item["title_count"] = len(matches)
                    item["fallback_poster"] = next((title.get("poster_url") for title in matches if title.get("poster_url")), None)
                item["artwork_url"] = collection_artwork_url(item)
                collections.append(item)
        return templates.TemplateResponse(request, "collections.html", {
            "collections": collections,
            "roots": roots, "genres": genres, "health_categories": sorted(MIE_CATEGORIES),
            "message": request.query_params.get("message", ""),
        })

    @librarian_post("/collections/smart/preview", response_class=HTMLResponse)
    async def preview_smart_collection(request: Request):
        form = await request.form()
        try:
            filters = smart_filter_form(form)
        except ValueError as exc:
            return redirect("/collections", f"Smart Collection preview could not be created. {exc}")
        with db.connect() as conn:
            titles = matching_titles(conn, filters, request.state.user.id)[:100]
        return templates.TemplateResponse(request, "smart_collection_preview.html", {
            "filters": filters, "filter_json": encode_filters(filters), "titles": titles,
            "name": " ".join(str(form.get("name") or "").split())[:80],
            "description": str(form.get("description") or "").strip()[:1000], "message": "",
        })

    @librarian_post("/collections/smart")
    async def create_smart_collection(request: Request):
        form = await request.form()
        name = str(form.get("name") or "")
        description = str(form.get("description") or "")
        cleaned = " ".join(name.split())[:80]
        try:
            encoded = str(form.get("filter_json") or "")
            filters = normalize_filters(decode_filters(encoded)) if encoded else smart_filter_form(form)
            if not cleaned:
                raise ValueError("Enter a collection name.")
            with db.connect() as conn:
                collection_id = conn.execute(
                    """INSERT INTO collections(name,description,created_by,collection_type,filter_json)
                       VALUES (?,?,?,'smart',?)""",
                    (cleaned, description.strip()[:1000], request.state.user.id if request.state.user.id > 0 else None, encode_filters(filters)),
                ).lastrowid
        except (ValueError, sqlite3.IntegrityError) as exc:
            return redirect("/collections", f"Smart Collection was not saved. {exc}")
        record_event("library", f'Smart Collection "{cleaned}" created.', user_id=request.state.user.id, context={"collection_id": collection_id})
        return redirect(f"/collections/{collection_id}", f'Smart Collection "{cleaned}" saved and will update automatically.')

    @router.get("/libraries", response_class=HTMLResponse)
    def custom_libraries_page(request: Request):
        with db.connect() as conn:
            libraries = conn.execute(
                """SELECT l.*,COUNT(lt.title_id) title_count,
                          (SELECT t.poster_url FROM custom_library_titles first_lt
                           JOIN titles t ON t.id=first_lt.title_id
                           WHERE first_lt.library_id=l.id ORDER BY first_lt.added_at LIMIT 1) poster_url
                   FROM custom_libraries l LEFT JOIN custom_library_titles lt ON lt.library_id=l.id
                   GROUP BY l.id ORDER BY l.name COLLATE NOCASE"""
            ).fetchall()
        return templates.TemplateResponse(request, "custom_libraries.html", {
            "libraries": libraries, "message": request.query_params.get("message", ""),
        })

    @librarian_post("/libraries")
    def create_custom_library(
        request: Request, name: str = Form(...), library_kind: str = Form("mixed"),
        description: str = Form(""), return_to: str = Form("/libraries"),
    ):
        cleaned = " ".join(name.split())[:80]
        kind = library_kind if library_kind in {"movie", "tv", "mixed"} else "mixed"
        if not cleaned:
            return redirect(safe_next(return_to), "Enter a library name. Nothing was created.")
        try:
            with db.connect() as conn:
                library_id = conn.execute(
                    "INSERT INTO custom_libraries(name,library_kind,description,created_by) VALUES (?,?,?,?)",
                    (cleaned, kind, description.strip()[:1000], request.state.user.id if request.state.user.id > 0 else None),
                ).lastrowid
        except sqlite3.IntegrityError:
            return redirect(safe_next(return_to), f'A library named "{cleaned}" already exists.')
        record_event("library", f'Custom library "{cleaned}" created.', user_id=request.state.user.id, context={"library_id": library_id})
        return redirect(safe_next(return_to), f'Library "{cleaned}" created.')

    @router.get("/libraries/{library_id}", response_class=HTMLResponse)
    def custom_library_detail(request: Request, library_id: int):
        with db.connect() as conn:
            library_row = conn.execute("SELECT * FROM custom_libraries WHERE id=?", (library_id,)).fetchone()
            if not library_row:
                raise HTTPException(404, "Library not found")
            titles = conn.execute(
                """SELECT t.*,COALESCE(NULLIF(t.metadata_title,''),t.title) display_title,
                          COALESCE(t.metadata_year,t.year) display_year,COALESCE(uts.favorite,0) favorite
                   FROM custom_library_titles lt JOIN titles t ON t.id=lt.title_id
                   LEFT JOIN user_title_state uts ON uts.title_id=t.id AND uts.user_id=?
                   WHERE lt.library_id=? ORDER BY display_title COLLATE NOCASE""",
                (request.state.user.id, library_id),
            ).fetchall()
        return templates.TemplateResponse(request, "custom_library_detail.html", {
            "library": library_row, "titles": titles,
            "message": request.query_params.get("message", ""),
        })

    @router.get("/titles/{title_id}/libraries", response_class=HTMLResponse)
    def title_libraries_page(request: Request, title_id: int):
        with db.connect() as conn:
            title = conn.execute("SELECT id,kind,COALESCE(NULLIF(metadata_title,''),title) display_title FROM titles WHERE id=?", (title_id,)).fetchone()
            if not title:
                raise HTTPException(404, "Title not found")
            libraries = conn.execute(
                """SELECT l.*,EXISTS(SELECT 1 FROM custom_library_titles lt
                       WHERE lt.library_id=l.id AND lt.title_id=?) selected
                   FROM custom_libraries l WHERE l.library_kind IN ('mixed',?)
                   ORDER BY l.name COLLATE NOCASE""", (title_id, title["kind"]),
            ).fetchall()
        return templates.TemplateResponse(request, "title_libraries.html", {
            "title": title, "libraries": libraries,
            "return_to": request.query_params.get("return_to", f"/titles/{title_id}"),
            "message": request.query_params.get("message", ""),
        })

    @librarian_post("/titles/{title_id}/libraries")
    def save_title_libraries(
        request: Request, title_id: int, selected: list[int] = Form(default=[]),
        new_library_name: str = Form(""), return_to: str = Form(""),
    ):
        with db.connect() as conn:
            title = conn.execute("SELECT id,kind,COALESCE(NULLIF(metadata_title,''),title) display_title FROM titles WHERE id=?", (title_id,)).fetchone()
            if not title:
                return redirect("/library", "That title no longer exists. Nothing changed.")
            new_name = " ".join(new_library_name.split())[:80]
            if new_name:
                conn.execute("INSERT OR IGNORE INTO custom_libraries(name,library_kind,created_by) VALUES (?,'mixed',?)", (new_name, request.state.user.id if request.state.user.id > 0 else None))
                created = conn.execute("SELECT id FROM custom_libraries WHERE name=? COLLATE NOCASE", (new_name,)).fetchone()
                if created:
                    selected.append(created["id"])
            allowed = {row["id"] for row in conn.execute("SELECT id FROM custom_libraries WHERE library_kind IN ('mixed',?)", (title["kind"],))}
            chosen = allowed.intersection(selected)
            conn.execute("DELETE FROM custom_library_titles WHERE title_id=?", (title_id,))
            conn.executemany("INSERT INTO custom_library_titles(library_id,title_id) VALUES (?,?)", [(library_id,title_id) for library_id in chosen])
        message = f'Updated libraries for "{title["display_title"]}". No media files were copied or moved.'
        record_event("library", message, user_id=request.state.user.id, context={"title_id": title_id})
        return redirect(safe_next(return_to or f"/titles/{title_id}"), message)

    @librarian_post("/collections")
    def create_collection(
        request: Request, name: str = Form(...), description: str = Form(""),
    ):
        cleaned = " ".join(name.strip().split())[:80]
        if not cleaned:
            return redirect(
                "/collections",
                "The collection was not created. Enter a name and try again.",
            )
        try:
            with db.connect() as conn:
                cursor = conn.execute(
                    """INSERT INTO collections(name,description,created_by)
                       VALUES (?,?,?)""",
                    (
                        cleaned, description.strip()[:1000],
                        request.state.user.id if request.state.user.id > 0 else None,
                    ),
                )
                collection_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            return redirect(
                "/collections",
                f'A collection named "{cleaned}" already exists. Open it or choose a different name.',
            )
        record_event(
            "library", f'Collection "{cleaned}" created.',
            user_id=request.state.user.id, context={"collection_id": collection_id},
        )
        return redirect(
            f"/collections/{collection_id}",
            f'Collection "{cleaned}" created. Add movies or TV series when you are ready.',
        )

    @router.get("/collections/art/{filename}")
    def collection_artwork(filename: str):
        if not re.fullmatch(r"[0-9a-f]{40}\.(?:jpg|png|webp)", filename):
            raise HTTPException(404, "Collection image not found")
        path = COLLECTION_ART_DIR / filename
        if not path.is_file():
            raise HTTPException(404, "Collection image not found")
        return FileResponse(path)

    @router.get("/collections/{collection_id}", response_class=HTMLResponse)
    def collection_detail(request: Request, collection_id: int, q: str = ""):
        with db.connect() as conn:
            collection = conn.execute(
                """SELECT c.*,COALESCE(
                          (SELECT t.poster_url FROM collection_titles first_ct
                           JOIN titles t ON t.id=first_ct.title_id
                           WHERE first_ct.collection_id=c.id
                           ORDER BY first_ct.position,first_ct.title_id LIMIT 1),
                          (SELECT t.poster_url FROM collection_episodes first_ce
                           JOIN expected_episodes e ON e.id=first_ce.expected_episode_id
                           JOIN titles t ON t.id=e.title_id
                           WHERE first_ce.collection_id=c.id
                           ORDER BY first_ce.position,first_ce.expected_episode_id LIMIT 1)
                         ) fallback_poster
                   FROM collections c WHERE c.id=?""",
                (collection_id,),
            ).fetchone()
            if not collection:
                raise HTTPException(404, "Collection not found")
            if collection["collection_type"] == "smart":
                smart_titles = matching_titles(conn, decode_filters(collection["filter_json"]), request.state.user.id)
                items = [{**title, "item_type": "title", "item_id": title["id"], "title_id": title["id"], "item_label": "TV series" if title["kind"] == "tv" else "Movie"} for title in smart_titles]
            else:
                items = collection_items(conn, collection_id, request.state.user.id)
            candidates = []
            if collection["collection_type"] == "manual" and q.strip():
                term = f"%{q.strip()}%"
                candidates = conn.execute(
                    """SELECT t.id,t.kind,t.poster_url,
                              COALESCE(NULLIF(t.metadata_title,''),t.title) display_title,
                              COALESCE(t.metadata_year,t.year) display_year
                       FROM titles t
                       WHERE (t.title LIKE ? OR t.metadata_title LIKE ?)
                         AND NOT EXISTS (
                           SELECT 1 FROM collection_titles ct
                           WHERE ct.collection_id=? AND ct.title_id=t.id
                         )
                       ORDER BY display_title COLLATE NOCASE LIMIT 20""",
                    (term, term, collection_id),
                ).fetchall()
        return templates.TemplateResponse(request, "collection_detail.html", {
            "collection": {
                **dict(collection),
                "artwork_url": collection_artwork_url(collection),
            },
            "items": items, "candidates": candidates, "q": q,
            "message": request.query_params.get("message", ""),
        })

    @librarian_post("/collections/{collection_id}/edit")
    async def edit_collection(
        request: Request, collection_id: int, name: str = Form(...),
        description: str = Form(""), artwork: UploadFile | None = File(None),
        remove_artwork: str = Form(""),
    ):
        cleaned = " ".join(name.strip().split())[:80]
        if not cleaned:
            return redirect(
                f"/collections/{collection_id}",
                "The collection was not changed. Enter a name and try again.",
            )
        try:
            artwork_filename = await save_collection_artwork(artwork)
        except ValueError as exc:
            return redirect(f"/collections/{collection_id}", str(exc))
        old_artwork = ""
        try:
            with db.connect() as conn:
                current = conn.execute(
                    "SELECT artwork_filename FROM collections WHERE id=?",
                    (collection_id,),
                ).fetchone()
                if not current:
                    raise HTTPException(404, "Collection not found")
                old_artwork = current["artwork_filename"] or ""
                selected_artwork = (
                    artwork_filename if artwork_filename
                    else (None if remove_artwork == "1" else old_artwork or None)
                )
                conn.execute(
                    """UPDATE collections SET name=?,description=?,artwork_filename=?,
                         updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (cleaned, description.strip()[:1000], selected_artwork, collection_id),
                )
        except sqlite3.IntegrityError:
            if artwork_filename:
                (COLLECTION_ART_DIR / artwork_filename).unlink(missing_ok=True)
            return redirect(
                f"/collections/{collection_id}",
                f'The name "{cleaned}" is already used by another collection.',
            )
        if old_artwork and (artwork_filename or remove_artwork == "1"):
            (COLLECTION_ART_DIR / old_artwork).unlink(missing_ok=True)
        return redirect(
            f"/collections/{collection_id}",
            f'Collection "{cleaned}" updated.',
        )

    @librarian_post("/collections/{collection_id}/titles")
    def add_collection_title(
        request: Request, collection_id: int, title_id: int = Form(...),
    ):
        with db.connect() as conn:
            collection = conn.execute(
                "SELECT name FROM collections WHERE id=?", (collection_id,)
            ).fetchone()
            title = conn.execute(
                "SELECT COALESCE(NULLIF(metadata_title,''),title) name FROM titles WHERE id=?",
                (title_id,),
            ).fetchone()
            if not collection or not title:
                return redirect(
                    f"/collections/{collection_id}",
                    "The title could not be added because the collection or library title no longer exists.",
                )
            position = next_collection_position(conn, collection_id)
            cursor = conn.execute(
                """INSERT OR IGNORE INTO collection_titles(collection_id,title_id,position)
                   VALUES (?,?,?)""",
                (collection_id, title_id, position),
            )
        message = (
            f'"{title["name"]}" added to "{collection["name"]}".'
            if cursor.rowcount else
            f'"{title["name"]}" is already in "{collection["name"]}".'
        )
        return redirect(f"/collections/{collection_id}", message)

    @librarian_post("/collections/{collection_id}/titles/{title_id}/remove")
    def remove_collection_title(request: Request, collection_id: int, title_id: int):
        with db.connect() as conn:
            title = conn.execute(
                "SELECT COALESCE(NULLIF(metadata_title,''),title) name FROM titles WHERE id=?",
                (title_id,),
            ).fetchone()
            cursor = conn.execute(
                "DELETE FROM collection_titles WHERE collection_id=? AND title_id=?",
                (collection_id, title_id),
            )
            normalize_collection_positions(conn, collection_id)
        if not cursor.rowcount:
            return redirect(
                f"/collections/{collection_id}",
                "That title was not in this collection, so nothing was removed.",
            )
        return redirect(
            f"/collections/{collection_id}",
            f'"{title["name"] if title else "Title"}" removed from the collection. The media files were not changed.',
        )

    @librarian_post("/collections/{collection_id}/titles/{title_id}/move")
    def move_collection_title(
        request: Request, collection_id: int, title_id: int,
        direction: str = Form(...),
    ):
        return move_collection_item(collection_id, "title", title_id, direction)

    @librarian_post("/collections/{collection_id}/episodes/{episode_id}/move")
    def move_collection_episode(
        request: Request, collection_id: int, episode_id: int,
        direction: str = Form(...),
    ):
        return move_collection_item(collection_id, "episode", episode_id, direction)

    @librarian_post("/collections/{collection_id}/episodes/{episode_id}/remove")
    def remove_collection_episode(request: Request, collection_id: int, episode_id: int):
        with db.connect() as conn:
            episode = conn.execute(
                """SELECT e.name,e.season,e.episode,
                          COALESCE(NULLIF(t.metadata_title,''),t.title) show_name
                   FROM expected_episodes e JOIN titles t ON t.id=e.title_id
                   WHERE e.id=?""",
                (episode_id,),
            ).fetchone()
            cursor = conn.execute(
                """DELETE FROM collection_episodes
                   WHERE collection_id=? AND expected_episode_id=?""",
                (collection_id, episode_id),
            )
            normalize_collection_positions(conn, collection_id)
        if not cursor.rowcount:
            return redirect(
                f"/collections/{collection_id}",
                "That episode was not in this collection, so nothing was removed.",
            )
        label = (
            f'{episode["show_name"]} S{episode["season"]:02d}E{episode["episode"]:02d}'
            if episode else "Episode"
        )
        return redirect(
            f"/collections/{collection_id}",
            f'"{label}" removed from the collection. The episode file was not changed.',
        )

    @librarian_post("/collections/{collection_id}/delete")
    def delete_collection(request: Request, collection_id: int):
        artwork = ""
        with db.connect() as conn:
            collection = conn.execute(
                "SELECT name,artwork_filename FROM collections WHERE id=?",
                (collection_id,),
            ).fetchone()
            if not collection:
                return redirect("/collections", "That collection no longer exists.")
            artwork = collection["artwork_filename"] or ""
            conn.execute("DELETE FROM collections WHERE id=?", (collection_id,))
        if artwork:
            (COLLECTION_ART_DIR / artwork).unlink(missing_ok=True)
        return redirect(
            "/collections",
            f'Collection "{collection["name"]}" deleted. No movies, TV series, or media files were removed.',
        )

    return router, {
        "title_collections_page": title_collections_page,
        "save_title_collections": save_title_collections,
        "episode_collections_page": episode_collections_page,
        "save_episode_collections": save_episode_collections,
        "collections_page": collections_page,
        "preview_smart_collection": preview_smart_collection,
        "create_smart_collection": create_smart_collection,
        "custom_libraries_page": custom_libraries_page,
        "create_custom_library": create_custom_library,
        "custom_library_detail": custom_library_detail,
        "title_libraries_page": title_libraries_page,
        "save_title_libraries": save_title_libraries,
        "create_collection": create_collection,
        "collection_artwork": collection_artwork,
        "collection_detail": collection_detail,
        "edit_collection": edit_collection,
        "add_collection_title": add_collection_title,
        "remove_collection_title": remove_collection_title,
        "move_collection_title": move_collection_title,
        "move_collection_episode": move_collection_episode,
        "remove_collection_episode": remove_collection_episode,
        "delete_collection": delete_collection,
    }

from fastapi import APIRouter, Depends

from ..access import require_librarian
from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    Form = ctx.get("Form")
    HTMLResponse = ctx.get("HTMLResponse")
    HTTPException = ctx.get("HTTPException")
    Path = ctx.get("Path")
    Request = ctx.get("Request")
    TVDBError = ctx.get("TVDBError")
    TitleMetadataService = ctx.get("TitleMetadataService")
    active_title_ids = ctx.live("active_title_ids")
    actors = ctx.live("actors")
    allowed = ctx.live("allowed")
    allowed_collections = ctx.live("allowed_collections")
    allowed_ids = ctx.live("allowed_ids")
    app = ctx.live("app")
    apply = ctx.live("apply")
    assignments = ctx.live("assignments")
    candidates = ctx.live("candidates")
    changed = ctx.live("changed")
    chosen_ids = ctx.live("chosen_ids")
    clean_label = ctx.live("clean_label")
    cleaned = ctx.live("cleaned")
    collection_id = ctx.live("collection_id")
    collection_ids = ctx.live("collection_ids")
    collections = ctx.live("collections")
    confirm = ctx.live("confirm")
    conn = ctx.live("conn")
    contained_destination = ctx.live("contained_destination")
    context = ctx.live("context")
    continuing = ctx.live("continuing")
    covered_credits = ctx.live("covered_credits")
    credit = ctx.live("credit")
    credit_rows = ctx.live("credit_rows")
    credit_update_active = ctx.live("credit_update_active")
    db = ctx.live("db")
    description = ctx.live("description")
    destination = ctx.live("destination")
    directors = ctx.live("directors")
    duplicates = ctx.live("duplicates")
    edition_name = ctx.live("edition_name")
    edition_version_context = ctx.live("edition_version_context")
    edition_versions = ctx.live("edition_versions")
    episode = ctx.live("episode")
    episode_count = ctx.live("episode_count")
    episode_credit_map = ctx.live("episode_credit_map")
    episode_credit_rows = ctx.live("episode_credit_rows")
    episode_name = ctx.live("episode_name")
    episode_names = ctx.live("episode_names")
    episode_number = ctx.live("episode_number")
    episode_rename_proposals = ctx.live("episode_rename_proposals")
    episode_tvdb_ids = ctx.live("episode_tvdb_ids")
    error = ctx.live("error")
    exc = ctx.live("exc")
    expected = ctx.live("expected")
    expected_name_map = ctx.live("expected_name_map")
    expected_rows = ctx.live("expected_rows")
    favorite = ctx.live("favorite")
    file = ctx.live("file")
    file_id = ctx.live("file_id")
    file_ids = ctx.live("file_ids")
    file_row = ctx.live("file_row")
    file_rows = ctx.live("file_rows")
    file_view = ctx.live("file_view")
    files = ctx.live("files")
    final_episode = ctx.live("final_episode")
    first_aired = ctx.live("first_aired")
    formatted = ctx.live("formatted")
    found = ctx.live("found")
    genre = ctx.live("genre")
    genres = ctx.live("genres")
    height = ctx.live("height")
    identity = ctx.live("identity")
    imdb_genre_job = ctx.live("imdb_genre_job")
    imdb_genre_lock = ctx.live("imdb_genre_lock")
    index = ctx.live("index")
    is_tvdb_series_reference = ctx.live("is_tvdb_series_reference")
    item = ctx.live("item")
    key = ctx.live("key")
    labels = ctx.live("labels")
    letter = ctx.live("letter")
    letters = ctx.live("letters")
    localized_tvdb_title = ctx.live("localized_tvdb_title")
    match_confidence = ctx.live("match_confidence")
    match_origin = ctx.live("match_origin")
    match_success_redirect = ctx.live("match_success_redirect")
    media_info_job = ctx.live("media_info_job")
    media_info_lock = ctx.live("media_info_lock")
    merged_episode_name = ctx.live("merged_episode_name")
    message = ctx.live("message")
    missing = ctx.live("missing")
    missing_view = ctx.live("missing_view")
    movie_id = ctx.live("movie_id")
    name = ctx.live("name")
    new_name = ctx.live("new_name")
    new_names = ctx.live("new_names")
    new_path = ctx.live("new_path")
    new_prefix = ctx.live("new_prefix")
    next_position = ctx.live("next_position")
    number = ctx.live("number")
    number_style = ctx.live("number_style")
    numbers = ctx.live("numbers")
    offset = ctx.live("offset")
    old_prefix = ctx.live("old_prefix")
    ordered = ctx.live("ordered")
    pair = ctx.live("pair")
    personal_rating = ctx.live("personal_rating")
    placeholders = ctx.live("placeholders")
    plex_episode_filename = ctx.live("plex_episode_filename")
    plex_movie_filename = ctx.live("plex_movie_filename")
    plex_movie_ids = ctx.live("plex_movie_ids")
    plex_show_folder = ctx.live("plex_show_folder")
    poster_candidates = ctx.live("poster_candidates")
    poster_from = ctx.live("poster_from")
    poster_url = ctx.live("poster_url")
    preferred = ctx.live("preferred")
    prefix = ctx.live("prefix")
    proposal = ctx.live("proposal")
    proposals = ctx.live("proposals")
    proposed = ctx.live("proposed")
    provider = ctx.live("provider")
    provider_id = ctx.live("provider_id")
    provider_search_url = ctx.live("provider_search_url")
    q = ctx.live("q")
    query = ctx.live("query")
    rating = ctx.live("rating")
    raw_name = ctx.live("raw_name")
    raw_results = ctx.live("raw_results")
    re = ctx.live("re")
    record = ctx.live("record")
    record_event = ctx.live("record_event")
    redirect = ctx.live("redirect")
    renamed = ctx.live("renamed")
    request = ctx.live("request")
    resolution = ctx.live("resolution")
    restore_filename_proposals = ctx.live("restore_filename_proposals")
    restored = ctx.live("restored")
    result = ctx.live("result")
    results = ctx.live("results")
    return_to = ctx.live("return_to")
    review = ctx.live("review")
    row = ctx.live("row")
    rows = ctx.live("rows")
    run_media_inspection = ctx.live("run_media_inspection")
    runtime_values = ctx.live("runtime_values")
    safe_next = ctx.live("safe_next")
    saved = ctx.live("saved")
    saved_identity = ctx.live("saved_identity")
    scan_at = ctx.live("scan_at")
    scan_is_stale = ctx.live("scan_is_stale")
    search_movies_broadly = ctx.live("search_movies_broadly")
    search_series_broadly = ctx.live("search_series_broadly")
    season = ctx.live("season")
    season_totals = ctx.live("season_totals")
    seasons = ctx.live("seasons")
    seen_credits = ctx.live("seen_credits")
    selected = ctx.live("selected")
    selected_collections = ctx.live("selected_collections")
    selected_file_ids = ctx.live("selected_file_ids")
    selected_tags = ctx.live("selected_tags")
    sequence_letter = ctx.live("sequence_letter")
    sequence_number = ctx.live("sequence_number")
    series = ctx.live("series")
    series_id = ctx.live("series_id")
    series_provider_search_url = ctx.live("series_provider_search_url")
    service = ctx.live("service")
    settings = ctx.live("settings")
    show_name = ctx.live("show_name")
    sibling = ctx.live("sibling")
    sibling_identity = ctx.live("sibling_identity")
    skipped = ctx.live("skipped")
    sort_title = ctx.live("sort_title")
    sort_value = ctx.live("sort_value")
    source = ctx.live("source")
    state = ctx.live("state")
    store_movie_match = ctx.live("store_movie_match")
    store_tv_match = ctx.live("store_tv_match")
    tag_id = ctx.live("tag_id")
    tag_ids = ctx.live("tag_ids")
    tag_names = ctx.live("tag_names")
    tags = ctx.live("tags")
    technical_file = ctx.live("technical_file")
    templates = ctx.live("templates")
    threading = ctx.live("threading")
    title = ctx.live("title")
    title_facts = ctx.live("title_facts")
    title_id = ctx.live("title_id")
    title_ids = ctx.live("title_ids")
    title_state = ctx.live("title_state")
    title_tags = ctx.live("title_tags")
    titles = ctx.live("titles")
    tvdb = ctx.live("tvdb")
    tvdb_reference = ctx.live("tvdb_reference")
    tvdb_series_id_from_reference = ctx.live("tvdb_series_id_from_reference")
    urlencode = ctx.live("urlencode")
    user_id = ctx.live("user_id")
    valid = ctx.live("valid")
    valid_ids = ctx.live("valid_ids")
    valid_urls = ctx.live("valid_urls")
    value = ctx.live("value")
    version_name = ctx.live("version_name")
    width = ctx.live("width")
    writers = ctx.live("writers")

    def librarian_get(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.get(path, dependencies=dependencies, **kwargs)

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

    @router.get("/titles/{title_id}/organize", response_class=HTMLResponse)
    def organize_title_page(request: Request, title_id: int):
        with db.connect() as conn:
            title = conn.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
            if not title:
                raise HTTPException(404, "Title not found")
            state = conn.execute(
                "SELECT * FROM user_title_state WHERE user_id=? AND title_id=?",
                (request.state.user.id, title_id),
            ).fetchone()
            tags = conn.execute(
                """SELECT ut.*,tt.title_id IS NOT NULL selected
                   FROM user_tags ut LEFT JOIN title_tags tt
                     ON tt.tag_id=ut.id AND tt.title_id=?
                   WHERE ut.user_id=? ORDER BY ut.name COLLATE NOCASE""",
                (title_id, request.state.user.id),
            ).fetchall()
            collections = conn.execute(
                """SELECT c.*,ct.title_id IS NOT NULL selected
                   FROM collections c LEFT JOIN collection_titles ct
                     ON ct.collection_id=c.id AND ct.title_id=?
                   WHERE c.collection_type='manual' ORDER BY c.name COLLATE NOCASE""",
                (title_id,),
            ).fetchall()
        return templates.TemplateResponse(request, "organize.html", {
            "title": title, "title_state": state, "tags": tags,
            "collections": collections,
            "message": request.query_params.get("message", ""),
        })

    @router.post("/titles/{title_id}/organize")
    def save_title_organization(
        request: Request, title_id: int, favorite: str = Form(""),
        personal_rating: str = Form(""), sort_title: str = Form(""),
        tag_names: str = Form(""), selected_tags: list[int] = Form(default=[]),
        selected_collections: list[int] = Form(default=[]),
    ):
        if request.state.user.id <= 0:
            return redirect(
                f"/titles/{title_id}",
                "Personal organization requires a signed-in user account.",
            )
        try:
            rating = float(personal_rating) if personal_rating.strip() else None
            if rating is not None and not 0 <= rating <= 10:
                raise ValueError
        except ValueError:
            return redirect(
                f"/titles/{title_id}/organize",
                "Personal rating must be a number from 0 to 10, or left blank.",
            )
        sort_value = " ".join(sort_title.strip().split())[:200] or None
        new_names = []
        for raw_name in tag_names.split(","):
            cleaned = " ".join(raw_name.strip().split())
            if cleaned and cleaned.casefold() not in {item.casefold() for item in new_names}:
                new_names.append(cleaned[:40])
        if len(new_names) > 20:
            return redirect(
                f"/titles/{title_id}/organize",
                "Add no more than 20 new tags at a time so they remain easy to review.",
            )
        with db.connect() as conn:
            if not conn.execute("SELECT id FROM titles WHERE id=?", (title_id,)).fetchone():
                raise HTTPException(404, "Title not found")
            conn.execute(
                """INSERT INTO user_title_state
                   (user_id,title_id,favorite,personal_rating,sort_title,updated_at)
                   VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(user_id,title_id) DO UPDATE SET
                     favorite=excluded.favorite,
                     personal_rating=excluded.personal_rating,
                     sort_title=excluded.sort_title,
                     updated_at=CURRENT_TIMESTAMP""",
                (
                    request.state.user.id, title_id, int(favorite == "1"),
                    rating, sort_value,
                ),
            )
            allowed_ids = {
                row["id"] for row in conn.execute(
                    "SELECT id FROM user_tags WHERE user_id=?",
                    (request.state.user.id,),
                )
            }
            chosen_ids = {tag_id for tag_id in selected_tags if tag_id in allowed_ids}
            for name in new_names:
                conn.execute(
                    """INSERT INTO user_tags(user_id,name) VALUES (?,?)
                       ON CONFLICT(user_id,name) DO NOTHING""",
                    (request.state.user.id, name),
                )
                row = conn.execute(
                    "SELECT id FROM user_tags WHERE user_id=? AND name=? COLLATE NOCASE",
                    (request.state.user.id, name),
                ).fetchone()
                if row:
                    chosen_ids.add(row["id"])
            conn.execute(
                """DELETE FROM title_tags WHERE title_id=? AND tag_id IN
                   (SELECT id FROM user_tags WHERE user_id=?)""",
                (title_id, request.state.user.id),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO title_tags(title_id,tag_id) VALUES (?,?)",
                [(title_id, tag_id) for tag_id in chosen_ids],
            )
            if request.state.user.is_librarian:
                allowed_collections = {
                    row["id"] for row in conn.execute("SELECT id FROM collections")
                }
                collection_ids = {
                    collection_id for collection_id in selected_collections
                    if collection_id in allowed_collections
                }
                conn.execute("DELETE FROM collection_titles WHERE title_id=?", (title_id,))
                for collection_id in collection_ids:
                    next_position = conn.execute(
                        """SELECT COALESCE(MAX(position),-1)+1 next_position
                           FROM collection_titles WHERE collection_id=?""",
                        (collection_id,),
                    ).fetchone()["next_position"]
                    conn.execute(
                        """INSERT INTO collection_titles(collection_id,title_id,position)
                           VALUES (?,?,?)""",
                        (collection_id, title_id, next_position),
                    )
        record_event(
            "library", "Personal title organization updated.",
            user_id=request.state.user.id,
            context={"title_id": title_id, "tags": len(chosen_ids)},
        )
        message = "Favorites, rating, order, and tags saved."
        if request.state.user.is_librarian:
            message = "Organization and collection membership saved."
        return redirect(f"/titles/{title_id}", message)

    @router.post("/titles/organize-bulk", response_class=HTMLResponse)
    def organize_titles_bulk(
        request: Request, selected: list[int] = Form(default=[]),
        apply: str = Form(""), selected_tags: list[int] = Form(default=[]),
        tag_names: str = Form(""),
        selected_collections: list[int] = Form(default=[]),
    ):
        title_ids = list(dict.fromkeys(selected))[:1000]
        if not title_ids:
            return redirect(
                "/library",
                "Select at least one movie or TV series before organizing tags.",
            )
        user_id = request.state.user.id
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
            if apply == "1":
                allowed = {row["id"] for row in tags}
                tag_ids = {tag_id for tag_id in selected_tags if tag_id in allowed}
                new_names = []
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
                record_event(
                    "library",
                    f"Tags added to {len(title_ids)} selected titles.",
                    user_id=user_id,
                    context={"titles": len(title_ids), "tags": len(tag_ids)},
                )
                return redirect(
                    "/library",
                    f"Organization saved for {len(title_ids)} selected "
                    f"title{'s' if len(title_ids) != 1 else ''}.",
                )
        return templates.TemplateResponse(request, "organize_bulk.html", {
            "titles": titles, "title_ids": title_ids, "tags": tags,
            "collections": collections, "message": "",
        })

    @router.get("/titles/sort-titles", response_class=HTMLResponse)
    def sort_titles_dialog(request: Request):
        if request.state.user.id <= 0:
            return redirect("/library", "Sort Titles require a signed-in account.")
        selected = []
        for value in request.query_params.getlist("selected")[:200]:
            if value.isdigit() and int(value) not in selected:
                selected.append(int(value))
        if len(selected) < 2:
            return redirect("/library", "Select at least two titles to append Sort Titles.")
        placeholders = ",".join("?" for _ in selected)
        with db.connect() as conn:
            found = {row["id"]: dict(row) for row in conn.execute(
                f"""SELECT id,COALESCE(NULLIF(metadata_title,''),title) display_title,
                           poster_url FROM titles WHERE id IN ({placeholders})""", selected,
            )}
        titles = [found[title_id] for title_id in selected if title_id in found]
        return templates.TemplateResponse(request, "sort_titles_dialog.html", {
            "titles": titles, "return_to": safe_next(request.query_params.get("return_to") or "/library"),
            "message": "",
        })

    @librarian_post("/titles/sort-titles")
    def apply_sort_titles(
        request: Request, selected: list[int] = Form(default=[]),
        sequence_number: list[int] = Form(default=[]),
        sequence_letter: list[str] = Form(default=[]), number_style: str = Form("padded"),
        prefix: str = Form(""), return_to: str = Form("/library"),
    ):
        if request.state.user.id <= 0:
            return redirect("/library", "Sort Titles require a signed-in account.")
        ordered = list(dict.fromkeys(title_id for title_id in selected if title_id > 0))[:200]
        cleaned = " ".join(prefix.strip().split())[:160]
        if len(ordered) < 2 or not cleaned:
            destination = "/titles/sort-titles?" + urlencode(
                [("selected", str(title_id)) for title_id in ordered]
                + [("return_to", safe_next(return_to))]
            )
            return redirect(destination, "Enter a prefix and keep at least two selected titles. Nothing changed.")
        numbers = [value if 1 <= value <= 9999 else index for index, value in enumerate(sequence_number, 1)]
        if len(numbers) != len(ordered):
            numbers = list(range(1, len(ordered) + 1))
        letters = [value.casefold() if re.fullmatch(r"[a-z]?", value.casefold()) else "" for value in sequence_letter]
        if len(letters) != len(ordered):
            letters = [""] * len(ordered)
        width = max(2, len(str(max(numbers)))) if number_style == "padded" else 0
        with db.connect() as conn:
            valid = {row["id"] for row in conn.execute(
                f"SELECT id FROM titles WHERE id IN ({','.join('?' for _ in ordered)})", ordered,
            )}
            assignments = []
            for title_id, number, letter in zip(ordered, numbers, letters):
                if title_id not in valid:
                    continue
                formatted = f"{number:0{width}d}" if width else str(number)
                assignments.append((request.state.user.id, title_id, f"{cleaned} {formatted}{letter}"))
            conn.executemany(
                """INSERT INTO user_title_state(user_id,title_id,sort_title,updated_at)
                   VALUES (?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(user_id,title_id) DO UPDATE SET
                     sort_title=excluded.sort_title,updated_at=CURRENT_TIMESTAMP""",
                assignments,
            )
        message = f'Applied {len(assignments)} Sort Titles using the prefix "{cleaned}".'
        record_event("library", message, user_id=request.state.user.id, context={"titles": len(assignments)})
        return redirect(safe_next(return_to), message)

    @librarian_get("/files/{file_id}/edition-version", response_class=HTMLResponse)
    def edition_version_page(request: Request, file_id: int):
        context = edition_version_context(file_id)
        file = context["file"]
        proposed = dict(context["current"])
        if not file["identity_confirmed"]:
            proposed.update({
                "edition_name": file["suggested_edition"],
                "version_name": file["suggested_version"],
            })
        return templates.TemplateResponse(request, "edition_version.html", {
            **context, "proposed": proposed, "preview": False,
        })

    @librarian_post("/files/{file_id}/edition-version/preview", response_class=HTMLResponse)
    def preview_edition_version(
        request: Request, file_id: int, edition_name: str = Form(""),
        version_name: str = Form(""), preferred: str = Form(""),
    ):
        context = edition_version_context(file_id)
        return templates.TemplateResponse(request, "edition_version.html", {
            **context,
            "proposed": {
                "edition_name": clean_label(edition_name),
                "version_name": clean_label(version_name),
                "preferred": preferred == "1",
            },
            "preview": True,
        })

    @librarian_post("/files/{file_id}/edition-version")
    def save_edition_version(
        request: Request, file_id: int, edition_name: str = Form(""),
        version_name: str = Form(""), preferred: str = Form(""),
        confirm: str = Form(""),
    ):
        context = edition_version_context(file_id)
        if confirm.strip().upper() != "SAVE":
            return redirect(
                f"/files/{file_id}/edition-version",
                "Confirmation did not match SAVE. Nothing changed.",
            )
        saved = edition_versions.save(
            file_id, edition_name=edition_name, version_name=version_name,
            preferred=preferred == "1",
        )
        if not saved:
            return redirect("/library", "That file is no longer in the catalog. Nothing changed.")
        saved_identity = identity(saved)
        for sibling in edition_versions.siblings(file_id):
            pair = tuple(sorted((file_id, int(sibling["id"]))))
            sibling_identity = identity(sibling)
            with db.connect() as conn:
                review = conn.execute(
                    """SELECT decision,review_source FROM duplicate_reviews
                       WHERE file_a_id=? AND file_b_id=?""", pair,
                ).fetchone()
            if saved_identity and sibling_identity and saved_identity != sibling_identity:
                duplicates.decide(
                    *pair, "not_duplicate", request.state.user.id,
                    source=(review["review_source"] if review else "edition_version"),
                )
            elif review and review["review_source"] == "edition_version":
                duplicates.decide(
                    *pair, "active", request.state.user.id,
                    source="edition_version",
                )
        labels = [value for value in (saved["edition_name"], saved["version_name"]) if value]
        description = " · ".join(labels) or "Unlabeled"
        if saved["version_preferred"]:
            description += " · Preferred"
        record_event(
            "library", f"Edition and version updated for {saved['filename']}: {description}.",
            context={"title_id": saved["title_id"], "file_id": file_id},
            user_id=request.state.user.id,
        )
        return redirect(
            context["return_to"],
            f"Edition and version saved for {saved['filename']}. No media files were changed.",
        )

    @librarian_post("/titles/{title_id}/metadata/enrich")
    def enrich_title_metadata(title_id: int):
        service = TitleMetadataService(
            db, tvdb, poster_from=poster_from, plex_movie_ids=plex_movie_ids,
            localized_title=localized_tvdb_title, match_confidence=match_confidence,
        )
        try:
            changed = service.enrich(title_id)
        except TVDBError as exc:
            record_event(
                "metadata", "Title metadata enrichment could not reach TVDB.",
                level="warning", detail=str(exc), context={"title_id": title_id},
            )
            return redirect(f"/titles/{title_id}", "TVDB metadata refresh could not finish. Try again later.")
        except ValueError:
            raise HTTPException(404, "Title not found")
        if changed:
            record_event("metadata", "Title metadata was refreshed from TVDB.", context={"title_id": title_id})
        return redirect(
            f"/titles/{title_id}",
            "Metadata refreshed." if changed else "No missing TVDB metadata needed to be refreshed.",
        )

    @router.get("/titles/{title_id}", response_class=HTMLResponse)
    def title_detail(request: Request, title_id: int):
        with db.connect() as conn:
            title = conn.execute(
                """SELECT t.*, r.label source_label,
                          r.last_scanned_at root_last_scanned_at
                   FROM titles t JOIN roots r ON r.id=t.root_id WHERE t.id=?""",
                (title_id,),
            ).fetchone()
            if not title:
                raise HTTPException(404, "Title not found")
            file_rows = conn.execute(
                """SELECT f.* FROM files f
                   WHERE f.title_id=? ORDER BY f.season, f.episode_start, f.filename""",
                (title_id,),
            ).fetchall()
            episode_names = expected_name_map(conn, title_id)
            expected_rows = conn.execute(
                """SELECT id, season, episode, tvdb_episode_id, imdb_id FROM expected_episodes
                   WHERE title_id=? ORDER BY season, episode""",
                (title_id,),
            ).fetchall()
            episode_credit_rows = conn.execute(
                """SELECT e.season, e.episode, c.imdb_person_id, c.person_name,
                          c.role, c.billing_order
                   FROM episode_credits c JOIN expected_episodes e ON e.id=c.expected_episode_id
                   WHERE e.title_id=? ORDER BY e.season, e.episode, c.role, c.billing_order""",
                (title_id,),
            ).fetchall()
            episode_credit_map: dict[tuple[int, int], list] = {}
            for credit in episode_credit_rows:
                episode_credit_map.setdefault(
                    (credit["season"], credit["episode"]), []
                ).append(credit)
            episode_tvdb_ids = {
                (row["season"], row["episode"]): row["tvdb_episode_id"]
                for row in expected_rows
            }
            season_totals: dict[int, int] = {}
            for expected in expected_rows:
                season_totals[expected["season"]] = season_totals.get(expected["season"], 0) + 1
            files = []
            for file_row in file_rows:
                file_view = dict(file_row)
                file_view["episode_name"] = merged_episode_name(
                    episode_names, file_row["season"], file_row["episode_start"],
                    file_row["episode_end"],
                )
                file_view["episode_tvdb_id"] = episode_tvdb_ids.get(
                    (file_row["season"], file_row["episode_start"])
                )
                file_view["season_total"] = season_totals.get(file_row["season"])
                covered_credits = []
                if file_row["season"] is not None and file_row["episode_start"] is not None:
                    final_episode = max(
                        file_row["episode_start"],
                        file_row["episode_end"] or file_row["episode_start"],
                    )
                    seen_credits = set()
                    for episode_number in range(file_row["episode_start"], final_episode + 1):
                        for credit in episode_credit_map.get(
                            (file_row["season"], episode_number), []
                        ):
                            key = (credit["imdb_person_id"], credit["role"])
                            if key not in seen_credits:
                                seen_credits.add(key)
                                covered_credits.append(credit)
                file_view["episode_directors"] = [
                    credit for credit in covered_credits if credit["role"] == "director"
                ]
                file_view["episode_writers"] = [
                    credit for credit in covered_credits if credit["role"] == "writer"
                ]
                files.append(file_view)
            missing = conn.execute(
                """SELECT e.* FROM expected_episodes e WHERE e.title_id=? AND e.season > 0
                   AND (e.aired IS NULL OR e.aired <= date('now')) AND NOT EXISTS (
                     SELECT 1 FROM files f WHERE f.title_id=e.title_id AND f.season=e.season
                     AND e.episode BETWEEN f.episode_start AND f.episode_end)
                   ORDER BY e.season, e.episode""", (title_id,)
            ).fetchall()
            credit_rows = conn.execute(
                """SELECT imdb_person_id, person_name, role, billing_order
                   FROM title_credits WHERE title_id=?
                   ORDER BY CASE role WHEN 'director' THEN 1 WHEN 'actor' THEN 2 ELSE 3 END,
                            billing_order, person_name COLLATE NOCASE""",
                (title_id,),
            ).fetchall()
            title_state = conn.execute(
                """SELECT favorite, personal_rating, custom_order, sort_title
                   FROM user_title_state WHERE user_id=? AND title_id=?""",
                (request.state.user.id, title_id),
            ).fetchone() if request.state.user.id > 0 else None
            title_tags = conn.execute(
                """SELECT ut.id, ut.name, ut.color
                   FROM user_tags ut JOIN title_tags tt ON tt.tag_id=ut.id
                   WHERE ut.user_id=? AND tt.title_id=?
                   ORDER BY ut.name COLLATE NOCASE""",
                (request.state.user.id, title_id),
            ).fetchall() if request.state.user.id > 0 else []
        missing_view = []
        show_name = title["metadata_title"] or title["title"]
        for episode in missing:
            query = f'{show_name} S{episode["season"]:02d}E{episode["episode"]:02d}'
            missing_view.append({**dict(episode), "query": query,
                                 "search_url": provider_search_url(query)})
        seasons = sorted({row["season"] for row in files if row["season"] is not None})
        genres = [genre for genre in (title["genres"] or "").split(",") if genre]
        directors = [row for row in credit_rows if row["role"] == "director"]
        actors = [row for row in credit_rows if row["role"] == "actor"]
        writers = [row for row in credit_rows if row["role"] == "writer"]
        runtime_values = [row["runtime_seconds"] for row in files if row["runtime_seconds"]]
        title_facts = []
        if title["metadata_status"]:
            title_facts.append(("Status", title["metadata_status"]))
        if title["kind"] == "tv":
            if seasons:
                title_facts.append(("Seasons", str(len([season for season in seasons if season > 0]))))
            if expected_rows:
                title_facts.append(("Episodes", str(len(expected_rows))))
        elif runtime_values:
            title_facts.append(("Runtime", f"{round(max(runtime_values) / 60):.0f} min"))
        technical_file = next(
            (row for row in files if row["version_preferred"]),
            files[0] if files else None,
        )
        if technical_file:
            if technical_file["width"] and technical_file["height"]:
                width = int(technical_file["width"])
                height = int(technical_file["height"])
                resolution = (
                    "4K UHD" if width >= 3800 or height >= 2000
                    else "1440p" if width >= 2500 or height >= 1400
                    else "1080p" if width >= 1900 or height >= 1000
                    else "720p" if width >= 1200 or height >= 700
                    else f"{width} × {height}"
                )
                title_facts.append((
                    "Resolution", resolution,
                ))
            if technical_file["video_codec"]:
                title_facts.append(("Video", technical_file["video_codec"].upper()))
            if technical_file["dynamic_range"]:
                title_facts.append(("HDR", technical_file["dynamic_range"]))
            if technical_file["audio_codec"]:
                title_facts.append(("Audio", technical_file["audio_codec"].upper()))
        if title["source_label"]:
            title_facts.append(("Source", title["source_label"]))
        scan_at = title["last_scanned_at"] or title["root_last_scanned_at"]
        with imdb_genre_lock:
            active_title_ids = imdb_genre_job.get("title_ids")
            credit_update_active = (
                imdb_genre_job.get("status") in {"starting", "running"}
                and (active_title_ids is None or title_id in active_title_ids)
            )
        return templates.TemplateResponse(request, "detail.html", {
            "title": title, "files": files, "missing": missing_view,
            "seasons": seasons, "genres": genres,
            "directors": directors, "actors": actors, "writers": writers,
            "title_facts": title_facts,
            "credit_update_active": credit_update_active,
            "scan_at": scan_at, "scan_stale": scan_is_stale(scan_at),
            "series_search_url": series_provider_search_url(title),
            "title_state": title_state, "title_tags": title_tags,
            "tvdb_enabled": bool(getattr(tvdb, "api_key", settings.tvdb_api_key)),
            "message": request.query_params.get("message", ""),
        })

    @librarian_get("/titles/{title_id}/cover", response_class=HTMLResponse)
    def title_cover(request: Request, title_id: int):
        with db.connect() as conn:
            title = conn.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
        if not title:
            raise HTTPException(404, "Title not found")
        provider_id = title["tvdb_id"] if title["kind"] == "tv" else title["tvdb_movie_id"]
        candidates: list[dict] = []
        error = ""
        if not provider_id:
            error = (
                "No alternate covers are available because this title is not matched to "
                "TheTVDB. Match the title first, then return here to choose its artwork."
            )
        else:
            try:
                record = (
                    tvdb.series(provider_id)
                    if title["kind"] == "tv"
                    else tvdb.movie(provider_id)
                )
                candidates = poster_candidates(record)
            except TVDBError:
                error = (
                    "InfoMancer could not load alternate covers from TheTVDB. Check the "
                    "TVDB connection in Settings, then try again."
                )
        return templates.TemplateResponse(request, "cover.html", {
            "title": title, "candidates": candidates, "error": error,
            "message": request.query_params.get("message", ""),
        })

    @librarian_post("/titles/{title_id}/cover")
    def update_title_cover(
        title_id: int, poster_url: str = Form(...), return_to: str = Form(""),
    ):
        with db.connect() as conn:
            title = conn.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
        if not title:
            return redirect("/library", "The cover could not be changed because that title no longer exists.")
        provider_id = title["tvdb_id"] if title["kind"] == "tv" else title["tvdb_movie_id"]
        if not provider_id:
            return redirect(
                f"/titles/{title_id}/cover",
                "The cover could not be changed because this title is not matched to TheTVDB.",
            )
        try:
            record = (
                tvdb.series(provider_id)
                if title["kind"] == "tv"
                else tvdb.movie(provider_id)
            )
            valid_urls = {item["url"] for item in poster_candidates(record)}
        except TVDBError:
            return redirect(
                f"/titles/{title_id}/cover",
                "The cover could not be changed because TheTVDB could not be reached. Check the TVDB connection in Settings and try again.",
            )
        selected = poster_url.strip()
        if selected not in valid_urls:
            return redirect(
                f"/titles/{title_id}/cover",
                "That cover is no longer available from TheTVDB. Refresh the choices and select another cover.",
            )
        with db.connect() as conn:
            conn.execute(
                "UPDATE titles SET poster_url=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (selected, title_id),
            )
        record_event(
            "metadata",
            f"Cover changed for {title['metadata_title'] or title['title']}.",
            context={"title_id": title_id, "provider": "tvdb"},
        )
        return match_success_redirect(title_id, "Cover updated", return_to)

    @librarian_post("/titles/{title_id}/media-info")
    def inspect_title_media(title_id: int):
        global media_info_job
        with db.connect() as conn:
            title = conn.execute(
                "SELECT id, metadata_title, title FROM titles WHERE id=?", (title_id,)
            ).fetchone()
            if not title:
                return redirect(
                    "/library",
                    "Media inspection could not start because that title no longer exists.",
                )
            file_ids = [
                row["id"] for row in conn.execute(
                    "SELECT id FROM files WHERE title_id=? ORDER BY id", (title_id,)
                ).fetchall()
            ]
        if not file_ids:
            return redirect(
                f"/titles/{title_id}",
                "Media inspection found no files for this title. Rescan its source, then try again.",
            )
        with media_info_lock:
            if media_info_job.get("status") in {"starting", "running"}:
                return redirect(
                    f"/titles/{title_id}",
                    "Media inspection is already running. Its progress is available in the task widget.",
                )
            media_info_job = {
                "status": "starting", "current": 0, "total": len(file_ids),
                "message": "Preparing media inspection",
            }
        threading.Thread(target=run_media_inspection, args=(file_ids,), daemon=True).start()
        record_event(
            "media",
            f"Media inspection requested for {title['metadata_title'] or title['title']}.",
            context={"title_id": title_id, "files": len(file_ids)},
        )
        return redirect(
            f"/titles/{title_id}",
            f"Media inspection started for {len(file_ids)} file{'s' if len(file_ids) != 1 else ''}. Progress is shown in the task widget.",
        )

    @librarian_get("/titles/{title_id}/tvdb", response_class=HTMLResponse)
    def tvdb_search(request: Request, title_id: int, q: str = ""):
        with db.connect() as conn:
            title = conn.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
        if not title:
            raise HTTPException(404, "Title not found")
        query = q or title["title"]
        try:
            if title["kind"] == "movie":
                raw_results = search_movies_broadly(query)
            elif query.strip().isdigit() or is_tvdb_series_reference(query):
                series_id = tvdb_series_id_from_reference(query)
                series = tvdb.series(series_id)
                first_aired = str(series.get("firstAired") or series.get("first_aired") or "")
                raw_results = [{
                    **series,
                    "tvdb_id": series_id,
                    "image_url": poster_from(series),
                    "year": first_aired[:4],
                    "overview": series.get("overview") or "",
                    "_direct_reference": True,
                }]
            else:
                raw_results = search_series_broadly(query)
            results = [
                {**result, "confidence": match_confidence(title["title"], title["year"], result)}
                for result in raw_results
            ]
            results.sort(
                key=lambda result: (
                    bool(result.get("_direct_reference")),
                    result["confidence"]["score"],
                ),
                reverse=True,
            )
        except TVDBError as exc:
            results = []
            error = str(exc)
        else:
            error = ""
        return templates.TemplateResponse(request, "tvdb.html", {
            "title": title, "q": query, "results": results, "error": error,
            "entity": "movie" if title["kind"] == "movie" else "series",
            "message": request.query_params.get("message", ""),
        })

    @librarian_post("/titles/{title_id}/movie/{movie_id}")
    def match_movie(
        title_id: int, movie_id: int, return_to: str = Form(""),
        match_origin: str = Form(""),
    ):
        try:
            provider = store_movie_match(title_id, movie_id)
        except (TVDBError, ValueError) as exc:
            return redirect(f"/titles/{title_id}", str(exc))
        return match_success_redirect(
            title_id, f"Movie matched using {provider}", return_to, match_origin,
        )

    @librarian_post("/titles/{title_id}/tvdb/{series_id}")
    def match_tvdb(
        title_id: int, series_id: int, return_to: str = Form(""),
        match_origin: str = Form(""),
    ):
        try:
            episode_count = store_tv_match(title_id, series_id)
        except TVDBError as exc:
            return redirect(f"/titles/{title_id}", str(exc))
        return match_success_redirect(
            title_id, f"Matched to TVDB and loaded {episode_count} episodes",
            return_to, match_origin,
        )

    @librarian_post("/titles/{title_id}/tvdb-manual")
    def match_tvdb_manual(
        title_id: int, tvdb_reference: str = Form(""), return_to: str = Form(""),
        match_origin: str = Form(""),
    ):
        with db.connect() as conn:
            title = conn.execute(
                "SELECT id, kind FROM titles WHERE id=?", (title_id,)
            ).fetchone()
        if not title:
            raise HTTPException(404, "Title not found")
        if title["kind"] != "tv":
            return redirect(f"/titles/{title_id}/tvdb", "Manual TVDB links currently support TV series")
        try:
            series_id = tvdb_series_id_from_reference(tvdb_reference)
            episode_count = store_tv_match(title_id, series_id)
        except (TVDBError, ValueError) as exc:
            return redirect(f"/titles/{title_id}/tvdb", str(exc))
        return match_success_redirect(
            title_id,
            f"Matched to TVDB {series_id} and loaded {episode_count} episodes",
            return_to, match_origin,
        )

    @librarian_post("/titles/{title_id}/unmatch")
    def unmatch_title(title_id: int):
        with db.connect() as conn:
            title = conn.execute("SELECT id FROM titles WHERE id=?", (title_id,)).fetchone()
            if not title:
                raise HTTPException(404, "Title not found")
            conn.execute("DELETE FROM expected_episodes WHERE title_id=?", (title_id,))
            conn.execute(
                """UPDATE titles SET tvdb_id=NULL, tvdb_movie_id=NULL, tmdb_id=NULL,
                   imdb_id=NULL, imdb_checked_at=NULL, genres=NULL,
                   imdb_title_type=NULL, imdb_rating=NULL, imdb_votes=NULL,
                   poster_url=NULL, metadata_title=NULL, metadata_year=NULL,
                   metadata_title_language=NULL,
                   metadata_end_year=NULL, metadata_continuing=NULL,
                   metadata_status=NULL, matched_at=NULL,
                   updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (title_id,),
            )
        return redirect(f"/titles/{title_id}", "Match metadata removed; media files were unchanged")

    @librarian_get("/titles/{title_id}/rename-folder", response_class=HTMLResponse)
    def rename_folder_preview(request: Request, title_id: int):
        with db.connect() as conn:
            title = conn.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
        if not title or not title["tvdb_id"]:
            return redirect(f"/titles/{title_id}", "Match this show to TVDB first")
        source = Path(title["folder_path"])
        continuing = title["metadata_continuing"] if title["metadata_continuing"] is not None else title["continuing"]
        new_name = plex_show_folder(
            title["metadata_title"] or title["title"], title["metadata_year"] or title["year"],
            title["tvdb_id"], title["metadata_end_year"] or title["end_year"], continuing,
        )
        destination = source.with_name(new_name)
        return templates.TemplateResponse(request, "rename.html", {
            "title": title, "source": source, "destination": destination,
            "action": f"/titles/{title_id}/rename-folder", "kind": "folder",
        })

    @librarian_post("/titles/{title_id}/rename-folder")
    def rename_folder(title_id: int, confirm: str = Form("")):
        if confirm != "RENAME":
            return redirect(f"/titles/{title_id}", "Rename cancelled: confirmation did not match")
        with db.connect() as conn:
            title = conn.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
            if not title or not title["tvdb_id"]:
                raise HTTPException(400, "TVDB match required")
            source = Path(title["folder_path"])
            continuing = title["metadata_continuing"] if title["metadata_continuing"] is not None else title["continuing"]
            new_name = plex_show_folder(
                title["metadata_title"] or title["title"], title["metadata_year"] or title["year"],
                title["tvdb_id"], title["metadata_end_year"] or title["end_year"], continuing,
            )
            destination = contained_destination(source, new_name)
            if destination == source:
                return redirect(f"/titles/{title_id}", "Folder already follows the Plex format")
            if destination.exists():
                return redirect(f"/titles/{title_id}", f"Destination already exists: {destination}")
            try:
                source.rename(destination)
            except OSError as exc:
                record_event(
                    "filesystem", f"Show folder could not be renamed: {source.name}.",
                    level="error", detail=str(exc),
                    context={"title_id": title_id, "source": str(source), "destination": str(destination)},
                )
                return redirect(
                    f"/titles/{title_id}",
                    "The show folder could not be renamed. Check that the folder still exists and InfoMancer has permission to change it, then try again. No catalog paths were changed.",
                )
            old_prefix, new_prefix = str(source), str(destination)
            conn.execute("UPDATE titles SET folder_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (new_prefix, title_id))
            rows = conn.execute("SELECT id, path FROM files WHERE title_id=?", (title_id,)).fetchall()
            for row in rows:
                new_path = new_prefix + row["path"][len(old_prefix):]
                conn.execute("UPDATE files SET path=? WHERE id=?", (new_path, row["id"]))
        record_event(
            "filesystem", f"Show folder renamed from {source.name} to {destination.name}.",
            context={"title_id": title_id, "source": str(source), "destination": str(destination)},
        )
        return redirect(f"/titles/{title_id}", "Show folder renamed")

    @librarian_get("/titles/{title_id}/rename-episodes", response_class=HTMLResponse)
    def bulk_rename_preview(request: Request, title_id: int):
        with db.connect() as conn:
            title, proposals = episode_rename_proposals(conn, title_id)
        if not title:
            raise HTTPException(404, "Title not found")
        if not title["tvdb_id"]:
            return redirect(f"/titles/{title_id}", "Match this show to TVDB first")
        return templates.TemplateResponse(request, "bulk_rename.html", {
            "title": title, "proposals": proposals,
            "ready": sum(item["status"] == "ready" for item in proposals),
            "conflicts": sum(item["status"] in {"conflict", "missing"} for item in proposals),
        })

    @librarian_post("/titles/{title_id}/rename-episodes")
    def bulk_rename_apply(
        title_id: int, selected_file_ids: list[int] = Form(default=[]),
    ):
        selected = set(selected_file_ids)
        if not selected:
            return redirect(
                f"/titles/{title_id}/rename-episodes",
                "Select at least one episode file to rename",
            )
        renamed = 0
        skipped = 0
        with db.connect() as conn:
            title, proposals = episode_rename_proposals(conn, title_id)
            if not title:
                raise HTTPException(404, "Title not found")
            for proposal in proposals:
                if proposal["file_id"] not in selected:
                    continue
                if proposal["status"] != "ready":
                    skipped += 1
                    continue
                source, destination = proposal["source"], proposal["destination"]
                try:
                    if destination.exists():
                        skipped += 1
                        continue
                    source.rename(destination)
                    conn.execute(
                        "UPDATE files SET path=?, filename=? WHERE id=?",
                        (str(destination), destination.name, proposal["file_id"]),
                    )
                    renamed += 1
                except OSError as exc:
                    skipped += 1
                    record_event(
                        "filesystem", f"Episode file could not be renamed: {source.name}.",
                        level="error", detail=str(exc),
                        context={"title_id": title_id, "source": str(source), "destination": str(destination)},
                    )
        message = f"Renamed {renamed} selected episode files"
        if skipped:
            message += f"; skipped {skipped} conflicts or missing files"
        record_event(
            "filesystem", message + ".",
            level="warning" if skipped else "info",
            context={"title_id": title_id, "renamed": renamed, "skipped": skipped},
        )
        return redirect(f"/titles/{title_id}", message)

    @librarian_get("/titles/{title_id}/restore-filenames", response_class=HTMLResponse)
    def restore_filenames_preview(request: Request, title_id: int):
        with db.connect() as conn:
            title, proposals = restore_filename_proposals(conn, title_id)
        if not title:
            raise HTTPException(404, "Title not found")
        return templates.TemplateResponse(request, "restore_filenames.html", {
            "title": title, "proposals": proposals,
            "ready": sum(item["status"] == "ready" for item in proposals),
            "conflicts": sum(item["status"] in {"conflict", "missing"} for item in proposals),
        })

    @librarian_post("/titles/{title_id}/restore-filenames")
    def restore_filenames_apply(title_id: int):
        restored = 0
        skipped = 0
        with db.connect() as conn:
            title, proposals = restore_filename_proposals(conn, title_id)
            if not title:
                raise HTTPException(404, "Title not found")
            for proposal in proposals:
                if proposal["status"] != "ready":
                    skipped += proposal["status"] != "unchanged"
                    continue
                try:
                    proposal["source"].rename(proposal["destination"])
                    conn.execute(
                        "UPDATE files SET path=?, filename=? WHERE id=?",
                        (str(proposal["destination"]), proposal["destination"].name,
                         proposal["file_id"]),
                    )
                    restored += 1
                except OSError as exc:
                    skipped += 1
                    record_event(
                        "filesystem",
                        f"Original filename could not be restored for {proposal['source'].name}.",
                        level="error", detail=str(exc),
                        context={"title_id": title_id, "source": str(proposal["source"])},
                    )
        message = f"Restored {restored} original filenames"
        if skipped:
            message += f"; skipped {skipped} conflicts or missing files"
        record_event(
            "filesystem", message + ".",
            level="warning" if skipped else "info",
            context={"title_id": title_id, "restored": restored, "skipped": skipped},
        )
        return redirect(f"/titles/{title_id}", message)

    @librarian_get("/files/{file_id}/rename", response_class=HTMLResponse)
    def rename_file_preview(request: Request, file_id: int):
        with db.connect() as conn:
            row = conn.execute(
                """SELECT f.*, t.title, t.metadata_title, t.year, t.metadata_year,
                   t.id matched_title_id FROM files f JOIN titles t ON t.id=f.title_id
                   WHERE f.id=?""", (file_id,)
            ).fetchone()
            episode_name = merged_episode_name(
                expected_name_map(conn, row["matched_title_id"]), row["season"],
                row["episode_start"], row["episode_end"],
            ) if row else ""
        if not row or row["season"] is None or row["episode_start"] is None:
            raise HTTPException(400, "This file has no parsed SxxExx identifier")
        new_name = plex_episode_filename(
            row["metadata_title"] or row["title"], row["metadata_year"] or row["year"],
            row["season"], row["episode_start"], episode_name, row["extension"],
            row["episode_end"],
        )
        source = Path(row["path"])
        return templates.TemplateResponse(request, "rename.html", {
            "title": row, "source": source, "destination": source.with_name(new_name),
            "action": f"/files/{file_id}/rename", "kind": "file",
        })

    @librarian_post("/files/{file_id}/rename")
    def rename_file(file_id: int):
        with db.connect() as conn:
            row = conn.execute(
                """SELECT f.*, t.title, t.metadata_title, t.year, t.metadata_year,
                   t.id matched_title_id FROM files f JOIN titles t ON t.id=f.title_id
                   WHERE f.id=?""", (file_id,)
            ).fetchone()
            if not row:
                raise HTTPException(404, "File not found")
            episode_name = merged_episode_name(
                expected_name_map(conn, row["matched_title_id"]), row["season"],
                row["episode_start"], row["episode_end"],
            )
            new_name = plex_episode_filename(
                row["metadata_title"] or row["title"], row["metadata_year"] or row["year"],
                row["season"], row["episode_start"], episode_name, row["extension"],
                row["episode_end"],
            )
            source = Path(row["path"])
            destination = contained_destination(source, new_name)
            if destination.exists() and destination != source:
                return redirect(f"/titles/{row['title_id']}", f"Destination already exists: {destination}")
            if destination != source:
                try:
                    source.rename(destination)
                except OSError as exc:
                    record_event(
                        "filesystem", f"Episode file could not be renamed: {source.name}.",
                        level="error", detail=str(exc),
                        context={"file_id": file_id, "source": str(source), "destination": str(destination)},
                    )
                    return redirect(
                        f"/titles/{row['title_id']}",
                        "The episode could not be renamed. Check that the file still exists and InfoMancer has permission to change it, then try again. The catalog was not changed.",
                    )
                conn.execute("UPDATE files SET path=?, filename=? WHERE id=?", (str(destination), destination.name, file_id))
                record_event(
                    "filesystem", f"Episode file renamed to {destination.name}.",
                    context={"file_id": file_id, "source": str(source), "destination": str(destination)},
                )
        return redirect(f"/titles/{row['title_id']}", "Episode renamed")

    @librarian_get("/files/{file_id}/rename-movie", response_class=HTMLResponse)
    def rename_movie_preview(request: Request, file_id: int):
        with db.connect() as conn:
            row = conn.execute(
                """SELECT f.*, t.title, t.metadata_title, t.year, t.metadata_year,
                   t.tmdb_id, t.imdb_id, t.kind title_kind, t.folder_path
                   FROM files f JOIN titles t ON t.id=f.title_id WHERE f.id=?""",
                (file_id,),
            ).fetchone()
        if not row or row["title_kind"] != "movie" or not (row["tmdb_id"] or row["imdb_id"]):
            raise HTTPException(400, "Match this movie before renaming it")
        new_name = plex_movie_filename(
            row["metadata_title"] or row["title"], row["metadata_year"] or row["year"],
            row["extension"], row["tmdb_id"] or "", row["imdb_id"] or "",
        )
        source = Path(row["path"])
        return templates.TemplateResponse(request, "rename.html", {
            "title": row, "source": source, "destination": source.with_name(new_name),
            "action": f"/files/{file_id}/rename-movie", "kind": "movie-file",
        })

    @librarian_post("/files/{file_id}/rename-movie")
    def rename_movie(file_id: int):
        with db.connect() as conn:
            row = conn.execute(
                """SELECT f.*, t.title, t.metadata_title, t.year, t.metadata_year,
                   t.tmdb_id, t.imdb_id, t.kind title_kind, t.folder_path
                   FROM files f JOIN titles t ON t.id=f.title_id WHERE f.id=?""",
                (file_id,),
            ).fetchone()
            if not row or row["title_kind"] != "movie":
                raise HTTPException(404, "Movie file not found")
            new_name = plex_movie_filename(
                row["metadata_title"] or row["title"], row["metadata_year"] or row["year"],
                row["extension"], row["tmdb_id"] or "", row["imdb_id"] or "",
            )
            source = Path(row["path"])
            destination = contained_destination(source, new_name)
            if destination.exists() and destination != source:
                return redirect(f"/titles/{row['title_id']}", f"Destination already exists: {destination}")
            if destination != source:
                try:
                    source.rename(destination)
                except OSError as exc:
                    record_event(
                        "filesystem", f"Movie file could not be renamed: {source.name}.",
                        level="error", detail=str(exc),
                        context={"file_id": file_id, "source": str(source), "destination": str(destination)},
                    )
                    return redirect(
                        f"/titles/{row['title_id']}",
                        "The movie could not be renamed. Check that the file still exists and InfoMancer has permission to change it, then try again. The catalog was not changed.",
                    )
                conn.execute(
                    "UPDATE files SET path=?, filename=? WHERE id=?",
                    (str(destination), destination.name, file_id),
                )
                if row["folder_path"] == str(source):
                    conn.execute(
                        "UPDATE titles SET folder_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (str(destination), row["title_id"]),
                    )
                record_event(
                    "filesystem", f"Movie file renamed to {destination.name}.",
                    context={"file_id": file_id, "source": str(source), "destination": str(destination)},
                )
        return redirect(f"/titles/{row['title_id']}", "Movie file renamed")

    return router, {
        "organize_title_page": organize_title_page,
        "save_title_organization": save_title_organization,
        "organize_titles_bulk": organize_titles_bulk,
        "sort_titles_dialog": sort_titles_dialog,
        "apply_sort_titles": apply_sort_titles,
        "edition_version_page": edition_version_page,
        "preview_edition_version": preview_edition_version,
        "save_edition_version": save_edition_version,
        "enrich_title_metadata": enrich_title_metadata,
        "title_detail": title_detail,
        "title_cover": title_cover,
        "update_title_cover": update_title_cover,
        "inspect_title_media": inspect_title_media,
        "tvdb_search": tvdb_search,
        "match_movie": match_movie,
        "match_tvdb": match_tvdb,
        "match_tvdb_manual": match_tvdb_manual,
        "unmatch_title": unmatch_title,
        "rename_folder_preview": rename_folder_preview,
        "rename_folder": rename_folder,
        "bulk_rename_preview": bulk_rename_preview,
        "bulk_rename_apply": bulk_rename_apply,
        "restore_filenames_preview": restore_filenames_preview,
        "restore_filenames_apply": restore_filenames_apply,
        "rename_file_preview": rename_file_preview,
        "rename_file": rename_file,
        "rename_movie_preview": rename_movie_preview,
        "rename_movie": rename_movie,
    }

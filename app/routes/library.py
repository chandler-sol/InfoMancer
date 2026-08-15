from fastapi import APIRouter, Depends

from ..access import require_librarian
from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    ElementTree = ctx.get("ElementTree")
    Form = ctx.get("Form")
    HTMLResponse = ctx.get("HTMLResponse")
    HTTPException = ctx.get("HTTPException")
    LIBRARY_EXPORT_FIELDS = ctx.get("LIBRARY_EXPORT_FIELDS")
    Request = ctx.get("Request")
    Response = ctx.get("Response")
    auth_error_response = ctx.live("auth_error_response")
    csv = ctx.live("csv")
    csv_safe_row = ctx.live("csv_safe_row")
    datetime = ctx.live("datetime")
    db = ctx.live("db")
    display_title_type = ctx.live("display_title_type")
    favorite_return_path = ctx.live("favorite_return_path")
    fuzzy_people = ctx.live("fuzzy_people")
    io = ctx.live("io")
    json = ctx.live("json")
    library_export_rows = ctx.live("library_export_rows")
    re = ctx.live("re")
    record_event = ctx.live("record_event")
    redirect = ctx.live("redirect")
    sqlite3 = ctx.live("sqlite3")
    templates = ctx.live("templates")
    timezone = ctx.live("timezone")
    title_return_path = ctx.live("title_return_path")
    urlencode = ctx.live("urlencode")

    def librarian_get(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.get(path, dependencies=dependencies, **kwargs)

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

    @router.get("/exports/library")
    def export_library(request: Request, format: str = "csv"):
        normalized = format.strip().casefold()
        if normalized not in {"csv", "json", "xml"}:
            return auth_error_response(
                request, 400, "Export format not supported",
                "Choose CSV, JSON, or XML, then try the export again.",
            )
        try:
            rows = library_export_rows(request.state.user.id)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            filename = f"infomancer-library-{stamp}.{normalized}"
            if normalized == "csv":
                output = io.StringIO(newline="")
                writer = csv.DictWriter(output, fieldnames=LIBRARY_EXPORT_FIELDS)
                writer.writeheader()
                writer.writerows(csv_safe_row(row) for row in rows)
                body = output.getvalue().encode("utf-8-sig")
                media_type = "text/csv; charset=utf-8"
            elif normalized == "json":
                body = json.dumps(
                    {"exported_at": datetime.now(timezone.utc).isoformat(), "items": rows},
                    ensure_ascii=False, indent=2,
                ).encode("utf-8")
                media_type = "application/json"
            else:
                root = ElementTree.Element(
                    "infomancer-library",
                    exported_at=datetime.now(timezone.utc).isoformat(),
                )
                for row in rows:
                    item = ElementTree.SubElement(root, "media-file")
                    for key, value in row.items():
                        field = ElementTree.SubElement(item, key.replace("_", "-"))
                        field.text = "" if value is None else str(value)
                body = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
                media_type = "application/xml"
        except (sqlite3.Error, OSError, ValueError) as exc:
            record_event(
                "export", "Library export could not be created.", level="error",
                detail=str(exc), user_id=request.state.user.id,
            )
            return auth_error_response(
                request, 500, "Library export could not be created",
                "InfoMancer could not read or format the catalog. Your library was not changed. Review Logs for the technical cause, then try again.",
            )
        record_event(
            "export", f"Library exported as {normalized.upper()}.",
            context={"rows": len(rows)}, user_id=request.state.user.id,
        )
        return Response(
            body, media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.post("/titles/{title_id}/favorite")
    def toggle_favorite(
        request: Request, title_id: int, return_to: str = Form(""),
    ):
        if request.state.user.id <= 0:
            return redirect(
                title_return_path(title_id, return_to),
                "Favorites require a signed-in user account so InfoMancer knows whose list to update.",
            )
        with db.connect() as conn:
            title = conn.execute(
                """SELECT id,COALESCE(NULLIF(metadata_title,''),title) name
                   FROM titles WHERE id=?""",
                (title_id,),
            ).fetchone()
            if not title:
                raise HTTPException(404, "Title not found")
            current = conn.execute(
                "SELECT favorite FROM user_title_state WHERE user_id=? AND title_id=?",
                (request.state.user.id, title_id),
            ).fetchone()
            favorite = not bool(current and current["favorite"])
            conn.execute(
                """INSERT INTO user_title_state(user_id,title_id,favorite,updated_at)
                   VALUES (?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(user_id,title_id) DO UPDATE SET
                     favorite=excluded.favorite,updated_at=CURRENT_TIMESTAMP""",
                (request.state.user.id, title_id, int(favorite)),
            )
        record_event(
            "library", "Title added to favorites." if favorite else "Title removed from favorites.",
            user_id=request.state.user.id, context={"title_id": title_id},
        )
        return redirect(
            title_return_path(title_id, return_to),
            (
                f'"{title["name"]}" has been added to favorites.'
                if favorite else f'"{title["name"]}" has been removed from favorites.'
            ),
        )

    @router.get("/favorites", response_class=HTMLResponse)
    def favorites_page(request: Request):
        if request.state.user.id <= 0:
            return templates.TemplateResponse(request, "favorites.html", {
                "favorite_titles": [], "favorite_episodes": [],
                "error": (
                    "Favorites need a signed-in account so InfoMancer can keep each "
                    "person's choices separate."
                ),
            })
        with db.connect() as conn:
            favorite_titles = conn.execute(
                """SELECT t.*,uts.updated_at favorite_updated_at
                   FROM user_title_state uts JOIN titles t ON t.id=uts.title_id
                   WHERE uts.user_id=? AND uts.favorite=1
                   ORDER BY COALESCE(NULLIF(t.metadata_title,''),t.title) COLLATE NOCASE""",
                (request.state.user.id,),
            ).fetchall()
            favorite_episodes = conn.execute(
                """SELECT uef.note,uef.updated_at,e.id expected_episode_id,
                          e.season,e.episode,e.name episode_name,
                          t.id title_id,COALESCE(NULLIF(t.metadata_title,''),t.title) show_name,
                          t.poster_url,
                          (SELECT MIN(f.id) FROM files f
                           WHERE f.title_id=e.title_id AND f.season=e.season
                             AND e.episode BETWEEN f.episode_start
                               AND COALESCE(f.episode_end,f.episode_start)) file_id
                   FROM user_episode_favorites uef
                   JOIN expected_episodes e ON e.id=uef.expected_episode_id
                   JOIN titles t ON t.id=e.title_id
                   WHERE uef.user_id=?
                   ORDER BY show_name COLLATE NOCASE,e.season,e.episode""",
                (request.state.user.id,),
            ).fetchall()
        return templates.TemplateResponse(request, "favorites.html", {
            "favorite_titles": favorite_titles,
            "favorite_episodes": favorite_episodes,
            "error": "",
            "message": request.query_params.get("message", ""),
        })

    @router.get("/files/{file_id}/favorite", response_class=HTMLResponse)
    def episode_favorite_page(request: Request, file_id: int):
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
                """SELECT e.id,e.season,e.episode,e.name,uef.note,
                          CASE WHEN uef.expected_episode_id IS NULL THEN 0 ELSE 1 END favorite
                   FROM expected_episodes e
                   LEFT JOIN user_episode_favorites uef
                     ON uef.expected_episode_id=e.id AND uef.user_id=?
                   WHERE e.title_id=? AND e.season=?
                     AND e.episode BETWEEN ? AND ?
                   ORDER BY e.episode""",
                (
                    request.state.user.id, file_row["title_id"], file_row["season"],
                    file_row["episode_start"], final_episode,
                ),
            ).fetchall()
        return templates.TemplateResponse(request, "episode_favorite.html", {
            "file": file_row, "episodes": episodes,
            "message": request.query_params.get("message", ""),
        })

    @router.post("/files/{file_id}/favorite")
    async def save_episode_favorite(request: Request, file_id: int):
        if request.state.user.id <= 0:
            return redirect(
                "/shows",
                "Episode favorites need a signed-in account so InfoMancer knows whose list to update.",
            )
        form = await request.form()
        selected = {
            int(value) for value in form.getlist("selected")
            if str(value).isdigit()
        }
        with db.connect() as conn:
            file_row = conn.execute(
                """SELECT f.id,f.title_id,f.season,f.episode_start,f.episode_end
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
            selected &= episode_ids
            for episode_id in episode_ids:
                if episode_id not in selected:
                    conn.execute(
                        """DELETE FROM user_episode_favorites
                           WHERE user_id=? AND expected_episode_id=?""",
                        (request.state.user.id, episode_id),
                    )
                    continue
                note = str(form.get(f"note_{episode_id}", "")).strip()[:1000]
                conn.execute(
                    """INSERT INTO user_episode_favorites(
                         user_id,expected_episode_id,note,updated_at
                       ) VALUES (?,?,?,CURRENT_TIMESTAMP)
                       ON CONFLICT(user_id,expected_episode_id) DO UPDATE SET
                         note=excluded.note,updated_at=CURRENT_TIMESTAMP""",
                    (request.state.user.id, episode_id, note),
                )
        record_event(
            "library", "Episode favorites updated.",
            user_id=request.state.user.id,
            context={"file_id": file_id, "favorite_episode_count": len(selected)},
        )
        return redirect(
            favorite_return_path(file_row),
            (
                f"Saved {len(selected)} episode favorite"
                f"{'' if len(selected) == 1 else 's'}."
            ),
        )

    @router.get("/tags", response_class=HTMLResponse)
    def manage_tags(request: Request):
        with db.connect() as conn:
            tags = conn.execute(
                """SELECT ut.*,COUNT(tt.title_id) usage_count
                   FROM user_tags ut LEFT JOIN title_tags tt ON tt.tag_id=ut.id
                   WHERE ut.user_id=? GROUP BY ut.id ORDER BY ut.name COLLATE NOCASE""",
                (request.state.user.id,),
            ).fetchall()
        return templates.TemplateResponse(request, "tags.html", {
            "tags": tags, "message": request.query_params.get("message", ""),
        })

    @router.post("/tags/create")
    def create_tag(request: Request, name: str = Form(...)):
        cleaned = " ".join(name.strip().split())[:40]
        if not cleaned:
            return redirect("/tags", "Enter a tag name before creating it.")
        try:
            with db.connect() as conn:
                conn.execute(
                    "INSERT INTO user_tags(user_id,name) VALUES (?,?)",
                    (request.state.user.id, cleaned),
                )
        except sqlite3.IntegrityError:
            return redirect(
                "/tags",
                f'The tag "{cleaned}" already exists. Choose a different name or use the existing tag.',
            )
        return redirect("/tags", f'Tag "{cleaned}" created.')

    @router.post("/tags/{tag_id}/rename")
    def rename_tag(request: Request, tag_id: int, name: str = Form(...)):
        cleaned = " ".join(name.strip().split())[:40]
        if not cleaned:
            return redirect("/tags", "Tag name was not changed because the new name was empty.")
        try:
            with db.connect() as conn:
                result = conn.execute(
                    "UPDATE user_tags SET name=? WHERE id=? AND user_id=?",
                    (cleaned, tag_id, request.state.user.id),
                )
                if not result.rowcount:
                    return redirect("/tags", "That tag could not be found in your account.")
        except sqlite3.IntegrityError:
            return redirect(
                "/tags",
                f'The tag "{cleaned}" already exists. Merge titles into that tag or choose another name.',
            )
        return redirect("/tags", f'Tag renamed to "{cleaned}".')

    @router.post("/tags/{tag_id}/delete")
    def delete_tag(request: Request, tag_id: int):
        with db.connect() as conn:
            tag = conn.execute(
                "SELECT name FROM user_tags WHERE id=? AND user_id=?",
                (tag_id, request.state.user.id),
            ).fetchone()
            if not tag:
                return redirect("/tags", "That tag could not be found in your account.")
            conn.execute(
                "DELETE FROM user_tags WHERE id=? AND user_id=?",
                (tag_id, request.state.user.id),
            )
        return redirect(
            "/tags",
            f'Tag "{tag["name"]}" deleted. Movies and TV series were not removed.',
        )

    @router.get("/library", response_class=HTMLResponse)
    def library(
        request: Request, q: str = "", kind: str = "all", letter: str = "",
        genre: str = "", title_type: str = "", root: str = "",
        person: str = "", person_name: str = "", credit_role: str = "",
        match: str = "", gaps: str = "", favorite: str = "", tag: str = "",
        sort: str = "title", record_search: str = "",
    ):
        q = q.strip()[:200]
        if q and record_search == "1" and request.state.user.id > 0:
            with db.connect() as conn:
                conn.execute(
                    """INSERT INTO user_search_history(user_id,query,searched_at)
                       VALUES (?,?,CURRENT_TIMESTAMP)
                       ON CONFLICT(user_id,query) DO UPDATE SET
                         query=excluded.query,searched_at=CURRENT_TIMESTAMP""",
                    (request.state.user.id, q),
                )
                conn.execute(
                    """DELETE FROM user_search_history
                       WHERE user_id=? AND id NOT IN (
                         SELECT id FROM user_search_history WHERE user_id=?
                         ORDER BY searched_at DESC,id DESC LIMIT 10
                       )""",
                    (request.state.user.id, request.state.user.id),
                )
        conditions, params = [], []
        root_id = int(root) if root.isdigit() else None
        person_id = person if re.fullmatch(r"nm\d+", person) else ""
        person_name = person_name.strip()
        credit_role = credit_role if credit_role in {"actor", "director", "writer"} else ""
        match_status = match if match in {"matched", "unmatched"} else ""
        gap_status = gaps if kind != "movie" and gaps in {"missing", "complete"} else ""
        favorite_status = "favorites" if favorite == "favorites" else ""
        tag_id = int(tag) if tag.isdigit() else None
        sort_key = sort if sort in {
            "title", "release_new", "release_old", "rating", "personal_rating",
            "date_added", "runtime", "resolution", "bitrate", "file_size",
            "favorites", "random",
        } else "title"
        if q:
            fuzzy_names = [item["person_name"] for item in fuzzy_people(q, kind, 6)]
            fuzzy_credit_sql = ""
            if fuzzy_names:
                placeholders = ",".join("?" for _ in fuzzy_names)
                fuzzy_credit_sql = (
                    " OR EXISTS (SELECT 1 FROM title_credits fuzzy_credit "
                    "WHERE fuzzy_credit.title_id=t.id "
                    f"AND fuzzy_credit.person_name IN ({placeholders}))"
                )
            conditions.append(
                "(t.title LIKE ? OR t.metadata_title LIKE ? OR EXISTS "
                "(SELECT 1 FROM files qf WHERE qf.title_id=t.id AND qf.filename LIKE ?) "
                "OR EXISTS ("
                "SELECT 1 FROM title_tags qtt "
                "JOIN user_tags qut ON qut.id=qtt.tag_id "
                "WHERE qtt.title_id=t.id AND qut.user_id=? AND qut.name LIKE ?"
                ") OR EXISTS ("
                "SELECT 1 FROM title_credits qtc "
                "WHERE qtc.title_id=t.id AND qtc.person_name LIKE ?"
                ") OR EXISTS ("
                "SELECT 1 FROM expected_episodes qee "
                "JOIN episode_credits qec ON qec.expected_episode_id=qee.id "
                "WHERE qee.title_id=t.id AND qec.person_name LIKE ?"
                ")"
                + fuzzy_credit_sql
                + ")"
            )
            term = f"%{q}%"
            params.extend([
                term, term, term, request.state.user.id, term, term, term,
            ])
            params.extend(fuzzy_names)
        if kind in {"movie", "tv"}:
            conditions.append("t.kind=?")
            params.append(kind)
        if genre:
            conditions.append("INSTR(',' || LOWER(COALESCE(t.genres,'')) || ',', ?) > 0")
            params.append(f",{genre.lower()},")
        if title_type:
            conditions.append("t.imdb_title_type=?")
            params.append(title_type)
        if root_id is not None:
            conditions.append("t.root_id=?")
            params.append(root_id)
        matched_condition = (
            "((t.kind='tv' AND t.tvdb_id IS NOT NULL) OR "
            "(t.kind='movie' AND (t.tvdb_movie_id IS NOT NULL OR "
            "t.tmdb_id IS NOT NULL OR t.imdb_id IS NOT NULL)))"
        )
        if match_status == "matched":
            conditions.append(matched_condition)
        elif match_status == "unmatched":
            conditions.append(f"NOT {matched_condition}")
        if gap_status == "missing":
            conditions.append("t.kind='tv' AND COALESCE(ms.missing_count,0) > 0")
        elif gap_status == "complete":
            conditions.append(
                "t.kind='tv' AND t.tvdb_id IS NOT NULL "
                "AND COALESCE(ms.missing_count,0) = 0"
            )
        if person_id or person_name:
            credit_conditions = ["c.title_id=t.id"]
            if person_id:
                credit_conditions.append("c.imdb_person_id=?")
                params.append(person_id)
            else:
                credit_conditions.append("c.person_name LIKE ?")
                params.append(f"%{person_name}%")
            if credit_role:
                credit_conditions.append("c.role=?")
                params.append(credit_role)
            conditions.append(
                "EXISTS (SELECT 1 FROM title_credits c WHERE "
                + " AND ".join(credit_conditions) + ")"
            )
        if favorite_status:
            conditions.append("COALESCE(uts.favorite,0)=1")
        if tag_id is not None:
            conditions.append(
                "EXISTS (SELECT 1 FROM title_tags filtered_tag "
                "JOIN user_tags filtered_user_tag ON filtered_user_tag.id=filtered_tag.tag_id "
                "WHERE filtered_tag.title_id=t.id AND filtered_tag.tag_id=? "
                "AND filtered_user_tag.user_id=?)"
            )
            params.extend([tag_id, request.state.user.id])
        title_sort_base = "COALESCE(NULLIF(uts.sort_title,''),NULLIF(t.metadata_title,''),t.title)"
        title_sort_sql = (
            f"CASE WHEN LOWER({title_sort_base}) LIKE 'the %' THEN SUBSTR({title_sort_base},5) "
            f"WHEN LOWER({title_sort_base}) LIKE 'an %' THEN SUBSTR({title_sort_base},4) "
            f"WHEN LOWER({title_sort_base}) LIKE 'a %' THEN SUBSTR({title_sort_base},3) "
            f"ELSE {title_sort_base} END"
        )
        title_order = f"{title_sort_sql} COLLATE NOCASE, COALESCE(NULLIF(t.metadata_title,''),t.title) COLLATE NOCASE"
        normalized_letter = letter.upper() if letter else ""
        if normalized_letter == "#":
            conditions.append(f"{title_sort_sql} GLOB '[0-9]*'")
        elif len(normalized_letter) == 1 and normalized_letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            conditions.append(f"UPPER(SUBSTR({title_sort_sql},1,1))=?")
            params.append(normalized_letter)
        else:
            normalized_letter = ""
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        sort_sql = {
            "title": title_order,
            "release_new": f"COALESCE(t.metadata_year,t.year,0) DESC, {title_order}",
            "release_old": f"COALESCE(t.metadata_year,t.year,9999), {title_order}",
            "rating": f"t.imdb_rating IS NULL, t.imdb_rating DESC, {title_order}",
            "personal_rating": f"uts.personal_rating IS NULL, uts.personal_rating DESC, {title_order}",
            "date_added": "t.discovered_at IS NULL, t.discovered_at DESC, t.id DESC",
            "runtime": f"COALESCE(fs.runtime_seconds,0) DESC, {title_order}",
            "resolution": f"COALESCE(fs.resolution_pixels,0) DESC, {title_order}",
            "bitrate": f"COALESCE(fs.max_bitrate,0) DESC, {title_order}",
            "file_size": f"COALESCE(fs.bytes,0) DESC, {title_order}",
            "favorites": f"COALESCE(uts.favorite,0) DESC, {title_order}",
            "random": "RANDOM()",
        }[sort_key]
        with db.connect() as conn:
            option_conditions = ["(genres IS NOT NULL OR imdb_title_type IS NOT NULL)"]
            option_params: list = []
            if kind in {"movie", "tv"}:
                option_conditions.append("kind=?")
                option_params.append(kind)
            if root_id is not None:
                option_conditions.append("root_id=?")
                option_params.append(root_id)
            option_condition = "WHERE " + " AND ".join(option_conditions)
            metadata_options = conn.execute(
                f"SELECT genres, imdb_title_type FROM titles {option_condition}",
                option_params,
            ).fetchall()
            root_options = conn.execute(
                "SELECT id, label, path, kind FROM roots WHERE enabled=1 ORDER BY kind, label, path"
            ).fetchall()
            selected_person = None
            if person_id:
                selected_person = conn.execute(
                    """SELECT imdb_person_id, person_name FROM title_credits
                       WHERE imdb_person_id=? ORDER BY person_name LIMIT 1""",
                    (person_id,),
                ).fetchone()
            genre_options = sorted({
                value for row in metadata_options for value in (row["genres"] or "").split(",")
                if value
            })
            title_type_options = sorted({
                row["imdb_title_type"] for row in metadata_options if row["imdb_title_type"]
            }, key=display_title_type)
            tag_options = conn.execute(
                """SELECT ut.id,ut.name,ut.color,COUNT(tt.title_id) title_count
                   FROM user_tags ut LEFT JOIN title_tags tt ON tt.tag_id=ut.id
                   WHERE ut.user_id=? GROUP BY ut.id ORDER BY ut.name COLLATE NOCASE""",
                (request.state.user.id,),
            ).fetchall()
            rows = conn.execute(
                f"""WITH file_stats AS (
                      SELECT title_id, COUNT(*) file_count, COALESCE(SUM(size_bytes),0) bytes,
                        MIN(id) first_file_id,
                        SUM(runtime_seconds) runtime_seconds,
                        MAX(COALESCE(width,0) * COALESCE(height,0)) resolution_pixels,
                        MAX(bitrate) max_bitrate,
                        COALESCE(SUM(CASE WHEN season IS NOT NULL AND episode_start IS NOT NULL
                          THEN COALESCE(episode_end, episode_start) - episode_start + 1
                          ELSE 0 END), 0) episode_count
                      FROM files GROUP BY title_id
                    ), missing_stats AS (
                      SELECT e.title_id, COUNT(*) missing_count
                      FROM expected_episodes e
                      WHERE e.season > 0 AND (e.aired IS NULL OR e.aired <= date('now'))
                        AND NOT EXISTS (
                          SELECT 1 FROM files owned
                          WHERE owned.title_id=e.title_id AND owned.season=e.season
                            AND e.episode BETWEEN owned.episode_start
                              AND COALESCE(owned.episode_end, owned.episode_start)
                        )
                      GROUP BY e.title_id
                    )
                    SELECT t.*, COALESCE(fs.file_count,0) file_count,
                      COALESCE(fs.bytes,0) bytes, fs.first_file_id,
                      fs.runtime_seconds,fs.resolution_pixels,fs.max_bitrate,
                      COALESCE(fs.episode_count,0) episode_count,
                      COALESCE(ms.missing_count,0) missing_count,
                      COALESCE(uts.favorite,0) favorite,
                      uts.personal_rating,uts.custom_order,uts.sort_title,
                      (SELECT GROUP_CONCAT(ut.name, ', ')
                       FROM title_tags tt JOIN user_tags ut ON ut.id=tt.tag_id
                       WHERE tt.title_id=t.id AND ut.user_id=?) custom_tags
                    FROM titles t
                    LEFT JOIN file_stats fs ON fs.title_id=t.id
                    LEFT JOIN missing_stats ms ON ms.title_id=t.id
                    LEFT JOIN user_title_state uts
                      ON uts.title_id=t.id AND uts.user_id=?
                    {where}
                    ORDER BY {sort_sql} LIMIT 1000""",
                [request.state.user.id, request.state.user.id, *params],
            ).fetchall()
        return templates.TemplateResponse(request, "library.html", {
            "rows": rows, "q": q, "kind": kind, "letter": normalized_letter,
            "genre": genre, "title_type": title_type, "root_id": root_id,
            "match_status": match_status, "gap_status": gap_status,
            "favorite_status": favorite_status, "tag_id": tag_id, "sort_key": sort_key,
            "tag_options": tag_options,
            "root_options": root_options,
            "selected_root": next((item for item in root_options if item["id"] == root_id), None),
            "person_id": person_id, "person_name": (
                selected_person["person_name"] if selected_person else person_name
            ),
            "credit_role": credit_role,
            "genre_options": genre_options, "title_type_options": title_type_options,
            "filter_query": urlencode({
                key: value for key, value in {
                    "q": q, "genre": genre, "title_type": title_type, "root": root_id,
                    "person": person_id, "person_name": person_name,
                    "credit_role": credit_role, "match": match_status,
                    "gaps": gap_status,
                    "favorite": favorite_status, "tag": tag_id, "sort": sort_key,
                }.items() if value
            }),
            "source_query": urlencode({
                key: value for key, value in {
                    "q": q, "genre": genre, "title_type": title_type,
                    "root": root_id, "person": person_id,
                    "person_name": (
                        selected_person["person_name"] if selected_person else person_name
                    ),
                    "credit_role": credit_role, "match": match_status,
                    "gaps": gap_status,
                    "favorite": favorite_status, "tag": tag_id, "sort": sort_key,
                }.items() if value
            }),
            "heading": {"movie": "Movies", "tv": "TV Shows"}.get(kind, "Library"),
            "message": request.query_params.get("message", ""),
        })

    def workspace_inspector_context(request: Request, title_id: int) -> dict:
        """Build the read-only Workspace Inspector from catalog state only."""
        with db.connect() as conn:
            title_row = conn.execute(
                """SELECT t.*,r.label source_label,r.path source_path,
                          r.health_status source_health,r.last_scanned_at source_scanned_at,
                          COALESCE(uts.favorite,0) favorite,uts.personal_rating,uts.sort_title
                   FROM titles t JOIN roots r ON r.id=t.root_id
                   LEFT JOIN user_title_state uts
                     ON uts.title_id=t.id AND uts.user_id=?
                   WHERE t.id=?""",
                (request.state.user.id, title_id),
            ).fetchone()
            if not title_row:
                raise HTTPException(404, "Title not found")
            title = dict(title_row)
            file_rows = conn.execute(
                """SELECT * FROM files WHERE title_id=?
                   ORDER BY version_preferred DESC,identity_confirmed DESC,id
                   LIMIT 12""",
                (title_id,),
            ).fetchall()
            file_totals = conn.execute(
                """SELECT COUNT(*) file_count,
                          COALESCE(SUM(size_bytes),0) total_size,
                          COALESCE(SUM(runtime_seconds),0) total_runtime
                   FROM files WHERE title_id=?""",
                (title_id,),
            ).fetchone()
            tags = conn.execute(
                """SELECT ut.id,ut.name,ut.color,
                          CASE WHEN tt.title_id IS NULL THEN 0 ELSE 1 END selected
                   FROM user_tags ut LEFT JOIN title_tags tt
                     ON tt.tag_id=ut.id AND tt.title_id=?
                   WHERE ut.user_id=? ORDER BY ut.name COLLATE NOCASE""",
                (title_id, request.state.user.id),
            ).fetchall() if request.state.user.id > 0 else []
            collections = conn.execute(
                """SELECT c.id,c.name FROM collections c
                   JOIN collection_titles ct ON ct.collection_id=c.id
                   WHERE ct.title_id=? ORDER BY c.name COLLATE NOCASE""",
                (title_id,),
            ).fetchall()
            libraries = conn.execute(
                """SELECT l.id,l.name FROM custom_libraries l
                   JOIN custom_library_titles lt ON lt.library_id=l.id
                   WHERE lt.title_id=? ORDER BY l.name COLLATE NOCASE""",
                (title_id,),
            ).fetchall()
            findings = conn.execute(
                """SELECT id,severity,category,summary,recommendation,last_seen_at
                   FROM mie_findings WHERE title_id=? AND status='active'
                   ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                            last_seen_at DESC LIMIT 5""",
                (title_id,),
            ).fetchall()
            finding_counts = conn.execute(
                """SELECT COUNT(*) total,
                          SUM(CASE WHEN severity='critical' THEN 1 ELSE 0 END) critical,
                          SUM(CASE WHEN severity='warning' THEN 1 ELSE 0 END) warning
                   FROM mie_findings WHERE title_id=? AND status='active'""",
                (title_id,),
            ).fetchone()
            missing_count = conn.execute(
                """SELECT COUNT(*) count FROM expected_episodes e
                   WHERE e.title_id=? AND e.season>0
                     AND (e.aired IS NULL OR e.aired<=date('now'))
                     AND NOT EXISTS (
                       SELECT 1 FROM files owned WHERE owned.title_id=e.title_id
                         AND owned.season=e.season
                         AND e.episode BETWEEN owned.episode_start
                           AND COALESCE(owned.episode_end,owned.episode_start)
                     )""",
                (title_id,),
            ).fetchone()["count"] if title["kind"] == "tv" else 0
            duplicate_count = conn.execute(
                """SELECT COUNT(*) count FROM duplicate_reviews dr
                   JOIN files a ON a.id=dr.file_a_id
                   JOIN files b ON b.id=dr.file_b_id
                   WHERE dr.decision='active' AND (a.title_id=? OR b.title_id=?)""",
                (title_id, title_id),
            ).fetchone()["count"]
            metadata_queue = conn.execute(
                "SELECT status,provider,error,requested_at,completed_at FROM metadata_refresh_queue WHERE title_id=?",
                (title_id,),
            ).fetchone()

        def size_label(value: int | float | None) -> str:
            amount = float(value or 0)
            units = ("B", "KB", "MB", "GB", "TB", "PB")
            unit = units[0]
            for candidate in units:
                unit = candidate
                if amount < 1024 or candidate == units[-1]:
                    break
                amount /= 1024
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.1f} {unit}"

        def runtime_label(value: int | float | None) -> str:
            seconds = int(value or 0)
            if seconds <= 0:
                return ""
            minutes = max(1, round(seconds / 60))
            hours, minutes = divmod(minutes, 60)
            return f"{hours}h {minutes}m" if hours else f"{minutes}m"

        files = []
        total_size = 0
        total_runtime = 0.0
        for row in file_rows:
            item = dict(row)
            total_size += int(item.get("size_bytes") or 0)
            total_runtime += float(item.get("runtime_seconds") or 0)
            item["size_display"] = size_label(item.get("size_bytes"))
            item["runtime_display"] = runtime_label(item.get("runtime_seconds"))
            width, height = item.get("width"), item.get("height")
            item["resolution_display"] = f"{width}×{height}" if width and height else ""
            files.append(item)
        primary = files[0] if files else None
        matched = bool(
            title.get("tvdb_id") if title["kind"] == "tv"
            else title.get("tvdb_movie_id") or title.get("tmdb_id") or title.get("imdb_id")
        )
        provider_ids = []
        for label, value in (
            ("TVDB", title.get("tvdb_id") or title.get("tvdb_movie_id")),
            ("TMDB", title.get("tmdb_id")),
            ("IMDb", title.get("imdb_id")),
        ):
            if value:
                provider_ids.append({"label": label, "value": str(value)})
        return {
            "title": title,
            "display_title": title.get("metadata_title") or title.get("title") or "Untitled",
            "display_year": title.get("metadata_year") or title.get("year"),
            "matched": matched,
            "provider_ids": provider_ids,
            "files": files,
            "primary_file": primary,
            "file_count": int(file_totals["file_count"] or 0),
            "total_size_display": size_label(file_totals["total_size"]),
            "runtime_display": (
                primary["runtime_display"] if title["kind"] == "movie" and primary
                else runtime_label(file_totals["total_runtime"])
            ),
            "tags": tags,
            "collections": collections,
            "libraries": libraries,
            "findings": findings,
            "finding_counts": dict(finding_counts) if finding_counts else {"total": 0, "critical": 0, "warning": 0},
            "missing_count": missing_count,
            "duplicate_count": duplicate_count,
            "metadata_queue": dict(metadata_queue) if metadata_queue else None,
            "message": "",
        }

    @router.get("/library/inspector/{title_id}", response_class=HTMLResponse)
    def workspace_inspector(request: Request, title_id: int):
        response = templates.TemplateResponse(
            request, "_workspace_inspector.html",
            workspace_inspector_context(request, title_id),
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.post("/api/titles/{title_id}/favorite")
    def workspace_toggle_favorite(request: Request, title_id: int) -> dict:
        if request.state.user.id <= 0:
            raise HTTPException(403, "Favorites require a signed-in account")
        with db.connect() as conn:
            title = conn.execute(
                "SELECT id,COALESCE(NULLIF(metadata_title,''),title) name FROM titles WHERE id=?",
                (title_id,),
            ).fetchone()
            if not title:
                raise HTTPException(404, "Title not found")
            current = conn.execute(
                "SELECT favorite FROM user_title_state WHERE user_id=? AND title_id=?",
                (request.state.user.id, title_id),
            ).fetchone()
            favorite = not bool(current and current["favorite"])
            conn.execute(
                """INSERT INTO user_title_state(user_id,title_id,favorite,updated_at)
                   VALUES (?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(user_id,title_id) DO UPDATE SET
                     favorite=excluded.favorite,updated_at=CURRENT_TIMESTAMP""",
                (request.state.user.id, title_id, int(favorite)),
            )
        record_event(
            "library", "Title added to favorites." if favorite else "Title removed from favorites.",
            user_id=request.state.user.id, context={"title_id": title_id, "source": "workspace-inspector"},
        )
        return {"title_id": title_id, "favorite": favorite}

    @router.post("/api/titles/{title_id}/tags/{tag_id}")
    def workspace_toggle_tag(request: Request, title_id: int, tag_id: int) -> dict:
        if request.state.user.id <= 0:
            raise HTTPException(403, "Tags require a signed-in account")
        with db.connect() as conn:
            if not conn.execute("SELECT id FROM titles WHERE id=?", (title_id,)).fetchone():
                raise HTTPException(404, "Title not found")
            tag = conn.execute(
                "SELECT id,name FROM user_tags WHERE id=? AND user_id=?",
                (tag_id, request.state.user.id),
            ).fetchone()
            if not tag:
                raise HTTPException(404, "Tag not found")
            existing = conn.execute(
                "SELECT 1 FROM title_tags WHERE title_id=? AND tag_id=?",
                (title_id, tag_id),
            ).fetchone()
            selected = not bool(existing)
            if selected:
                conn.execute(
                    "INSERT OR IGNORE INTO title_tags(title_id,tag_id) VALUES (?,?)",
                    (title_id, tag_id),
                )
            else:
                conn.execute(
                    "DELETE FROM title_tags WHERE title_id=? AND tag_id=?",
                    (title_id, tag_id),
                )
        record_event(
            "library", f'Tag "{tag["name"]}" {"added to" if selected else "removed from"} title.',
            user_id=request.state.user.id,
            context={"title_id": title_id, "tag_id": tag_id, "source": "workspace-inspector"},
        )
        return {"title_id": title_id, "tag_id": tag_id, "selected": selected}

    @router.get("/movies", response_class=HTMLResponse)
    def movies(
        request: Request, q: str = "", letter: str = "",
        genre: str = "", title_type: str = "", root: str = "",
        person: str = "", person_name: str = "", credit_role: str = "",
        match: str = "", gaps: str = "", favorite: str = "", tag: str = "",
        sort: str = "title",
    ):
        return library(
            request, q, "movie", letter, genre, title_type, root,
            person, person_name, credit_role, match, gaps, favorite, tag, sort,
        )

    @router.get("/shows", response_class=HTMLResponse)
    def shows(
        request: Request, q: str = "", letter: str = "",
        genre: str = "", title_type: str = "", root: str = "",
        person: str = "", person_name: str = "", credit_role: str = "",
        match: str = "", gaps: str = "", favorite: str = "", tag: str = "",
        sort: str = "title",
    ):
        return library(
            request, q, "tv", letter, genre, title_type, root,
            person, person_name, credit_role, match, gaps, favorite, tag, sort,
        )

    @router.get("/api/people")
    def people_search(q: str = "", role: str = "", kind: str = "") -> dict:
        query = q.strip()
        if len(query) < 2:
            return {"people": []}
        role = role if role in {"actor", "director", "writer"} else ""
        conditions = ["c.person_name LIKE ?"]
        params: list = [f"%{query}%"]
        if kind in {"movie", "tv"}:
            conditions.append("t.kind=?")
            params.append(kind)
        if role:
            conditions.append("c.role=?")
            params.append(role)
        params.extend([query, f"{query}%"])
        with db.connect() as conn:
            rows = conn.execute(
                f"""SELECT c.imdb_person_id, c.person_name,
                           GROUP_CONCAT(DISTINCT c.role) roles,
                           COUNT(DISTINCT c.title_id) title_count
                    FROM title_credits c JOIN titles t ON t.id=c.title_id
                    WHERE {' AND '.join(conditions)}
                    GROUP BY c.imdb_person_id, c.person_name
                    ORDER BY CASE WHEN c.person_name=? COLLATE NOCASE THEN 0
                                  WHEN c.person_name LIKE ? THEN 1 ELSE 2 END,
                             title_count DESC, c.person_name COLLATE NOCASE
                    LIMIT 10""",
                params,
            ).fetchall()
        people = [dict(row) for row in rows]
        seen = {
            (item.get("imdb_person_id") or "", item["person_name"].casefold())
            for item in people
        }
        for item in fuzzy_people(query, kind, 10):
            key = (item.get("imdb_person_id") or "", item["person_name"].casefold())
            if key not in seen:
                item.pop("similarity", None)
                people.append(item)
                seen.add(key)
            if len(people) >= 10:
                break
        return {"people": people[:10]}

    @router.get("/api/library-suggestions")
    def library_suggestions(request: Request, q: str = "", kind: str = "all") -> dict:
        """Suggest searchable values already present in this installation."""
        query = q.strip()
        if len(query) < 2:
            return {"suggestions": []}
        kind = kind if kind in {"movie", "tv"} else "all"
        term = f"%{query}%"
        prefix = f"{query}%"
        kind_sql = " AND t.kind=?" if kind != "all" else ""

        with db.connect() as conn:
            title_rows = conn.execute(
                f"""SELECT DISTINCT COALESCE(NULLIF(t.metadata_title,''),t.title) label,
                           COALESCE(t.metadata_year,t.year) year, t.kind
                    FROM titles t
                    WHERE COALESCE(NULLIF(t.metadata_title,''),t.title) LIKE ?{kind_sql}
                    ORDER BY CASE
                      WHEN COALESCE(NULLIF(t.metadata_title,''),t.title)=? COLLATE NOCASE THEN 0
                      WHEN COALESCE(NULLIF(t.metadata_title,''),t.title) LIKE ? THEN 1
                      ELSE 2 END,
                      label COLLATE NOCASE
                    LIMIT 5""",
                [term, *([kind] if kind != "all" else []), query, prefix],
            ).fetchall()
            people_rows = conn.execute(
                f"""SELECT c.person_name label, GROUP_CONCAT(DISTINCT c.role) roles,
                           COUNT(DISTINCT c.title_id) title_count
                    FROM title_credits c JOIN titles t ON t.id=c.title_id
                    WHERE c.person_name LIKE ?{kind_sql}
                    GROUP BY c.person_name
                    ORDER BY CASE WHEN c.person_name=? COLLATE NOCASE THEN 0
                                  WHEN c.person_name LIKE ? THEN 1 ELSE 2 END,
                             title_count DESC, c.person_name COLLATE NOCASE
                    LIMIT 4""",
                [term, *([kind] if kind != "all" else []), query, prefix],
            ).fetchall()
            file_rows = conn.execute(
                f"""SELECT f.filename label, t.kind
                    FROM files f JOIN titles t ON t.id=f.title_id
                    WHERE f.filename LIKE ?{kind_sql}
                    ORDER BY CASE WHEN f.filename LIKE ? THEN 0 ELSE 1 END,
                             f.filename COLLATE NOCASE
                    LIMIT 3""",
                [term, *([kind] if kind != "all" else []), prefix],
            ).fetchall()
            tag_rows = conn.execute(
                """SELECT ut.name label, COUNT(DISTINCT tt.title_id) title_count
                   FROM user_tags ut LEFT JOIN title_tags tt ON tt.tag_id=ut.id
                   WHERE ut.user_id=? AND ut.name LIKE ?
                   GROUP BY ut.id
                   ORDER BY CASE WHEN ut.name=? COLLATE NOCASE THEN 0
                                 WHEN ut.name LIKE ? THEN 1 ELSE 2 END,
                            ut.name COLLATE NOCASE
                   LIMIT 3""",
                (request.state.user.id, term, query, prefix),
            ).fetchall()

        suggestions: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def add(value: str, suggestion_type: str, detail: str = "") -> None:
            key = (suggestion_type, value.casefold())
            if key in seen or len(suggestions) >= 10:
                return
            seen.add(key)
            suggestions.append({
                "value": value, "label": value, "type": suggestion_type, "detail": detail,
            })

        for row in title_rows:
            detail = "Movie" if row["kind"] == "movie" else "TV Show"
            if row["year"]:
                detail += f" · {row['year']}"
            add(row["label"], "Title", detail)
        for row in people_rows:
            roles = ", ".join(role.title() for role in (row["roles"] or "").split(","))
            count = row["title_count"]
            add(row["label"], "Person", f"{roles} · {count} title{'s' if count != 1 else ''}")
        for row in fuzzy_people(query, kind, 6):
            roles = ", ".join(role.title() for role in (row["roles"] or "").split(","))
            count = row["title_count"]
            add(
                row["person_name"], "Person",
                f"{roles} · {count} title{'s' if count != 1 else ''}",
            )
        for row in tag_rows:
            count = row["title_count"]
            add(row["label"], "Custom Tag", f"{count} title{'s' if count != 1 else ''}")
        for row in file_rows:
            add(row["label"], "Filename", "Movie" if row["kind"] == "movie" else "TV Show")
        return {"suggestions": suggestions}

    @router.get("/api/search-history")
    def search_history(request: Request) -> dict:
        if request.state.user.id <= 0:
            return {"history": []}
        with db.connect() as conn:
            rows = conn.execute(
                """SELECT query,searched_at FROM user_search_history
                   WHERE user_id=? ORDER BY searched_at DESC,id DESC LIMIT 10""",
                (request.state.user.id,),
            ).fetchall()
        return {"history": [dict(row) for row in rows]}

    @librarian_post("/api/search-history/clear")
    def clear_search_history(request: Request) -> dict:
        if request.state.user.id > 0:
            with db.connect() as conn:
                conn.execute(
                    "DELETE FROM user_search_history WHERE user_id=?",
                    (request.state.user.id,),
                )
        return {"cleared": True}

    return router, {
        "export_library": export_library,
        "toggle_favorite": toggle_favorite,
        "favorites_page": favorites_page,
        "episode_favorite_page": episode_favorite_page,
        "save_episode_favorite": save_episode_favorite,
        "manage_tags": manage_tags,
        "create_tag": create_tag,
        "rename_tag": rename_tag,
        "delete_tag": delete_tag,
        "library": library,
        "workspace_inspector": workspace_inspector,
        "workspace_toggle_favorite": workspace_toggle_favorite,
        "workspace_toggle_tag": workspace_toggle_tag,
        "movies": movies,
        "shows": shows,
        "people_search": people_search,
        "library_suggestions": library_suggestions,
        "search_history": search_history,
        "clear_search_history": clear_search_history,
    }

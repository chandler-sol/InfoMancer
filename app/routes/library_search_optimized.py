from __future__ import annotations

from urllib.parse import urlencode

from fastapi.responses import HTMLResponse

from .library_cached import _trimmed_response
from ..saved_views import SavedViewService


def eligible_search(
    *, q: str = "", kind: str = "all", letter: str = "", genre: str = "",
    title_type: str = "", root: str = "", person: str = "", person_name: str = "",
    credit_role: str = "", match: str = "", gaps: str = "", favorite: str = "",
    tag: str = "", sort: str = "title", record_search: str = "",
) -> bool:
    """Use the candidate-first plan only for the common free-text search path."""
    return bool(q.strip()) and kind in {"all", "movie", "tv"} and not any((
        letter, genre, title_type, root, person, person_name, credit_role,
        match, gaps, favorite, tag,
    )) and sort in {"", "title"} and record_search in {"", "1"}


def _record_search(db, user_id: int, query: str) -> None:
    if user_id <= 0:
        return
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO user_search_history(user_id,query,searched_at)
               VALUES (?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(user_id,query) DO UPDATE SET
                 query=excluded.query,searched_at=CURRENT_TIMESTAMP""",
            (user_id, query),
        )
        conn.execute(
            """DELETE FROM user_search_history
               WHERE user_id=? AND id NOT IN (
                 SELECT id FROM user_search_history WHERE user_id=?
                 ORDER BY searched_at DESC,id DESC LIMIT 10
               )""",
            (user_id, user_id),
        )


def _live_partial(request) -> bool:
    return (
        request.headers.get("x-infomancer-partial", "").strip().casefold()
        == "library"
    )


def _render_live_results(templates, request, rows, *, query: str, kind: str, view: str):
    """Render only the result surfaces without running global template processors.

    Jinja2Templates applies every context processor even to tiny partial templates.
    Library live-search can fire several times while someone types, so using the
    environment directly avoids announcement/tour/activity/settings queries that are
    unrelated to replacing the Library result rows.
    """
    template = templates.env.get_template("library_results.html")
    body = template.render({
        "request": request,
        "rows": rows,
        "q": query,
        "kind": kind,
        "view": view,
        "letter": "",
        "genre": "",
        "title_type": "",
        "root_id": None,
        "match_status": "",
        "gap_status": "",
        "favorite_status": "",
        "tag_id": None,
        "person_id": "",
        "person_name": "",
        "credit_role": "",
    })
    return HTMLResponse(body, headers={
        "Cache-Control": "private, no-store",
        "X-InfoMancer-Library-Query": "candidate-search",
        "X-InfoMancer-Partial": "library",
    })


def search_response(
    db, templates, display_title_type, fuzzy_people, request,
    *, q: str, kind: str = "all", record_search: str = "", view: str = "",
):
    """Render free-text Library search after finding candidate title ids first.

    The mature Library query aggregates every file and every missing episode before
    applying its WHERE clause. That is flexible for the advanced filter matrix but
    expensive for the high-frequency search box. This path preserves the same search
    sources, then computes file/missing statistics only for matching title ids.
    """
    query = q.strip()[:200]
    user_id = int(getattr(request.state.user, "id", 0) or 0)
    if record_search == "1":
        _record_search(db, user_id, query)

    term = f"%{query}%"
    fuzzy_names = [item["person_name"] for item in fuzzy_people(query, kind, 6)]
    branches = [
        ("SELECT id FROM titles WHERE title LIKE ? OR metadata_title LIKE ?", [term, term]),
        ("SELECT title_id FROM files WHERE filename LIKE ?", [term]),
        (
            "SELECT tt.title_id FROM title_tags tt "
            "JOIN user_tags ut ON ut.id=tt.tag_id "
            "WHERE ut.user_id=? AND ut.name LIKE ?",
            [user_id, term],
        ),
        ("SELECT title_id FROM title_credits WHERE person_name LIKE ?", [term]),
        (
            "SELECT ee.title_id FROM expected_episodes ee "
            "JOIN episode_credits ec ON ec.expected_episode_id=ee.id "
            "WHERE ec.person_name LIKE ?",
            [term],
        ),
    ]
    if fuzzy_names:
        placeholders = ",".join("?" for _ in fuzzy_names)
        branches.append((
            f"SELECT title_id FROM title_credits WHERE person_name IN ({placeholders})",
            fuzzy_names,
        ))
    candidate_sql = " UNION ".join(statement for statement, _params in branches)
    candidate_params = [value for _statement, values in branches for value in values]

    title_sort_base = "COALESCE(NULLIF(uts.sort_title,''),NULLIF(t.metadata_title,''),t.title)"
    title_sort_sql = (
        f"CASE WHEN LOWER({title_sort_base}) LIKE 'the %' THEN SUBSTR({title_sort_base},5) "
        f"WHEN LOWER({title_sort_base}) LIKE 'an %' THEN SUBSTR({title_sort_base},4) "
        f"WHEN LOWER({title_sort_base}) LIKE 'a %' THEN SUBSTR({title_sort_base},3) "
        f"ELSE {title_sort_base} END"
    )
    title_order = (
        f"{title_sort_sql} COLLATE NOCASE, "
        "COALESCE(NULLIF(t.metadata_title,''),t.title) COLLATE NOCASE"
    )
    kind_where = "WHERE t.kind=?" if kind in {"movie", "tv"} else ""
    query_params = [*candidate_params, user_id, user_id]
    if kind_where:
        query_params.append(kind)

    with db.connect() as conn:
        rows = conn.execute(
            f"""WITH candidates(id) AS (
                  {candidate_sql}
                ), file_stats AS (
                  SELECT f.title_id, COUNT(*) file_count,
                    COALESCE(SUM(f.size_bytes),0) bytes,
                    MIN(f.id) first_file_id,
                    SUM(f.runtime_seconds) runtime_seconds,
                    MAX(COALESCE(f.width,0) * COALESCE(f.height,0)) resolution_pixels,
                    MAX(f.bitrate) max_bitrate,
                    COALESCE(SUM(CASE WHEN f.season IS NOT NULL
                      AND f.episode_start IS NOT NULL
                      THEN COALESCE(f.episode_end,f.episode_start)-f.episode_start+1
                      ELSE 0 END),0) episode_count
                  FROM files f JOIN candidates c ON c.id=f.title_id
                  GROUP BY f.title_id
                ), missing_stats AS (
                  SELECT e.title_id, COUNT(*) missing_count
                  FROM expected_episodes e JOIN candidates c ON c.id=e.title_id
                  WHERE e.season>0 AND (e.aired IS NULL OR e.aired<=date('now'))
                    AND NOT EXISTS (
                      SELECT 1 FROM files owned
                      WHERE owned.title_id=e.title_id AND owned.season=e.season
                        AND e.episode BETWEEN owned.episode_start
                          AND COALESCE(owned.episode_end,owned.episode_start)
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
                FROM candidates candidate
                JOIN titles t ON t.id=candidate.id
                LEFT JOIN file_stats fs ON fs.title_id=t.id
                LEFT JOIN missing_stats ms ON ms.title_id=t.id
                LEFT JOIN user_title_state uts
                  ON uts.title_id=t.id AND uts.user_id=?
                {kind_where}
                ORDER BY {title_order} LIMIT 1000""",
            query_params,
        ).fetchall()

    # This is the high-frequency keystroke path. It needs only the rows being
    # replaced, not filter option scans, saved-view queries, or base.html context.
    if _live_partial(request):
        return _render_live_results(
            templates, request, rows, query=query, kind=kind, view=view,
        )

    # Full page navigation still needs the controls and their current option lists.
    with db.connect() as conn:
        option_conditions = ["(genres IS NOT NULL OR imdb_title_type IS NOT NULL)"]
        option_params: list = []
        if kind in {"movie", "tv"}:
            option_conditions.append("kind=?")
            option_params.append(kind)
        metadata_options = conn.execute(
            "SELECT genres, imdb_title_type FROM titles WHERE "
            + " AND ".join(option_conditions),
            option_params,
        ).fetchall()
        root_options = conn.execute(
            "SELECT id, label, path, kind FROM roots WHERE enabled=1 ORDER BY kind, label, path"
        ).fetchall()
        tag_options = conn.execute(
            """SELECT ut.id,ut.name,ut.color,COUNT(tt.title_id) title_count
               FROM user_tags ut LEFT JOIN title_tags tt ON tt.tag_id=ut.id
               WHERE ut.user_id=? GROUP BY ut.id ORDER BY ut.name COLLATE NOCASE""",
            (user_id,),
        ).fetchall()

    genre_options = sorted({
        value
        for row in metadata_options
        for value in (row["genres"] or "").split(",")
        if value
    })
    title_type_options = sorted({
        row["imdb_title_type"]
        for row in metadata_options
        if row["imdb_title_type"]
    }, key=display_title_type)
    saved_views = SavedViewService(db).list_for_user(user_id)
    current_view_path = {"movie": "/movies", "tv": "/shows"}.get(kind, "/library")
    current_view_query = urlencode({"q": query})
    filter_query = urlencode({"q": query, "sort": "title"})

    response = templates.TemplateResponse(request, "library.html", {
        "rows": rows,
        "q": query,
        "kind": kind,
        "letter": "",
        "genre": "",
        "title_type": "",
        "root_id": None,
        "match_status": "",
        "gap_status": "",
        "favorite_status": "",
        "tag_id": None,
        "sort_key": "title",
        "tag_options": tag_options,
        "saved_views": saved_views,
        "pinned_saved_views": [saved_view for saved_view in saved_views if saved_view["pinned"]],
        "current_view_path": current_view_path,
        "current_view_query": current_view_query,
        "root_options": root_options,
        "selected_root": None,
        "person_id": "",
        "person_name": "",
        "credit_role": "",
        "genre_options": genre_options,
        "title_type_options": title_type_options,
        "filter_query": filter_query,
        "source_query": filter_query,
        "heading": {"movie": "Movies", "tv": "TV Shows"}.get(kind, "Library"),
        "message": request.query_params.get("message", ""),
    })
    response.headers["X-InfoMancer-Library-Query"] = "candidate-search"
    response.headers.setdefault("Cache-Control", "private, no-store")
    return _trimmed_response(response, getattr(response, "body", b""), view)

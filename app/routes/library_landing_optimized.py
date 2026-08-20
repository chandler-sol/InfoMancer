from __future__ import annotations

from urllib.parse import urlencode

from ..saved_views import SavedViewService


def fast_landing_response(db, templates, display_title_type, request, *, kind: str = "all"):
    """Render an unfiltered Library landing after selecting visible candidates first."""
    kind = kind if kind in {"movie", "tv"} else "all"
    user_id = int(getattr(request.state.user, "id", 0) or 0)
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
    candidate_where = "WHERE t.kind=?" if kind != "all" else ""
    candidate_params: list = [user_id]
    if kind != "all":
        candidate_params.append(kind)

    with db.connect() as conn:
        option_conditions = ["(genres IS NOT NULL OR imdb_title_type IS NOT NULL)"]
        option_params: list = []
        if kind != "all":
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
        rows = conn.execute(
            f"""WITH candidates AS (
                  SELECT t.id
                  FROM titles t
                  LEFT JOIN user_title_state uts
                    ON uts.title_id=t.id AND uts.user_id=?
                  {candidate_where}
                  ORDER BY {title_order}
                  LIMIT 1000
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
                FROM candidates c
                JOIN titles t ON t.id=c.id
                LEFT JOIN file_stats fs ON fs.title_id=t.id
                LEFT JOIN missing_stats ms ON ms.title_id=t.id
                LEFT JOIN user_title_state uts
                  ON uts.title_id=t.id AND uts.user_id=?
                ORDER BY {title_order}""",
            (*candidate_params, user_id, user_id),
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
    default_query = urlencode({"sort": "title"})
    return templates.TemplateResponse(request, "library.html", {
        "rows": rows,
        "q": "",
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
        "pinned_saved_views": [view for view in saved_views if view["pinned"]],
        "current_view_path": current_view_path,
        "current_view_query": "",
        "root_options": root_options,
        "selected_root": None,
        "person_id": "",
        "person_name": "",
        "credit_role": "",
        "genre_options": genre_options,
        "title_type_options": title_type_options,
        "filter_query": default_query,
        "source_query": default_query,
        "heading": {"movie": "Movies", "tv": "TV Shows"}.get(kind, "Library"),
        "message": "",
    })

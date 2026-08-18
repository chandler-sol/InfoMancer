from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
import time
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

from ..saved_views import SavedViewService
from .library import build_router as build_base_router


@dataclass(frozen=True)
class _CachedLibrary:
    body: bytes
    signature: tuple


_CACHE: OrderedDict[tuple, _CachedLibrary] = OrderedDict()
_CACHE_LOCK = RLock()
_CACHE_LIMIT = 24
_LIBRARY_VIEW_COOKIE = "infomancer_library_view"


def _session_key(request: Request) -> str:
    session = getattr(request.state, "auth_session", None)
    if session is not None:
        return f"session:{getattr(session, 'id', '')}:{getattr(session, 'csrf_token', '')}"
    return f"local:{getattr(request.state, 'local_csrf_token', '')}"


def _requested_view(request: Request) -> str:
    explicit = request.headers.get("x-infomancer-library-view", "").strip().casefold()
    if explicit in {"list", "covers"}:
        return explicit
    saved = request.cookies.get(_LIBRARY_VIEW_COOKIE, "").strip().casefold()
    return saved if saved in {"list", "covers"} else ""


def _trim_library_surface(body: bytes, view: str) -> bytes:
    """Remove the inactive large Library surface while preserving JS anchor nodes.

    library.html historically renders every title twice: once as cover cards and once
    as table rows. Once the browser has told us its preferred view, only that surface
    needs to cross the wire. Lightweight placeholders keep the existing Library script
    compatible and can be filled on demand when the user switches views.
    """
    if view not in {"list", "covers"}:
        return body
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body

    cover_start = text.find('<section class="cover-library" id="cover-library"')
    bulk_id = text.find('id="library-bulk-form"', cover_start)
    bulk_start = text.rfind("<form", cover_start, bulk_id) if bulk_id >= 0 else -1
    list_start = text.find('<section class="panel table-wrap library-table"', bulk_id)
    list_end = text.find("</section>", list_start)
    if min(cover_start, bulk_start, list_start, list_end) < 0:
        return body
    list_end += len("</section>")

    if view == "list":
        placeholder = (
            '<section class="cover-library" id="cover-library" '
            'aria-label="Library covers" hidden '
            'data-library-surface-placeholder="covers"></section>\n'
        )
        text = text[:cover_start] + placeholder + text[bulk_start:]
    else:
        placeholder = (
            '<section class="panel table-wrap library-table" data-library-kind="all" '
            'hidden data-library-surface-placeholder="list">'
            '<table><thead></thead><tbody></tbody></table></section>'
        )
        text = text[:list_start] + placeholder + text[list_end:]
    return text.encode("utf-8")


def _library_signature(db, user_id: int) -> tuple:
    """Cheaply fingerprint state that changes the default Library document.

    The normal Library query computes file and missing-episode aggregates and then
    renders large title surfaces. This signature intentionally does much less work,
    so an unchanged landing page can reuse its already-rendered HTML safely.
    Activity/announcement state is included because the shared application chrome is
    part of the cached document. A minute bucket bounds time-based announcement drift.
    """
    with db.connect() as conn:
        row = conn.execute(
            """SELECT
                 (SELECT COUNT(*) FROM titles) title_count,
                 (SELECT COUNT(*) FROM files) file_count,
                 (SELECT COALESCE(MAX(updated_at),'') FROM titles) title_updated,
                 (SELECT COALESCE(MAX(last_scanned_at),'') FROM roots) root_scanned,
                 (SELECT COALESCE(GROUP_CONCAT(id || ':' || COALESCE(label,'') || ':' || path, '|'),'') FROM roots) roots_state,
                 (SELECT COALESCE(MAX(updated_at),'') FROM user_title_state WHERE user_id=?) user_state,
                 (SELECT COALESCE(GROUP_CONCAT(id || ':' || name || ':' || pinned || ':' || query_string, '|'),'') FROM user_saved_views WHERE user_id=?) saved_views,
                 (SELECT COALESCE(GROUP_CONCAT(id || ':' || name || ':' || color, '|'),'') FROM user_tags WHERE user_id=?) tags,
                 (SELECT COUNT(*) FROM title_tags tt JOIN user_tags ut ON ut.id=tt.tag_id WHERE ut.user_id=?) tag_links,
                 (SELECT COALESCE(MAX(updated_at),'') FROM app_settings) settings_updated,
                 (SELECT COALESCE(updated_at,'') FROM users WHERE id=?) user_updated,
                 (SELECT COALESCE(MAX(id),0) FROM event_logs) event_max,
                 (SELECT COUNT(*) FROM user_event_reads WHERE user_id=?) event_reads,
                 (SELECT COALESCE(MAX(updated_at),'') FROM announcements) announcement_updated,
                 (SELECT COUNT(*) FROM announcement_receipts WHERE user_id=?) announcement_receipts""",
            (user_id, user_id, user_id, user_id, user_id, user_id, user_id),
        ).fetchone()
    return (*tuple(row), int(time.time() // 60))


def _cacheable_landing(
    *, q: str, kind: str, letter: str, genre: str, title_type: str, root: str,
    person: str, person_name: str, credit_role: str, match: str, gaps: str,
    favorite: str, tag: str, sort: str, record_search: str,
) -> bool:
    return not any((
        q, letter, genre, title_type, root, person, person_name, credit_role,
        match, gaps, favorite, tag, record_search,
        kind not in {"", "all"}, sort not in {"", "title"},
    ))


def _cache_get(key: tuple, signature: tuple) -> bytes | None:
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if item is None or item.signature != signature:
            if item is not None:
                _CACHE.pop(key, None)
            return None
        _CACHE.move_to_end(key)
        return item.body


def _cache_put(key: tuple, signature: tuple, body: bytes) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = _CachedLibrary(body=bytes(body), signature=signature)
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)


def _trimmed_response(response, body: bytes, view: str, render_state: str = ""):
    trimmed = _trim_library_surface(body, view)
    if trimmed == body:
        if render_state:
            response.headers["X-InfoMancer-Library-Render"] = render_state
        return response
    headers = {
        key: value for key, value in response.headers.items()
        if key.lower() != "content-length"
    }
    headers["Cache-Control"] = "private, no-store"
    headers["X-InfoMancer-Library-Surface"] = view
    if render_state:
        headers["X-InfoMancer-Library-Render"] = render_state
    return Response(content=trimmed, status_code=response.status_code, headers=headers)


def _fast_landing_response(db, templates, display_title_type, request: Request):
    """Render the common unfiltered Library path without aggregating the whole catalog.

    Candidate title IDs are chosen first in display order. File and missing-episode
    aggregates then touch only those at-most-1000 visible titles instead of every
    title/file/episode in the installation. Filtered and specialized sorts continue
    through the original mature query path.
    """
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

    with db.connect() as conn:
        metadata_options = conn.execute(
            "SELECT genres, imdb_title_type FROM titles "
            "WHERE genres IS NOT NULL OR imdb_title_type IS NOT NULL"
        ).fetchall()
        root_options = conn.execute(
            "SELECT id, label, path, kind FROM roots WHERE enabled=1 ORDER BY kind, label, path"
        ).fetchall()
        genre_options = sorted({
            value
            for row in metadata_options
            for value in (row["genres"] or "").split(",")
            if value
        })
        title_type_options = sorted({
            row["imdb_title_type"] for row in metadata_options if row["imdb_title_type"]
        }, key=display_title_type)
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
                  ORDER BY {title_order}
                  LIMIT 1000
                ), file_stats AS (
                  SELECT f.title_id, COUNT(*) file_count,
                    COALESCE(SUM(f.size_bytes),0) bytes,
                    MIN(f.id) first_file_id,
                    SUM(f.runtime_seconds) runtime_seconds,
                    MAX(COALESCE(f.width,0) * COALESCE(f.height,0)) resolution_pixels,
                    MAX(f.bitrate) max_bitrate,
                    COALESCE(SUM(CASE WHEN f.season IS NOT NULL AND f.episode_start IS NOT NULL
                      THEN COALESCE(f.episode_end, f.episode_start) - f.episode_start + 1
                      ELSE 0 END),0) episode_count
                  FROM files f JOIN candidates c ON c.id=f.title_id
                  GROUP BY f.title_id
                ), missing_stats AS (
                  SELECT e.title_id, COUNT(*) missing_count
                  FROM expected_episodes e JOIN candidates c ON c.id=e.title_id
                  WHERE e.season > 0 AND (e.aired IS NULL OR e.aired <= date('now'))
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
            (user_id, user_id, user_id),
        ).fetchall()

    saved_views = SavedViewService(db).list_for_user(user_id)
    default_query = urlencode({"sort": "title"})
    return templates.TemplateResponse(request, "library.html", {
        "rows": rows,
        "q": "",
        "kind": "all",
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
        "current_view_path": "/library",
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
        "heading": "Library",
        "message": "",
    })


def build_router(ctx):
    router = build_base_router(ctx)
    db = ctx.live("db")
    templates = ctx.live("templates")
    display_title_type = ctx.live("display_title_type")

    original_route = next(
        (
            route for route in list(router.routes)
            if getattr(route, "path", None) == "/library"
            and "GET" in (getattr(route, "methods", set()) or set())
        ),
        None,
    )
    if original_route is None:
        return router

    original_library = original_route.endpoint
    router.routes.remove(original_route)

    @router.get("/library", response_class=HTMLResponse, name="library")
    def cached_library(
        request: Request, q: str = "", kind: str = "all", letter: str = "",
        genre: str = "", title_type: str = "", root: str = "",
        person: str = "", person_name: str = "", credit_role: str = "",
        match: str = "", gaps: str = "", favorite: str = "", tag: str = "",
        sort: str = "title", record_search: str = "",
    ):
        view = _requested_view(request)
        cacheable = _cacheable_landing(
            q=q, kind=kind, letter=letter, genre=genre, title_type=title_type,
            root=root, person=person, person_name=person_name,
            credit_role=credit_role, match=match, gaps=gaps, favorite=favorite,
            tag=tag, sort=sort, record_search=record_search,
        ) and "message" not in request.query_params and "tour" not in request.query_params

        arguments = (
            request, q, kind, letter, genre, title_type, root, person,
            person_name, credit_role, match, gaps, favorite, tag, sort,
            record_search,
        )

        if not cacheable:
            response = original_library(*arguments)
            return _trimmed_response(response, getattr(response, "body", b""), view)

        user_id = int(getattr(request.state.user, "id", 0) or 0)
        signature = _library_signature(db, user_id)
        key = (_session_key(request), request.url.path, view or "full")
        cached = _cache_get(key, signature)
        if cached is not None:
            headers = {
                "Cache-Control": "private, no-store",
                "X-InfoMancer-Library-Render": "hit",
                "X-InfoMancer-Library-Query": "scoped",
            }
            if view:
                headers["X-InfoMancer-Library-Surface"] = view
            return Response(content=cached, media_type="text/html", headers=headers)

        response = _fast_landing_response(db, templates, display_title_type, request)
        body = getattr(response, "body", b"")
        served_body = _trim_library_surface(body, view)
        if response.status_code == 200 and served_body:
            _cache_put(key, signature, served_body)
        if served_body != body:
            headers = {
                key: value for key, value in response.headers.items()
                if key.lower() != "content-length"
            }
            headers["Cache-Control"] = "private, no-store"
            headers["X-InfoMancer-Library-Render"] = "miss"
            headers["X-InfoMancer-Library-Surface"] = view
            headers["X-InfoMancer-Library-Query"] = "scoped"
            return Response(content=served_body, status_code=response.status_code, headers=headers)
        if response.status_code == 200 and body:
            response.headers["X-InfoMancer-Library-Render"] = "miss"
            response.headers["X-InfoMancer-Library-Query"] = "scoped"
            response.headers.setdefault("Cache-Control", "private, no-store")
        return response

    return router

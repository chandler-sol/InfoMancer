from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
import time

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

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


def build_router(ctx):
    router = build_base_router(ctx)
    db = ctx.live("db")

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
            }
            if view:
                headers["X-InfoMancer-Library-Surface"] = view
            return Response(content=cached, media_type="text/html", headers=headers)

        response = original_library(*arguments)
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
            return Response(content=served_body, status_code=response.status_code, headers=headers)
        if response.status_code == 200 and body:
            response.headers["X-InfoMancer-Library-Render"] = "miss"
            response.headers.setdefault("Cache-Control", "private, no-store")
        return response

    return router

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock

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


def _session_key(request: Request) -> str:
    session = getattr(request.state, "auth_session", None)
    if session is not None:
        return f"session:{getattr(session, 'id', '')}:{getattr(session, 'csrf_token', '')}"
    return f"local:{getattr(request.state, 'local_csrf_token', '')}"


def _library_signature(db, user_id: int) -> tuple:
    """Cheaply fingerprint state that changes the default Library document.

    The normal Library query computes file and missing-episode aggregates and then
    renders both List and Cover DOM. This signature intentionally does much less
    work, so an unchanged landing page can reuse its already-rendered HTML safely.
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
                 (SELECT COALESCE(updated_at,'') FROM users WHERE id=?) user_updated""",
            (user_id, user_id, user_id, user_id, user_id),
        ).fetchone()
    return tuple(row)


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
        cacheable = _cacheable_landing(
            q=q, kind=kind, letter=letter, genre=genre, title_type=title_type,
            root=root, person=person, person_name=person_name,
            credit_role=credit_role, match=match, gaps=gaps, favorite=favorite,
            tag=tag, sort=sort, record_search=record_search,
        ) and "message" not in request.query_params and "tour" not in request.query_params

        if not cacheable:
            return original_library(
                request, q, kind, letter, genre, title_type, root, person,
                person_name, credit_role, match, gaps, favorite, tag, sort,
                record_search,
            )

        user_id = int(getattr(request.state.user, "id", 0) or 0)
        signature = _library_signature(db, user_id)
        key = (_session_key(request), request.url.path)
        cached = _cache_get(key, signature)
        if cached is not None:
            return Response(
                content=cached,
                media_type="text/html",
                headers={
                    "Cache-Control": "private, no-store",
                    "X-InfoMancer-Library-Render": "hit",
                },
            )

        response = original_library(
            request, q, kind, letter, genre, title_type, root, person,
            person_name, credit_role, match, gaps, favorite, tag, sort,
            record_search,
        )
        body = getattr(response, "body", b"")
        if response.status_code == 200 and body:
            _cache_put(key, signature, body)
            response.headers["X-InfoMancer-Library-Render"] = "miss"
            response.headers.setdefault("Cache-Control", "private, no-store")
        return response

    return router

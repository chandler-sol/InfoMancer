from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

from .library import build_router as build_base_router
from .library_cached import (
    _cache_get,
    _cache_put,
    _library_signature,
    _requested_view,
    _session_key,
    _trim_library_surface,
    _trimmed_response,
)
from .library_landing_optimized import fast_landing_response
from .library_search_optimized import eligible_search, search_response


def _warm_response(render_state: str, view: str) -> Response:
    headers = {
        "Cache-Control": "private, no-store",
        "X-InfoMancer-Library-Render": render_state,
        "X-InfoMancer-Library-Query": "scoped",
        "X-InfoMancer-Library-Prefetch": "warm",
    }
    if view:
        headers["X-InfoMancer-Library-Surface"] = view
    return Response(status_code=204, headers=headers)


def _live_results_fragment(body: bytes) -> bytes | None:
    """Keep only the two nodes the inline Library filter actually consumes."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None

    cover_start = text.find('<section class="cover-library" id="cover-library"')
    cover_end = text.find("</section>", cover_start)
    list_start = text.find('<section class="panel table-wrap library-table"')
    list_end = text.find("</section>", list_start)
    if min(cover_start, cover_end, list_start, list_end) < 0:
        return None
    cover_end += len("</section>")
    list_end += len("</section>")
    return (
        "<!doctype html><html><body>"
        + text[cover_start:cover_end]
        + text[list_start:list_end]
        + "</body></html>"
    ).encode("utf-8")


def _live_results_response(response: Response) -> Response:
    if response.headers.get("X-InfoMancer-Partial", "").casefold() == "library":
        return response
    body = getattr(response, "body", b"")
    if response.status_code != 200 or not body:
        return response
    fragment = _live_results_fragment(body)
    if fragment is None:
        return response
    headers = {
        key: value for key, value in response.headers.items()
        if key.lower() != "content-length"
    }
    headers["Cache-Control"] = "private, no-store"
    headers["X-InfoMancer-Partial"] = "library"
    return Response(content=fragment, status_code=200, headers=headers)


def _cacheable_landing(
    *, q: str, kind: str, letter: str, genre: str, title_type: str, root: str,
    person: str, person_name: str, credit_role: str, match: str, gaps: str,
    favorite: str, tag: str, sort: str, record_search: str,
) -> bool:
    """All, Movies, and TV landings share the same candidate-first fast path."""
    return kind in {"all", "movie", "tv"} and not any((
        q, letter, genre, title_type, root, person, person_name, credit_role,
        match, gaps, favorite, tag, record_search, sort not in {"", "title"},
    ))


def build_router(ctx):
    """Wrap Library routes with optimized landing, search, partial, and warm paths."""
    router, handlers = build_base_router(ctx)
    db = ctx.live("db")
    templates = ctx.live("templates")
    display_title_type = ctx.live("display_title_type")
    fuzzy_people = ctx.live("fuzzy_people")

    optimized_paths = {"/library", "/movies", "/shows"}
    original_routes = {
        route.path: route
        for route in list(router.routes)
        if getattr(route, "path", None) in optimized_paths
        and "GET" in (getattr(route, "methods", set()) or set())
    }
    original_route = original_routes.get("/library")
    if original_route is None:
        return router, handlers
    original_library = original_route.endpoint
    for route in original_routes.values():
        router.routes.remove(route)

    def serve(
        request: Request, *, q: str = "", kind: str = "all", letter: str = "",
        genre: str = "", title_type: str = "", root: str = "",
        person: str = "", person_name: str = "", credit_role: str = "",
        match: str = "", gaps: str = "", favorite: str = "", tag: str = "",
        sort: str = "title", record_search: str = "",
    ):
        kind = kind if kind in {"movie", "tv"} else "all"
        view = _requested_view(request)
        has_transient_context = (
            "message" in request.query_params or "tour" in request.query_params
        )
        is_prefetch = (
            request.headers.get("x-infomancer-prefetch", "").strip().casefold()
            == "library"
        )
        is_live_partial = (
            request.headers.get("x-infomancer-partial", "").strip().casefold()
            == "library"
        )

        if not has_transient_context and eligible_search(
            q=q, kind=kind, letter=letter, genre=genre, title_type=title_type,
            root=root, person=person, person_name=person_name,
            credit_role=credit_role, match=match, gaps=gaps, favorite=favorite,
            tag=tag, sort=sort, record_search=record_search,
        ):
            response = search_response(
                db, templates, display_title_type, fuzzy_people, request,
                q=q, kind=kind, record_search=record_search, view=view,
            )
            return _live_results_response(response) if is_live_partial else response

        cacheable = _cacheable_landing(
            q=q, kind=kind, letter=letter, genre=genre, title_type=title_type,
            root=root, person=person, person_name=person_name,
            credit_role=credit_role, match=match, gaps=gaps, favorite=favorite,
            tag=tag, sort=sort, record_search=record_search,
        ) and not has_transient_context

        arguments = (
            request, q, kind, letter, genre, title_type, root, person,
            person_name, credit_role, match, gaps, favorite, tag, sort,
            record_search,
        )

        if not cacheable:
            response = original_library(*arguments)
            response = _trimmed_response(response, getattr(response, "body", b""), view)
            return _live_results_response(response) if is_live_partial else response

        user_id = int(getattr(request.state.user, "id", 0) or 0)
        signature = _library_signature(db, user_id)
        key = (_session_key(request), request.url.path, kind, view or "full")
        cached = _cache_get(key, signature)
        if cached is not None:
            if is_prefetch:
                return _warm_response("hit", view)
            headers = {
                "Cache-Control": "private, no-store",
                "X-InfoMancer-Library-Render": "hit",
                "X-InfoMancer-Library-Query": "scoped",
            }
            if view:
                headers["X-InfoMancer-Library-Surface"] = view
            response = Response(content=cached, media_type="text/html", headers=headers)
            return _live_results_response(response) if is_live_partial else response

        response = fast_landing_response(
            db, templates, display_title_type, request, kind=kind,
        )
        body = getattr(response, "body", b"")
        served_body = _trim_library_surface(body, view)
        if response.status_code == 200 and served_body:
            _cache_put(key, signature, served_body)
        if is_prefetch and response.status_code == 200:
            return _warm_response("miss", view)
        if served_body != body:
            headers = {
                key: value for key, value in response.headers.items()
                if key.lower() != "content-length"
            }
            headers["Cache-Control"] = "private, no-store"
            headers["X-InfoMancer-Library-Render"] = "miss"
            headers["X-InfoMancer-Library-Surface"] = view
            headers["X-InfoMancer-Library-Query"] = "scoped"
            response = Response(
                content=served_body, status_code=response.status_code, headers=headers
            )
        elif response.status_code == 200 and body:
            response.headers["X-InfoMancer-Library-Render"] = "miss"
            response.headers["X-InfoMancer-Library-Query"] = "scoped"
            response.headers.setdefault("Cache-Control", "private, no-store")
        return _live_results_response(response) if is_live_partial else response

    @router.get("/library", response_class=HTMLResponse, name="library")
    def cached_library(
        request: Request, q: str = "", kind: str = "all", letter: str = "",
        genre: str = "", title_type: str = "", root: str = "",
        person: str = "", person_name: str = "", credit_role: str = "",
        match: str = "", gaps: str = "", favorite: str = "", tag: str = "",
        sort: str = "title", record_search: str = "",
    ):
        return serve(
            request, q=q, kind=kind, letter=letter, genre=genre,
            title_type=title_type, root=root, person=person, person_name=person_name,
            credit_role=credit_role, match=match, gaps=gaps, favorite=favorite,
            tag=tag, sort=sort, record_search=record_search,
        )

    @router.get("/movies", response_class=HTMLResponse, name="movies")
    def movies(
        request: Request, q: str = "", letter: str = "", genre: str = "",
        title_type: str = "", root: str = "", person: str = "",
        person_name: str = "", credit_role: str = "", match: str = "",
        gaps: str = "", favorite: str = "", tag: str = "", sort: str = "title",
        record_search: str = "",
    ):
        return serve(
            request, q=q, kind="movie", letter=letter, genre=genre,
            title_type=title_type, root=root, person=person, person_name=person_name,
            credit_role=credit_role, match=match, gaps=gaps, favorite=favorite,
            tag=tag, sort=sort, record_search=record_search,
        )

    @router.get("/shows", response_class=HTMLResponse, name="shows")
    def shows(
        request: Request, q: str = "", letter: str = "", genre: str = "",
        title_type: str = "", root: str = "", person: str = "",
        person_name: str = "", credit_role: str = "", match: str = "",
        gaps: str = "", favorite: str = "", tag: str = "", sort: str = "title",
        record_search: str = "",
    ):
        return serve(
            request, q=q, kind="tv", letter=letter, genre=genre,
            title_type=title_type, root=root, person=person, person_name=person_name,
            credit_role=credit_role, match=match, gaps=gaps, favorite=favorite,
            tag=tag, sort=sort, record_search=record_search,
        )

    updated_handlers = dict(handlers)
    updated_handlers.update({
        "library": cached_library,
        "movies": movies,
        "shows": shows,
    })
    return router, updated_handlers

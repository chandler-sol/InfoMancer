from __future__ import annotations

from urllib.parse import parse_qs

from starlette.datastructures import MutableHeaders


class StaticAssetCacheMiddleware:
    """Make versioned application assets reusable across full-page navigation.

    InfoMancer appends a process-specific ``?v=`` value to its CSS and JavaScript
    URLs. A restart therefore changes the URL when a new build is loaded, making
    long-lived immutable browser caching safe for those versioned requests.
    Unversioned assets are left on StaticFiles' normal validation policy so icons
    or other directly referenced files cannot become permanently stale.
    Dynamic HTML, JSON, downloads, and user data are deliberately untouched.
    """

    CACHE_CONTROL = "public, max-age=31536000, immutable"

    def __init__(self, app) -> None:
        self.app = app

    @staticmethod
    def _versioned(scope) -> bool:
        if scope.get("type") != "http" or not str(scope.get("path") or "").startswith("/static/"):
            return False
        raw_query = scope.get("query_string") or b""
        try:
            query = parse_qs(raw_query.decode("ascii", errors="ignore"), keep_blank_values=True)
        except (AttributeError, UnicodeError):
            return False
        return bool(query.get("v", [""])[0])

    async def __call__(self, scope, receive, send) -> None:
        if not self._versioned(scope):
            await self.app(scope, receive, send)
            return

        async def send_cached(message) -> None:
            if message.get("type") == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = self.CACHE_CONTROL
            await send(message)

        await self.app(scope, receive, send_cached)


class LibrarySurfacePartialMiddleware:
    """Trim Library view-hydration requests before bytes leave the server.

    The normal Library routes keep rendering their mature full-page response so the
    filtering/query code has one source of truth. When the browser asks specifically
    for the missing List or Covers surface, this middleware buffers that one response
    and sends only the requested ``<section>``. This avoids transferring and parsing
    the global application chrome a second time while preserving the normal route as
    a no-JavaScript fallback.
    """

    PATHS = {"/library", "/movies", "/shows"}
    PARTIAL_HEADER = b"x-infomancer-partial"
    VIEW_HEADER = b"x-infomancer-library-view"

    def __init__(self, app) -> None:
        self.app = app

    @staticmethod
    def _request_headers(scope) -> dict[bytes, bytes]:
        return {key.lower(): value for key, value in scope.get("headers") or ()}

    @classmethod
    def _requested_view(cls, scope) -> str:
        if scope.get("type") != "http" or scope.get("method") != "GET":
            return ""
        if str(scope.get("path") or "") not in cls.PATHS:
            return ""
        headers = cls._request_headers(scope)
        if headers.get(cls.PARTIAL_HEADER, b"").strip().lower() != b"library-surface":
            return ""
        view = headers.get(cls.VIEW_HEADER, b"").strip().lower()
        return view.decode("ascii") if view in {b"list", b"covers"} else ""

    @staticmethod
    def _extract(body: bytes, view: str) -> bytes | None:
        marker = (
            b'<section class="cover-library" id="cover-library"'
            if view == "covers"
            else b'<section class="panel table-wrap library-table"'
        )
        start = body.find(marker)
        if start < 0:
            return None
        end = body.find(b"</section>", start)
        if end < 0:
            return None
        return body[start:end + len(b"</section>")]

    async def __call__(self, scope, receive, send) -> None:
        view = self._requested_view(scope)
        if not view:
            await self.app(scope, receive, send)
            return

        start_message = None
        chunks: list[bytes] = []

        async def capture(message) -> None:
            nonlocal start_message
            message_type = message.get("type")
            if message_type == "http.response.start":
                start_message = dict(message)
                start_message["headers"] = list(message.get("headers") or ())
                return
            if message_type != "http.response.body":
                await send(message)
                return

            chunks.append(message.get("body") or b"")
            if message.get("more_body"):
                return

            body = b"".join(chunks)
            partial = self._extract(body, view)
            if start_message is None or partial is None:
                if start_message is not None:
                    await send(start_message)
                await send({"type": "http.response.body", "body": body, "more_body": False})
                return

            headers = MutableHeaders(scope=start_message)
            content_type = headers.get("content-type", "")
            if start_message.get("status", 200) != 200 or "text/html" not in content_type:
                await send(start_message)
                await send({"type": "http.response.body", "body": body, "more_body": False})
                return

            headers["Content-Length"] = str(len(partial))
            headers["Cache-Control"] = "private, no-store"
            headers["X-InfoMancer-Partial"] = "library-surface"
            headers["X-InfoMancer-Library-Surface"] = view
            await send(start_message)
            await send({"type": "http.response.body", "body": partial, "more_body": False})

        await self.app(scope, receive, capture)

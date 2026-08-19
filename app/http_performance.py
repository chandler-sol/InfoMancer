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

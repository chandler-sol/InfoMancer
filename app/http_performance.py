from __future__ import annotations

from starlette.datastructures import MutableHeaders


class StaticAssetCacheMiddleware:
    """Make versioned application assets reusable across full-page navigation.

    InfoMancer appends a process-specific ``?v=`` value to its CSS and JavaScript
    URLs. A restart therefore changes the URL when a new build is loaded, making
    long-lived immutable browser caching safe for the files under ``/static``.
    Dynamic HTML, JSON, downloads, and user data are deliberately untouched.
    """

    CACHE_CONTROL = "public, max-age=31536000, immutable"

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http" or not str(scope.get("path") or "").startswith("/static/"):
            await self.app(scope, receive, send)
            return

        async def send_cached(message) -> None:
            if message.get("type") == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = self.CACHE_CONTROL
            await send(message)

        await self.app(scope, receive, send_cached)

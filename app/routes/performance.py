from __future__ import annotations

import time

from fastapi import APIRouter

from ..http_performance import LibrarySurfacePartialMiddleware, StaticAssetCacheMiddleware
from .context import RouteContext


def build_router(ctx: RouteContext):
    """Install response-level performance policy without adding a public route."""
    router = APIRouter()
    app = ctx.live("app")
    templates = ctx.live("templates")
    state = app.state

    # base.html has long used ?v={{ static_version }}, but older builds never
    # supplied that value, leaving browsers with effectively unversioned assets.
    # Give each running application process one stable token. A restart/rebuild gets
    # a new URL and therefore a fresh CSS/JS payload, while navigation within the
    # process can safely reuse the immutable browser cache.
    static_version = getattr(state, "static_version", "")
    if not static_version:
        static_version = f"{time.time_ns():x}"
        state.static_version = static_version
    templates.env.globals["static_version"] = static_version

    if not getattr(state, "static_asset_cache_middleware", False):
        app.add_middleware(StaticAssetCacheMiddleware)
        state.static_asset_cache_middleware = True
    if not getattr(state, "library_surface_partial_middleware", False):
        app.add_middleware(LibrarySurfacePartialMiddleware)
        state.library_surface_partial_middleware = True
    return router, {}

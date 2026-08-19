from __future__ import annotations

from fastapi import APIRouter

from ..http_performance import StaticAssetCacheMiddleware
from .context import RouteContext


def build_router(ctx: RouteContext):
    """Install response-level performance policy without adding a public route."""
    router = APIRouter()
    app = ctx.live("app")
    state = app.state
    if not getattr(state, "static_asset_cache_middleware", False):
        app.add_middleware(StaticAssetCacheMiddleware)
        state.static_asset_cache_middleware = True
    return router, {}

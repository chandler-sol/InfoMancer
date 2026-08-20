from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter
from starlette.middleware.gzip import GZipMiddleware

from ..http_performance import LibrarySurfacePartialMiddleware, StaticAssetCacheMiddleware
from .context import RouteContext


def _static_asset_version(static_dir: Path) -> str:
    """Return a stable cache key for the exact static asset tree.

    Versioned assets are served as immutable for a year, so the version must change
    when bytes change but should *not* change merely because InfoMancer restarted.
    Hashing the relatively small static tree once during startup gives us both
    properties and lets browsers keep their CSS/JS cache across normal restarts.
    """
    digest = hashlib.blake2s(digest_size=12)
    for path in sorted(
        (candidate for candidate in static_dir.rglob("*") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(static_dir).as_posix(),
    ):
        relative = path.relative_to(static_dir).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(128 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def build_router(ctx: RouteContext):
    """Install response-level performance policy without adding a public route."""
    router = APIRouter()
    app = ctx.live("app")
    templates = ctx.live("templates")
    state = app.state

    # StaticAssetCacheMiddleware marks versioned assets immutable. Use a content
    # fingerprint rather than a process timestamp so a restart with identical files
    # reuses the browser cache while any changed asset automatically gets a new URL.
    static_version = getattr(state, "static_version", "")
    if not static_version:
        static_dir = Path(__file__).resolve().parents[1] / "static"
        static_version = _static_asset_version(static_dir)
        state.static_version = static_version
    templates.env.globals["static_version"] = static_version

    if not getattr(state, "static_asset_cache_middleware", False):
        app.add_middleware(StaticAssetCacheMiddleware)
        state.static_asset_cache_middleware = True
    if not getattr(state, "library_surface_partial_middleware", False):
        app.add_middleware(LibrarySurfacePartialMiddleware)
        state.library_surface_partial_middleware = True
    if not getattr(state, "gzip_middleware", False):
        # Added last so compression wraps the already-trimmed Library response. A
        # moderate level gets most of the CSS/HTML/JS savings without making request
        # threads spend excessive CPU chasing the final few percent of compression.
        app.add_middleware(GZipMiddleware, minimum_size=4096, compresslevel=5)
        state.gzip_middleware = True
    return router, {}

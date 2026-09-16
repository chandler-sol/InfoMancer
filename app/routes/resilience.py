from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .context import RouteContext


_API_FAILURE_MESSAGE = (
    "InfoMancer hit an unexpected error while handling that request. Do not assume "
    "the action completed. Open Logs for details, then try again."
)


def structured_api_failure_response(request: Request, exc: Exception, record_event):
    """Log and render an unexpected API failure without replacing the original error."""
    try:
        record_event(
            "system",
            "An API request stopped because of an unexpected error.",
            level="error",
            detail=f"{type(exc).__name__}: {exc}",
            context={"operation": "api_request_failure"},
            user_id=getattr(getattr(request.state, "user", None), "id", None),
        )
    except Exception:
        # Error reporting must never replace the original API failure with a
        # second exception if logging or the database is also unavailable.
        pass
    return JSONResponse(
        {"detail": _API_FAILURE_MESSAGE},
        status_code=500,
    )


def build_router(ctx: RouteContext):
    """Keep unexpected API failures machine-readable and visible in Logs.

    Browser clients expect JSON from /api routes. Starlette's default unhandled 500
    body is plain text, which can turn the real server error into a misleading
    JavaScript JSON-parser exception. This middleware preserves normal exception
    handling everywhere else while giving API clients a stable failure envelope.

    The maintenance admission middleware handles failures from admitted requests
    before releasing their operation lease. This outer layer remains as a fallback
    for failures raised outside that boundary.
    """
    router = APIRouter()
    app = ctx.get("app")
    record_event = ctx.live("record_event")

    @app.middleware("http")
    async def structured_api_failures(request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            if not request.url.path.startswith("/api/"):
                raise
            return structured_api_failure_response(request, exc, record_event)

    return router, {}

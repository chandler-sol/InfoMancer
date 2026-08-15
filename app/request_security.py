from __future__ import annotations

from urllib.parse import parse_qs

from starlette.requests import Request


MAX_URLENCODED_BODY = 2 * 1024 * 1024


class RequestBodyTooLarge(ValueError):
    pass


async def csrf_submission(
    request: Request, *, max_urlencoded_body: int = MAX_URLENCODED_BODY,
) -> tuple[str, bytes | None]:
    """Read only small URL-encoded forms when a CSRF header is unavailable.

    Multipart uploads and API requests must send X-CSRF-Token and are left
    untouched so downstream handlers can stream their request bodies normally.
    """
    header = request.headers.get("x-csrf-token", "").strip()
    if header:
        return header, None

    content_type = request.headers.get("content-type", "").casefold()
    if not content_type.startswith("application/x-www-form-urlencoded"):
        return "", None

    length_text = request.headers.get("content-length", "").strip()
    if length_text.isdigit() and int(length_text) > max_urlencoded_body:
        raise RequestBodyTooLarge("URL-encoded request body is too large")

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_urlencoded_body:
            raise RequestBodyTooLarge("URL-encoded request body is too large")
        chunks.append(chunk)
    body = b"".join(chunks)
    try:
        values = parse_qs(
            body.decode("utf-8", errors="replace"),
            keep_blank_values=True,
            max_num_fields=1000,
        )
    except ValueError:
        return "", body
    return str((values.get("csrf_token") or [""])[0]), body


def replay_body(request: Request, body: bytes) -> None:
    """Replay a small verified form body for FastAPI's downstream parser."""
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    request._receive = receive

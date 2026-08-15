from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from starlette.requests import Request


MAX_URLENCODED_BODY = 2 * 1024 * 1024
LOCAL_CSRF_COOKIE = "infomancer_local_csrf"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}


class RequestBodyTooLarge(ValueError):
    pass


def _hostname(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        return ""
    try:
        parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
        return (parsed.hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""


def allowed_hosts(settings) -> set[str]:
    allowed = set(LOCAL_HOSTS)
    for value in settings.trusted_hosts:
        host = _hostname(value)
        if host:
            allowed.add(host)
    if settings.public_url:
        host = _hostname(settings.public_url)
        if host:
            allowed.add(host)
    return allowed


def host_is_allowed(request: Request, settings) -> bool:
    """Reject DNS-rebinding/unexpected Host values when an allowlist applies."""
    enforce = (
        settings.auth_mode == "disabled"
        or bool(settings.trusted_hosts)
        or bool(settings.public_url)
    )
    if not enforce:
        return True
    return _hostname(request.headers.get("host", "")) in allowed_hosts(settings)


def _origin(value: str) -> tuple[str, str, int | None] | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").casefold().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not host:
            return None
        return parsed.scheme.casefold(), host, parsed.port
    except ValueError:
        return None


def browser_request_is_same_origin(request: Request, settings) -> bool:
    """Fail browser cross-site requests while leaving non-browser clients usable."""
    fetch_site = request.headers.get("sec-fetch-site", "").strip().casefold()
    if fetch_site == "cross-site":
        return False
    origin_header = request.headers.get("origin", "").strip()
    if not origin_header:
        return True
    received = _origin(origin_header)
    if received is None:
        return False
    expected: set[tuple[str, str, int | None]] = set()
    host = request.headers.get("host", "").strip()
    current = _origin(f"{request.url.scheme}://{host}")
    if current:
        expected.add(current)
    public = _origin(settings.public_url)
    if public:
        expected.add(public)
    return received in expected


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
    request._body = body
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    request._receive = receive

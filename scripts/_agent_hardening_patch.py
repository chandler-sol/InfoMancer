from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match in {path}, found {count}: {old[:120]!r}")
    write(path, content.replace(old, new, 1))


# --- Focused request-security helpers ---------------------------------------
write(
    "app/request_security.py",
    '''from __future__ import annotations

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
''',
)

write(
    "app/bootstrap.py",
    '''from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path


class BootstrapTokenManager:
    """Provide a one-time server-side secret for first-run account creation."""

    def __init__(self, path: Path, configured_token: str = ""):
        self.path = path
        self.configured_token = configured_token.strip()
        self._announced = False

    def token(self) -> str:
        if self.configured_token:
            return self.configured_token
        self.path.parent.mkdir(parents=True, exist_ok=True)
        token = ""
        try:
            token = self.path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            pass
        if not token:
            token = secrets.token_urlsafe(32)
            try:
                descriptor = os.open(
                    self.path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                token = self.path.read_text(encoding="utf-8").strip()
                if not token:
                    raise RuntimeError("The first-run bootstrap token file is empty.")
            else:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(token + "\n")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        if not self._announced:
            print(
                "InfoMancer first-run bootstrap token: " + token,
                flush=True,
            )
            print(
                "Enter this token once at /setup. It is invalidated after the first Librarian is created.",
                flush=True,
            )
            self._announced = True
        return token

    def verify(self, submitted: str) -> bool:
        expected = self.token()
        return bool(submitted and hmac.compare_digest(submitted, expected))

    def clear(self) -> None:
        if not self.configured_token:
            self.path.unlink(missing_ok=True)
''',
)

write(
    "app/static/multipart-submit.js",
    '''(() => {
  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.method.toLowerCase() !== "post") return;
    if (form.enctype.toLowerCase() !== "multipart/form-data") return;
    if (event.defaultPrevented) return;

    const csrfInput = form.querySelector('input[name="csrf_token"]');
    const csrfToken = csrfInput?.value || "";
    if (!csrfToken) return;

    event.preventDefault();
    const submitter = event.submitter;
    const data = new FormData(form);
    if (submitter?.name) data.append(submitter.name, submitter.value || "");
    const action = submitter?.formAction || form.action || window.location.href;
    const button = submitter instanceof HTMLButtonElement ? submitter : null;
    if (button) button.disabled = true;

    try {
      const response = await fetch(action, {
        method: "POST",
        body: data,
        credentials: "same-origin",
        headers: {"X-CSRF-Token": csrfToken},
        redirect: "follow",
      });
      if (response.redirected) {
        window.location.assign(response.url);
        return;
      }
      const html = await response.text();
      window.history.replaceState({}, "", response.url);
      document.open();
      document.write(html);
      document.close();
    } catch (_error) {
      if (button) button.disabled = false;
      window.alert("The upload could not be submitted. Check your connection and try again.");
    }
  });
})();
''',
)

# --- Configuration ----------------------------------------------------------
replace_once(
    "app/config.py",
    '    application_secret: str = ""\n    sandbox: bool = False\n',
    '    application_secret: str = ""\n    sandbox: bool = False\n    bootstrap_token: str = ""\n',
)
replace_once(
    "app/config.py",
    '        application_secret=os.getenv("INFOMANCER_SECRET", "").strip(),\n        sandbox=os.getenv("INFOMANCER_SANDBOX", "").strip().casefold()\n        in {"1", "true", "yes", "on"},\n',
    '        application_secret=os.getenv("INFOMANCER_SECRET", "").strip(),\n        sandbox=os.getenv("INFOMANCER_SANDBOX", "").strip().casefold()\n        in {"1", "true", "yes", "on"},\n        bootstrap_token=os.getenv("INFOMANCER_BOOTSTRAP_TOKEN", "").strip(),\n',
)

# --- Forwarded-header trust -------------------------------------------------
replace_once(
    "app/auth.py",
    'import hashlib\nimport re\nimport secrets\n',
    'import hashlib\nimport ipaddress\nimport re\nimport secrets\n',
)
replace_once(
    "app/auth.py",
    '''def secure_cookie_for(request, settings: Settings) -> bool:
    if settings.cookie_secure == "true":
        return True
    if settings.cookie_secure == "false":
        return False
    forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    return forwarded == "https" or request.url.scheme == "https"


def request_ip(request) -> str:
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get(
        "x-forwarded-for", ""
    ).split(",", 1)[0].strip()
    if forwarded:
        return forwarded[:64]
    return (request.client.host if request.client else "")[:64]
''',
    '''def _verified_cloudflare_request(request, settings: Settings) -> bool:
    claims = getattr(getattr(request, "state", None), "external_claims", None)
    return settings.auth_mode == "cloudflare" and bool(claims)


def secure_cookie_for(request, settings: Settings) -> bool:
    if settings.cookie_secure == "true":
        return True
    if settings.cookie_secure == "false":
        return False
    if getattr(getattr(request, "url", None), "scheme", "") == "https":
        return True
    if _verified_cloudflare_request(request, settings):
        forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
        return forwarded == "https"
    return False


def request_ip(request, settings: Settings | None = None) -> str:
    if settings is not None and _verified_cloudflare_request(request, settings):
        forwarded = request.headers.get("cf-connecting-ip", "").strip()
        try:
            if forwarded:
                return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return (request.client.host if request.client else "")[:64]
''',
)
replace_once(
    "app/auth.py",
    '                    request.headers.get("user-agent", "")[:500], request_ip(request),\n',
    '                    request.headers.get("user-agent", "")[:500],\n                    request_ip(request, self.settings),\n',
)

# --- Application middleware and first-run setup ----------------------------
replace_once(
    "app/main.py",
    'from .app_settings import AppSettingError, AppSettings\nfrom .auth import (\n',
    'from .app_settings import AppSettingError, AppSettings\nfrom .bootstrap import BootstrapTokenManager\nfrom .auth import (\n',
)
replace_once(
    "app/main.py",
    'from .provider_secrets import ProviderSecretError, ProviderSecretStore\nfrom .timezones import timezone_groups\n',
    'from .provider_secrets import ProviderSecretError, ProviderSecretStore\nfrom .request_security import RequestBodyTooLarge, csrf_submission, replay_body\nfrom .timezones import timezone_groups\n',
)
replace_once(
    "app/main.py",
    'auth_service = AuthService(db, settings)\napp_settings = AppSettings(db, settings.search_url_template)\n',
    'auth_service = AuthService(db, settings)\nbootstrap_tokens = BootstrapTokenManager(\n    settings.database.parent / "bootstrap-token", settings.bootstrap_token\n)\napp_settings = AppSettings(db, settings.search_url_template)\n',
)
replace_once(
    "app/main.py",
    '''            body = await request.body()
            form = await request.form()
            submitted = request.headers.get("x-csrf-token", "") or str(
                form.get("csrf_token") or ""
            )
            if not submitted or not hmac.compare_digest(submitted, session.csrf_token):
                return await finish(auth_error_response(
                    request, 403, "Request verification failed",
                    "Refresh the page and try the operation again.",
                ))
            # BaseHTTPMiddleware passes the downstream app a new Request. Replay
            # the verified body so FastAPI can still populate its Form fields.
            sent = False

            async def replay_body():
                nonlocal sent
                if sent:
                    return {"type": "http.request", "body": b"", "more_body": False}
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}

            request._receive = replay_body
''',
    '''            try:
                submitted, buffered_body = await csrf_submission(request)
            except RequestBodyTooLarge:
                return await finish(auth_error_response(
                    request, 413, "Request too large",
                    "This form submission is larger than InfoMancer accepts.",
                ))
            if not submitted or not hmac.compare_digest(submitted, session.csrf_token):
                return await finish(auth_error_response(
                    request, 403, "Request verification failed",
                    "Refresh the page and try the operation again.",
                ))
            if buffered_body is not None:
                replay_body(request, buffered_body)
''',
)
replace_once(
    "app/main.py",
    '''def setup_page(request: Request):
    if auth_service.user_count():
        return redirect("/login" if settings.auth_mode == "local" else "/")
    claims = getattr(request.state, "external_claims", {})
''',
    '''def setup_page(request: Request):
    if auth_service.user_count():
        bootstrap_tokens.clear()
        return redirect("/login" if settings.auth_mode == "local" else "/")
    if not settings.sandbox:
        bootstrap_tokens.token()
    claims = getattr(request.state, "external_claims", {})
''',
)
replace_once(
    "app/main.py",
    '''    password: str = Form(""), password_confirm: str = Form(""),
    preauth_token: str = Form(""),
):
    if auth_service.user_count():
        return redirect("/login")
    if not valid_preauth(request, preauth_token):
        return redirect("/setup", "Setup form expired. Please try again.")
    if settings.auth_mode == "local" and password != password_confirm:
''',
    '''    password: str = Form(""), password_confirm: str = Form(""),
    preauth_token: str = Form(""), bootstrap_token: str = Form(""),
):
    if auth_service.user_count():
        bootstrap_tokens.clear()
        return redirect("/login")
    if not valid_preauth(request, preauth_token):
        return redirect("/setup", "Setup form expired. Please try again.")
    if not settings.sandbox and not bootstrap_tokens.verify(bootstrap_token):
        return preauth_response(request, "setup.html", {
            "username": username, "email": email, "display_name": display_name,
            "requires_password": settings.auth_mode == "local",
            "error": "The first-run bootstrap token is incorrect. Check the server startup logs and try again.",
        })
    if settings.auth_mode == "local" and password != password_confirm:
''',
)
replace_once(
    "app/main.py",
    '''    welcome = quote_plus(
        f"Librarian account created successfully. Welcome, {user.display_name}!"
    )
''',
    '''    bootstrap_tokens.clear()
    welcome = quote_plus(
        f"Librarian account created successfully. Welcome, {user.display_name}!"
    )
''',
)
replace_once(
    "app/main.py",
    '        user = auth_service.authenticate_local(identity, password, request_ip(request))\n',
    '        user = auth_service.authenticate_local(identity, password, request_ip(request, settings))\n',
)

# --- UI support for streaming multipart forms and bootstrap secret ----------
replace_once(
    "app/templates/base.html",
    '  <script src="{{ url_for(\'static\', path=\'engagement.js\') }}?v={{ static_version }}" defer></script>\n',
    '  <script src="{{ url_for(\'static\', path=\'engagement.js\') }}?v={{ static_version }}" defer></script>\n  <script src="{{ url_for(\'static\', path=\'multipart-submit.js\') }}?v={{ static_version }}" defer></script>\n',
)
replace_once(
    "app/templates/setup.html",
    '    <input type="hidden" name="preauth_token" value="{{ preauth_token }}">\n    {% if error %}<div class="form-error">{{ error }}</div>{% endif %}\n',
    '    <input type="hidden" name="preauth_token" value="{{ preauth_token }}">\n    {% if error %}<div class="form-error">{{ error }}</div>{% endif %}\n    {% if not sandbox_mode %}<label>Bootstrap token<input name="bootstrap_token" type="password" autocomplete="off" required><small>Use the one-time token shown in the InfoMancer server startup logs, or the value configured with <code>INFOMANCER_BOOTSTRAP_TOKEN</code>.</small></label>{% endif %}\n',
)

# --- Deployment hardening ---------------------------------------------------
write(
    "Dockerfile",
    '''FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \\
    && apt-get install -y --no-install-recommends ffmpeg \\
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 1000 infomancer \\
    && useradd --uid 1000 --gid infomancer --create-home --shell /bin/false infomancer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=infomancer:infomancer app app
RUN mkdir -p /app/data && chown -R infomancer:infomancer /app/data
USER infomancer
EXPOSE 8787
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787", "--no-proxy-headers"]
''',
)
replace_once(
    ".env.example",
    'INFOMANCER_SECRET=\n# Authentication modes: local, cloudflare, or disabled. Disabled is intended\n',
    'INFOMANCER_SECRET=\n# Optional first-run override. When blank, InfoMancer generates a one-time\n# bootstrap token and prints it to the server startup logs until setup finishes.\nINFOMANCER_BOOTSTRAP_TOKEN=\n# Authentication modes: local, cloudflare, or disabled. Disabled is intended\n',
)

# --- Regression tests -------------------------------------------------------
write(
    "tests/test_security_hardening.py",
    '''from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from starlette.requests import Request

from app.auth import request_ip, secure_cookie_for
from app.bootstrap import BootstrapTokenManager
from app.config import Settings
from app.request_security import RequestBodyTooLarge, csrf_submission


def settings_for(path: Path, *, auth_mode: str = "local") -> Settings:
    return Settings(
        database=path / "test.db", tvdb_api_key="", tvdb_pin="",
        search_url_template="", media_browse_roots=(path,), auth_mode=auth_mode,
        session_days=14, cookie_secure="auto", cloudflare_team_domain="",
        cloudflare_audience="",
    )


def request_with(
    *, headers: dict[str, str] | None = None, client: str = "10.0.0.2",
    claims: dict | None = None, scheme: str = "http", body: bytes = b"",
) -> Request:
    encoded_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http", "http_version": "1.1", "method": "POST",
        "scheme": scheme, "path": "/", "raw_path": b"/", "query_string": b"",
        "headers": encoded_headers, "client": (client, 12345),
        "server": ("testserver", 80), "state": {},
    }
    request = Request(scope, receive)
    if claims is not None:
        request.state.external_claims = claims
    return request


class RequestSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_urlencoded_csrf_body_is_bounded_and_replayable(self):
        body = b"csrf_token=expected&value=ok"
        request = request_with(
            headers={
                "content-type": "application/x-www-form-urlencoded",
                "content-length": str(len(body)),
            },
            body=body,
        )
        token, buffered = await csrf_submission(request)
        self.assertEqual(token, "expected")
        self.assertEqual(buffered, body)

    async def test_large_urlencoded_form_is_rejected_before_buffering(self):
        request = request_with(
            headers={
                "content-type": "application/x-www-form-urlencoded",
                "content-length": str(3 * 1024 * 1024),
            },
        )
        with self.assertRaises(RequestBodyTooLarge):
            await csrf_submission(request)

    async def test_multipart_with_csrf_header_is_not_consumed(self):
        request = request_with(
            headers={
                "content-type": "multipart/form-data; boundary=example",
                "x-csrf-token": "header-token",
            },
            body=b"this body must stay unread",
        )
        token, buffered = await csrf_submission(request)
        self.assertEqual(token, "header-token")
        self.assertIsNone(buffered)
        message = await request.receive()
        self.assertEqual(message["body"], b"this body must stay unread")


class BootstrapTokenTests(unittest.TestCase):
    def test_generated_token_is_required_and_removed_after_setup(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bootstrap-token"
            manager = BootstrapTokenManager(path)
            token = manager.token()
            self.assertTrue(path.is_file())
            self.assertTrue(manager.verify(token))
            self.assertFalse(manager.verify("wrong-token"))
            manager.clear()
            self.assertFalse(path.exists())

    def test_configured_token_does_not_create_a_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bootstrap-token"
            manager = BootstrapTokenManager(path, "configured-secret")
            self.assertTrue(manager.verify("configured-secret"))
            self.assertFalse(path.exists())


class ForwardedHeaderTests(unittest.TestCase):
    def test_local_auth_ignores_forged_forwarded_headers(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = settings_for(Path(temporary), auth_mode="local")
            request = request_with(
                headers={
                    "cf-connecting-ip": "203.0.113.50",
                    "x-forwarded-for": "203.0.113.51",
                    "x-forwarded-proto": "https",
                },
                client="10.10.10.10",
            )
            self.assertEqual(request_ip(request, settings), "10.10.10.10")
            self.assertFalse(secure_cookie_for(request, settings))

    def test_verified_cloudflare_request_can_use_cloudflare_headers(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = settings_for(Path(temporary), auth_mode="cloudflare")
            request = request_with(
                headers={
                    "cf-connecting-ip": "203.0.113.50",
                    "x-forwarded-proto": "https",
                },
                client="172.20.0.4", claims={"sub": "verified-user"},
            )
            self.assertEqual(request_ip(request, settings), "203.0.113.50")
            self.assertTrue(secure_cookie_for(request, settings))

    def test_invalid_cloudflare_ip_falls_back_to_socket_peer(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = settings_for(Path(temporary), auth_mode="cloudflare")
            request = request_with(
                headers={"cf-connecting-ip": "not-an-ip"},
                client="172.20.0.4", claims={"sub": "verified-user"},
            )
            self.assertEqual(request_ip(request, settings), "172.20.0.4")


class ContainerHardeningTests(unittest.TestCase):
    def test_docker_process_is_non_root_and_proxy_headers_are_disabled(self):
        dockerfile = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text(
            encoding="utf-8"
        )
        self.assertIn("USER infomancer", dockerfile)
        self.assertIn('"--no-proxy-headers"', dockerfile)
        self.assertNotIn('"--forwarded-allow-ips", "*"', dockerfile)


if __name__ == "__main__":
    unittest.main()
''',
)

print("Security hardening patch applied successfully.")

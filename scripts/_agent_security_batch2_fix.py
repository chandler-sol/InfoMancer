from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}: {old[:100]!r}")
    write(path, content.replace(old, new, 1))


# Browser-origin checking protects auth-disabled installations from cross-site
# form submissions without breaking trusted CLI/API callers that do not speak
# browser Fetch Metadata or Origin headers.
replace_once(
    "app/request_security.py",
    '''def host_is_allowed(request: Request, settings) -> bool:
    """Reject DNS-rebinding/unexpected Host values when an allowlist applies."""
    enforce = (
        settings.auth_mode == "disabled"
        or bool(settings.trusted_hosts)
        or bool(settings.public_url)
    )
    if not enforce:
        return True
    return _hostname(request.headers.get("host", "")) in allowed_hosts(settings)


async def csrf_submission(
''',
    '''def host_is_allowed(request: Request, settings) -> bool:
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
''',
)

# Persist failed login counters before raising. Database.connect() rolls back on
# exceptions, so raising from inside the write transaction made the previous
# limiter effectively forget every failed attempt.
auth = read("app/auth.py")
start = auth.index("    def authenticate_local(self, identity: str, password: str, ip_address: str) -> AuthUser:\n")
end = auth.index("    def create_session(self, user: AuthUser, request) -> tuple[str, AuthSession]:\n", start)
method = '''    def authenticate_local(self, identity: str, password: str, ip_address: str) -> AuthUser:
        identity = identity.strip().casefold()
        if not identity or not password:
            raise AuthenticationError("Incorrect username, email, or password.")

        failure = False
        with self.database.connect() as conn:
            conn.execute(
                "DELETE FROM login_attempts WHERE datetime(last_attempt_at)<datetime('now','-1 day')"
            )
            conn.execute(
                """DELETE FROM login_attempts WHERE rowid IN (
                     SELECT rowid FROM login_attempts
                     ORDER BY datetime(last_attempt_at) DESC,rowid DESC
                     LIMIT -1 OFFSET 5000
                   )"""
            )
            attempt = conn.execute(
                "SELECT * FROM login_attempts WHERE identity=? AND ip_address=?",
                (identity, ip_address),
            ).fetchone()
            if attempt and attempt["locked_until"]:
                try:
                    if datetime.fromisoformat(attempt["locked_until"]) > utcnow():
                        raise LoginLocked("Too many attempts. Try again in a few minutes.")
                except ValueError:
                    pass
            identity_failures = int(conn.execute(
                """SELECT COALESCE(SUM(failures),0) FROM login_attempts
                   WHERE identity=? AND datetime(last_attempt_at)>=datetime('now','-15 minutes')""",
                (identity,),
            ).fetchone()[0])
            ip_failures = int(conn.execute(
                """SELECT COALESCE(SUM(failures),0) FROM login_attempts
                   WHERE ip_address=? AND datetime(last_attempt_at)>=datetime('now','-15 minutes')""",
                (ip_address,),
            ).fetchone()[0])
            if identity_failures >= 15 or ip_failures >= 30:
                raise LoginLocked("Too many attempts. Try again in a few minutes.")

            row = conn.execute(
                """SELECT * FROM users
                   WHERE LOWER(username)=? OR LOWER(COALESCE(email,''))=?""",
                (identity, identity),
            ).fetchone()
            if row and not row["active"]:
                raise AuthenticationError(
                    "This account is disabled. Ask a Librarian to enable it before signing in."
                )
            if row and not row["password_hash"]:
                raise AuthenticationError(
                    "This account is waiting for setup. Ask a Librarian for a fresh one-time setup link."
                )

            verified = False
            if row and row["password_hash"]:
                try:
                    verified = password_hasher.verify(row["password_hash"], password)
                    if verified and password_hasher.check_needs_rehash(row["password_hash"]):
                        conn.execute(
                            "UPDATE users SET password_hash=? WHERE id=?",
                            (password_hasher.hash(password), row["id"]),
                        )
                except (VerifyMismatchError, VerificationError, InvalidHashError):
                    verified = False

            if not verified:
                failures = int(attempt["failures"] if attempt else 0) + 1
                locked_until = (
                    iso_timestamp(utcnow() + timedelta(minutes=15)) if failures >= 5 else None
                )
                conn.execute(
                    """INSERT INTO login_attempts
                       (identity,ip_address,failures,last_attempt_at,locked_until)
                       VALUES (?,?,?,CURRENT_TIMESTAMP,?)
                       ON CONFLICT(identity,ip_address) DO UPDATE SET
                         failures=excluded.failures,last_attempt_at=CURRENT_TIMESTAMP,
                         locked_until=excluded.locked_until""",
                    (identity, ip_address, failures, locked_until),
                )
                conn.execute(
                    """DELETE FROM login_attempts WHERE rowid IN (
                         SELECT rowid FROM login_attempts
                         ORDER BY datetime(last_attempt_at) DESC,rowid DESC
                         LIMIT -1 OFFSET 5000
                       )"""
                )
                failure = True
            else:
                conn.execute("DELETE FROM login_attempts WHERE identity=?", (identity,))
                conn.execute(
                    "UPDATE users SET last_login_at=CURRENT_TIMESTAMP WHERE id=?",
                    (row["id"],),
                )
                refreshed = conn.execute(
                    "SELECT * FROM users WHERE id=?", (row["id"],)
                ).fetchone()

        if failure:
            raise AuthenticationError("Incorrect username, email, or password.")
        return user_from_row(refreshed)

'''
write("app/auth.py", auth[:start] + method + auth[end:])

replace_once(
    "app/main.py",
    '''from .request_security import (
    LOCAL_CSRF_COOKIE, RequestBodyTooLarge, csrf_submission, host_is_allowed,
    replay_body,
)
''',
    '''from .request_security import (
    LOCAL_CSRF_COOKIE, RequestBodyTooLarge, browser_request_is_same_origin,
    csrf_submission, host_is_allowed, replay_body,
)
''',
)

# Replace the common unsafe-request verification block. Logged-in auth still
# requires the session CSRF token. Auth-disabled mode instead rejects browser
# cross-site requests, validates a local token when one is supplied, and keeps
# non-browser local API compatibility.
main = read("app/main.py")
old = '''            expected_csrf = (
                session.csrf_token if session else
                getattr(request.state, "local_csrf_token", "")
            )
            if not expected_csrf:
                return await finish(auth_error_response(
                    request, 403, "Session required", "Start a fresh session and try again."
                ))
            try:
                submitted, buffered_body = await csrf_submission(request)
            except RequestBodyTooLarge:
                return await finish(auth_error_response(
                    request, 413, "Request too large",
                    "This form submission is larger than InfoMancer accepts.",
                ))
            if not submitted or not hmac.compare_digest(submitted, expected_csrf):
                return await finish(auth_error_response(
                    request, 403, "Request verification failed",
                    "Refresh the page and try the operation again.",
                ))
            if buffered_body is not None:
                replay_body(request, buffered_body)
'''
new = '''            if settings.auth_mode == "disabled":
                if not browser_request_is_same_origin(request, settings):
                    return await finish(auth_error_response(
                        request, 403, "Cross-site request blocked",
                        "Open InfoMancer directly and try the operation again.",
                    ))
                try:
                    submitted, buffered_body = await csrf_submission(request)
                except RequestBodyTooLarge:
                    return await finish(auth_error_response(
                        request, 413, "Request too large",
                        "This form submission is larger than InfoMancer accepts.",
                    ))
                local_csrf = getattr(request.state, "local_csrf_token", "")
                if submitted and (
                    not local_csrf
                    or not hmac.compare_digest(submitted, local_csrf)
                ):
                    return await finish(auth_error_response(
                        request, 403, "Request verification failed",
                        "Refresh the page and try the operation again.",
                    ))
                if buffered_body is not None:
                    replay_body(request, buffered_body)
            else:
                if not session:
                    return await finish(auth_error_response(
                        request, 403, "Session required", "Start a fresh session and try again."
                    ))
                try:
                    submitted, buffered_body = await csrf_submission(request)
                except RequestBodyTooLarge:
                    return await finish(auth_error_response(
                        request, 413, "Request too large",
                        "This form submission is larger than InfoMancer accepts.",
                    ))
                if not submitted or not hmac.compare_digest(
                    submitted, session.csrf_token
                ):
                    return await finish(auth_error_response(
                        request, 403, "Request verification failed",
                        "Refresh the page and try the operation again.",
                    ))
                if buffered_body is not None:
                    replay_body(request, buffered_body)
'''
if main.count(old) != 1:
    raise RuntimeError(f"Expected unsafe verification block once, found {main.count(old)}")
write("app/main.py", main.replace(old, new, 1))

replace_once(
    "tests/test_security_hardening.py",
    '''from app.request_security import RequestBodyTooLarge, csrf_submission, host_is_allowed
''',
    '''from app.request_security import (
    RequestBodyTooLarge, browser_request_is_same_origin, csrf_submission,
    host_is_allowed,
)
''',
)
replace_once(
    "tests/test_security_hardening.py",
    '''    def test_invalid_cloudflare_ip_falls_back_to_socket_peer(self):
''',
    '''    def test_disabled_mode_rejects_cross_site_browser_origin(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = settings_for(Path(temporary), auth_mode="disabled")
            cross_site = request_with(headers={
                "host": "127.0.0.1:8787",
                "origin": "https://attacker.example",
                "sec-fetch-site": "cross-site",
            })
            same_origin = request_with(headers={
                "host": "127.0.0.1:8787",
                "origin": "http://127.0.0.1:8787",
                "sec-fetch-site": "same-origin",
            })
            self.assertFalse(browser_request_is_same_origin(cross_site, settings))
            self.assertTrue(browser_request_is_same_origin(same_origin, settings))

    def test_invalid_cloudflare_ip_falls_back_to_socket_peer(self):
''',
)

replace_once(
    "tests/test_zz_auth_flow.py",
    '''                rejected = client.post("/account/home-layout", data={})
                self.assertEqual(rejected.status_code, 403)
                accepted = client.post(
                    "/account/home-layout", data={"csrf_token": token}
                )
                self.assertEqual(accepted.status_code, 303)
                bad_host = client.get("/", headers={"host": "attacker.example"})
''',
    '''                accepted_without_browser_metadata = client.post(
                    "/account/home-layout", data={}
                )
                self.assertEqual(accepted_without_browser_metadata.status_code, 303)
                rejected = client.post(
                    "/account/home-layout", data={},
                    headers={
                        "origin": "https://attacker.example",
                        "sec-fetch-site": "cross-site",
                    },
                )
                self.assertEqual(rejected.status_code, 403)
                accepted = client.post(
                    "/account/home-layout", data={"csrf_token": token},
                    headers={
                        "origin": "http://testserver",
                        "sec-fetch-site": "same-origin",
                    },
                )
                self.assertEqual(accepted.status_code, 303)
                bad_host = client.get("/", headers={"host": "attacker.example"})
''',
)

print("Security batch compatibility fixes applied successfully.")

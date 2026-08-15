from __future__ import annotations

import textwrap
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


def replace_count(path: str, old: str, new: str, expected: int) -> None:
    content = read(path)
    count = content.count(old)
    if count != expected:
        raise RuntimeError(f"Expected {expected} matches in {path}, found {count}: {old[:100]!r}")
    write(path, content.replace(old, new))


# ---------------------------------------------------------------------------
# Dependency floor: move off the vulnerable Starlette 0.47.x resolution and
# pin the patched Starlette release explicitly so future resolver behavior
# cannot drift back to a known-vulnerable version.
# ---------------------------------------------------------------------------
write(
    "requirements.txt",
    """fastapi==0.136.3
starlette==1.3.1
argon2-cffi==25.1.0
cryptography==50.0.0
httpx==0.28.1
jinja2==3.1.6
PyJWT[crypto]==2.13.0
python-multipart==0.0.31
python-dotenv==1.2.2
tzdata==2026.3
uvicorn[standard]==0.35.0
""",
)

# ---------------------------------------------------------------------------
# Runtime configuration for canonical public URLs, exact Host allowlisting,
# and explicitly opted-in Cloudflare proxy metadata trust.
# ---------------------------------------------------------------------------
write(
    "app/config.py",
    '''from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)


def _enabled(value: str) -> bool:
    return value.strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database: Path
    tvdb_api_key: str
    tvdb_pin: str
    search_url_template: str
    media_browse_roots: tuple[Path, ...]
    auth_mode: str
    session_days: int
    cookie_secure: str
    cloudflare_team_domain: str
    cloudflare_audience: str
    application_secret: str = ""
    sandbox: bool = False
    bootstrap_token: str = ""
    public_url: str = ""
    trusted_hosts: tuple[str, ...] = ()
    trust_cloudflare_proxy: bool = False

    @property
    def minimum_password_length(self) -> int:
        return 1 if self.sandbox else 12


def get_settings() -> Settings:
    db = Path(os.getenv("INFOMANCER_DATABASE", "data/infomancer.db"))
    if not db.is_absolute():
        db = BASE_DIR / db
    browse_values = os.getenv("MEDIA_BROWSE_ROOTS", "/media")
    browse_roots = tuple(
        Path(value.strip()) for value in browse_values.split(",") if value.strip()
    )
    auth_mode = os.getenv("INFOMANCER_AUTH_MODE", "local").strip().casefold()
    if auth_mode not in {"local", "cloudflare", "disabled"}:
        auth_mode = "local"
    cookie_secure = os.getenv("INFOMANCER_COOKIE_SECURE", "auto").strip().casefold()
    if cookie_secure not in {"auto", "true", "false"}:
        cookie_secure = "auto"
    try:
        session_days = max(1, min(90, int(os.getenv("INFOMANCER_SESSION_DAYS", "14"))))
    except ValueError:
        session_days = 14
    trusted_hosts = tuple(
        value.strip().casefold().rstrip(".")
        for value in os.getenv("INFOMANCER_TRUSTED_HOSTS", "").split(",")
        if value.strip()
    )
    return Settings(
        database=db,
        tvdb_api_key=os.getenv("TVDB_API_KEY", "").strip(),
        tvdb_pin=os.getenv("TVDB_PIN", "").strip(),
        search_url_template=os.getenv(
            "SEARCH_URL_TEMPLATE", "https://ext.to/browse/?q={query}"
        ),
        media_browse_roots=browse_roots,
        auth_mode=auth_mode,
        session_days=session_days,
        cookie_secure=cookie_secure,
        cloudflare_team_domain=os.getenv("CF_ACCESS_TEAM_DOMAIN", "").strip().rstrip("/"),
        cloudflare_audience=os.getenv("CF_ACCESS_AUD", "").strip(),
        application_secret=os.getenv("INFOMANCER_SECRET", "").strip(),
        sandbox=_enabled(os.getenv("INFOMANCER_SANDBOX", "")),
        bootstrap_token=os.getenv("INFOMANCER_BOOTSTRAP_TOKEN", "").strip(),
        public_url=os.getenv("INFOMANCER_PUBLIC_URL", "").strip().rstrip("/"),
        trusted_hosts=trusted_hosts,
        trust_cloudflare_proxy=_enabled(
            os.getenv("INFOMANCER_TRUST_CLOUDFLARE_PROXY", "")
        ),
    )
''',
)

# ---------------------------------------------------------------------------
# Host validation and CSRF helpers. Disabled authentication still receives a
# double-submit token, so "no login" does not also mean "no browser boundary".
# ---------------------------------------------------------------------------
write(
    "app/request_security.py",
    '''from __future__ import annotations

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
''',
)

# ---------------------------------------------------------------------------
# Authentication: explicit proxy trust, canonical HTTPS cookie behavior,
# atomic first Librarian creation, and layered login throttling with pruning.
# ---------------------------------------------------------------------------
replace_once(
    "app/auth.py",
    "from typing import Any\n",
    "from typing import Any\nfrom urllib.parse import urlsplit\n",
)
replace_once(
    "app/auth.py",
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
    '''def _verified_cloudflare_request(request, settings: Settings) -> bool:
    claims = getattr(getattr(request, "state", None), "external_claims", None)
    return settings.auth_mode == "cloudflare" and bool(claims)


def _trusted_cloudflare_proxy(request, settings: Settings) -> bool:
    return settings.trust_cloudflare_proxy or _verified_cloudflare_request(
        request, settings
    )


def secure_cookie_for(request, settings: Settings) -> bool:
    if settings.cookie_secure == "true":
        return True
    if settings.cookie_secure == "false":
        return False
    if settings.public_url:
        try:
            if urlsplit(settings.public_url).scheme.casefold() == "https":
                return True
        except ValueError:
            pass
    if getattr(getattr(request, "url", None), "scheme", "") == "https":
        return True
    if _trusted_cloudflare_proxy(request, settings):
        forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
        return forwarded == "https"
    return False


def request_ip(request, settings: Settings | None = None) -> str:
    if settings is not None and _trusted_cloudflare_proxy(request, settings):
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
    '''    def get_user(self, user_id: int) -> AuthUser | None:
''',
    '''    def create_initial_librarian(
        self, username: str, email: str, display_name: str, password: str = "",
        profile_icon: str = "initials", *, require_password: bool = True,
        provider: str = "", subject: str = "", identity_email: str = "",
    ) -> AuthUser:
        """Create the first Librarian and optional external identity atomically."""
        username, email, display_name = self.validate_account_fields(
            username, email, display_name, password,
            require_password=require_password,
        )
        if profile_icon not in PROFILE_ICONS:
            profile_icon = "initials"
        provider = provider.strip().casefold()
        subject = subject.strip()
        if provider and not subject:
            raise AuthenticationError("The external sign-in identity is incomplete.")
        try:
            with self.database.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
                    raise AuthenticationError(
                        "First-run setup has already been completed."
                    )
                user_id = conn.execute(
                    """INSERT INTO users
                       (username,email,display_name,profile_icon,password_hash,role,
                        force_password_change,password_changed_at)
                       VALUES (?,?,?,?,?,'librarian',0,CURRENT_TIMESTAMP)""",
                    (
                        username, email or None, display_name, profile_icon,
                        password_hasher.hash(password) if password else None,
                    ),
                ).lastrowid
                if provider:
                    conn.execute(
                        """INSERT INTO auth_identities(user_id,provider,subject,email)
                           VALUES (?,?,?,?)""",
                        (
                            user_id, provider, subject,
                            normalize_email(identity_email or email) or None,
                        ),
                    )
                row = conn.execute(
                    "SELECT * FROM users WHERE id=?", (user_id,)
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise AuthenticationError(
                "The first Librarian account could not be created because its identity conflicts with existing setup data."
            ) from exc
        return user_from_row(row)

    def get_user(self, user_id: int) -> AuthUser | None:
''',
)

content = read("app/auth.py")
start = content.index("    def authenticate_local(self, identity: str, password: str, ip_address: str) -> AuthUser:\n")
end = content.index("    def create_session(self, user: AuthUser, request) -> tuple[str, AuthSession]:\n", start)
new_authenticate = '''    def authenticate_local(self, identity: str, password: str, ip_address: str) -> AuthUser:
        identity = identity.strip().casefold()
        if not identity or not password:
            raise AuthenticationError("Incorrect username, email, or password.")
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
                raise AuthenticationError("Incorrect username, email, or password.")
            conn.execute("DELETE FROM login_attempts WHERE identity=?", (identity,))
            conn.execute(
                "UPDATE users SET last_login_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],)
            )
            refreshed = conn.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
        return user_from_row(refreshed)

'''
write("app/auth.py", content[:start] + new_authenticate + content[end:])

# ---------------------------------------------------------------------------
# Database restore: structural integrity plus semantic filesystem boundaries.
# Existing live roots are grandfathered, while new roots must sit beneath an
# administrator-configured MEDIA_BROWSE_ROOTS location.
# ---------------------------------------------------------------------------
replace_once(
    "app/maintenance.py",
    '''SAFE_BACKUP_NAME = re.compile(
    r"^infomancer-backup-\\d{8}-\\d{6}(?:-[a-z-]+)?(?:-\\d+)?\\.db$"
)
''',
    '''SAFE_BACKUP_NAME = re.compile(
    r"^infomancer-backup-\\d{8}-\\d{6}(?:-[a-z-]+)?(?:-\\d+)?\\.db$"
)
SAFE_ARTWORK_NAME = re.compile(r"^[0-9a-f]{40}\\.(?:jpg|png|webp)$")
''',
)
replace_once(
    "app/maintenance.py",
    '''        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        tables = {
''',
    '''        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        foreign_key_error = connection.execute("PRAGMA foreign_key_check").fetchone()
        tables = {
''',
)
replace_once(
    "app/maintenance.py",
    '''    if not integrity or integrity[0] != "ok":
        raise MaintenanceError(
            "The selected database did not pass SQLite's integrity check."
        )
    missing = REQUIRED_TABLES - tables
''',
    '''    if not integrity or integrity[0] != "ok":
        raise MaintenanceError(
            "The selected database did not pass SQLite's integrity check."
        )
    if foreign_key_error:
        raise MaintenanceError(
            "The selected database contains broken catalog relationships."
        )
    missing = REQUIRED_TABLES - tables
''',
)
replace_once(
    "app/maintenance.py",
    '''def list_database_backups(database_path: Path) -> list[dict]:
''',
    '''def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def _database_roots(database_path: Path) -> tuple[Path, ...]:
    try:
        with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True) as connection:
            return tuple(
                Path(row[0]) for row in connection.execute("SELECT path FROM roots")
                if row[0]
            )
    except sqlite3.Error:
        return ()


def validate_database_paths(
    path: Path, media_browse_roots: tuple[Path, ...],
    existing_roots: tuple[Path, ...] = (),
) -> None:
    """Reject restored catalog paths that escape already-trusted storage."""
    allowed_parents = tuple(root.resolve(strict=False) for root in media_browse_roots)
    grandfathered = {root.resolve(strict=False) for root in existing_roots}
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        roots: dict[int, Path] = {}
        for row in connection.execute("SELECT id,path FROM roots"):
            root = Path(row["path"] or "")
            resolved = root.resolve(strict=False)
            if not root.is_absolute() or (
                resolved not in grandfathered
                and not any(_inside(resolved, parent) for parent in allowed_parents)
            ):
                raise MaintenanceError(
                    "The selected backup contains a media root outside the storage locations this installation trusts."
                )
            roots[int(row["id"])] = root

        for row in connection.execute("SELECT root_id,folder_path FROM titles"):
            root = roots.get(int(row["root_id"]))
            candidate = Path(row["folder_path"] or "")
            if root is None or not candidate.is_absolute() or not _inside(candidate, root):
                raise MaintenanceError(
                    "The selected backup contains a title path outside its configured media root."
                )

        for row in connection.execute(
            """SELECT f.path,t.root_id FROM files f JOIN titles t ON t.id=f.title_id"""
        ):
            root = roots.get(int(row["root_id"]))
            candidate = Path(row["path"] or "")
            if root is None or not candidate.is_absolute() or not _inside(candidate, root):
                raise MaintenanceError(
                    "The selected backup contains a media-file path outside its configured root."
                )

        if "duplicate_trash" in tables:
            for row in connection.execute(
                "SELECT root_id,original_path,trash_path FROM duplicate_trash"
            ):
                root = roots.get(int(row["root_id"])) if row["root_id"] is not None else None
                original = Path(row["original_path"] or "")
                trash = Path(row["trash_path"] or "")
                if (
                    root is None or not original.is_absolute() or not trash.is_absolute()
                    or not _inside(original, root)
                    or not _inside(trash, root / ".infomancer-trash")
                ):
                    raise MaintenanceError(
                        "The selected backup contains managed-trash paths outside a configured media root."
                    )

        if "collections" in tables:
            for row in connection.execute(
                "SELECT artwork_filename FROM collections WHERE artwork_filename IS NOT NULL AND artwork_filename<>''"
            ):
                if not SAFE_ARTWORK_NAME.fullmatch(str(row["artwork_filename"])):
                    raise MaintenanceError(
                        "The selected backup contains an invalid collection artwork filename."
                    )
    except sqlite3.Error as exc:
        raise MaintenanceError(
            "The selected database could not be checked for safe filesystem paths."
        ) from exc
    finally:
        connection.close()


def list_database_backups(database_path: Path) -> list[dict]:
''',
)
replace_once(
    "app/maintenance.py",
    '''def install_database_backup(database_path: Path, candidate: Path) -> Path:
    validate_database_backup(candidate)
    safety_backup = create_database_backup(database_path, "before-restore")
''',
    '''def install_database_backup(
    database_path: Path, candidate: Path,
    media_browse_roots: tuple[Path, ...] | None = None,
) -> Path:
    validate_database_backup(candidate)
    existing_roots = _database_roots(database_path)
    if media_browse_roots is not None:
        validate_database_paths(candidate, media_browse_roots, existing_roots)
    safety_backup = create_database_backup(database_path, "before-restore")
''',
)
replace_once(
    "app/maintenance.py",
    '''        validate_database_backup(staged)
        for suffix in ("-wal", "-shm"):
''',
    '''        validate_database_backup(staged)
        if media_browse_roots is not None:
            validate_database_paths(staged, media_browse_roots, existing_roots)
        for suffix in ("-wal", "-shm"):
''',
)

# ---------------------------------------------------------------------------
# Main application middleware: exact Host boundary, disabled-mode CSRF,
# canonical invitation URLs, atomic setup, semantic restore checks, CSV safety.
# ---------------------------------------------------------------------------
replace_once(
    "app/main.py",
    "from .request_security import RequestBodyTooLarge, csrf_submission, replay_body\n",
    "from .request_security import (\n    LOCAL_CSRF_COOKIE, RequestBodyTooLarge, csrf_submission, host_is_allowed,\n    replay_body,\n)\n",
)
replace_once(
    "app/main.py",
    '''        "csrf_token": getattr(getattr(request.state, "auth_session", None), "csrf_token", ""),
''',
    '''        "csrf_token": (
            getattr(getattr(request.state, "auth_session", None), "csrf_token", "")
            or getattr(request.state, "local_csrf_token", "")
        ),
''',
)
replace_once(
    "app/main.py",
    '''    new_session_token = ""

    async def finish(response):
        if new_session_token:
            set_session_cookie(response, request, new_session_token)
''',
    '''    new_session_token = ""
    new_local_csrf_token = ""

    async def finish(response):
        if new_session_token:
            set_session_cookie(response, request, new_session_token)
        if new_local_csrf_token:
            response.set_cookie(
                LOCAL_CSRF_COOKIE, new_local_csrf_token, httponly=True,
                secure=secure_cookie_for(request, settings), samesite="strict",
                path="/",
            )
''',
)
replace_once(
    "app/main.py",
    '''    if path.startswith("/static/") or path == "/health":
        return await finish(await call_next(request))

    if settings.auth_mode == "disabled":
        request.state.user = AuthUser(
            id=0, username="local", email="", display_name="Local Librarian",
            profile_icon="library", role="librarian", active=True,
            force_password_change=False, last_login_at="",
        )
        return await finish(await call_next(request))

    users_exist = auth_service.user_count() > 0
''',
    '''    if not host_is_allowed(request, settings):
        return await finish(Response(
            "Invalid Host header", status_code=400, media_type="text/plain"
        ))

    if path.startswith("/static/") or path == "/health":
        return await finish(await call_next(request))

    if settings.auth_mode == "disabled":
        request.state.user = AuthUser(
            id=0, username="local", email="", display_name="Local Librarian",
            profile_icon="library", role="librarian", active=True,
            force_password_change=False, last_login_at="",
        )
        local_csrf = request.cookies.get(LOCAL_CSRF_COOKIE, "")
        if not local_csrf:
            local_csrf = secrets.token_urlsafe(32)
            new_local_csrf_token = local_csrf
        request.state.local_csrf_token = local_csrf

    users_exist = auth_service.user_count() > 0
''',
)

# The normal local/Cloudflare authentication block must not run when auth is
# disabled. Indent only the block between users_exist and the common authz path.
main_text = read("app/main.py")
start = main_text.index("    users_exist = auth_service.user_count() > 0\n")
end = main_text.index("    user = getattr(request.state, \"user\", None)\n", start)
block = main_text[start:end]
wrapped = "    if settings.auth_mode != \"disabled\":\n" + textwrap.indent(block, "    ")
write("app/main.py", main_text[:start] + wrapped + main_text[end:])

replace_once(
    "app/main.py",
    '''            if not session:
                return await finish(auth_error_response(
                    request, 403, "Session required", "Start a fresh session and try again."
                ))
            try:
                submitted, buffered_body = await csrf_submission(request)
''',
    '''            expected_csrf = (
                session.csrf_token if session else
                getattr(request.state, "local_csrf_token", "")
            )
            if not expected_csrf:
                return await finish(auth_error_response(
                    request, 403, "Session required", "Start a fresh session and try again."
                ))
            try:
                submitted, buffered_body = await csrf_submission(request)
''',
)
replace_once(
    "app/main.py",
    '''            if not submitted or not hmac.compare_digest(submitted, session.csrf_token):
''',
    '''            if not submitted or not hmac.compare_digest(submitted, expected_csrf):
''',
)

replace_once(
    "app/main.py",
    '''def user_admin_context(
''',
    '''def public_activation_url(request: Request, token: str) -> str:
    generated = request.url_for("activate_page", token=token)
    if settings.public_url:
        return settings.public_url.rstrip("/") + generated.path
    return str(generated)


def user_admin_context(
''',
)
replace_count(
    "app/main.py",
    'invitation_url = str(request.url_for("activate_page", token=raw_token))',
    'invitation_url = public_activation_url(request, raw_token)',
    2,
)

replace_once(
    "app/main.py",
    '''    try:
        user = auth_service.create_user(
            username, email, display_name, password, role="librarian",
            profile_icon=profile_icon,
            require_password=settings.auth_mode == "local",
        )
        if settings.auth_mode == "cloudflare":
            claims = getattr(request.state, "external_claims", {})
            subject = str(claims.get("sub") or "")
            if not subject:
                raise AuthenticationError("Cloudflare identity is missing a subject.")
            auth_service.link_identity(user.id, "cloudflare", subject, email)
    except AuthenticationError as exc:
''',
    '''    try:
        claims = getattr(request.state, "external_claims", {})
        provider = "cloudflare" if settings.auth_mode == "cloudflare" else ""
        subject = str(claims.get("sub") or "") if provider else ""
        user = auth_service.create_initial_librarian(
            username, email, display_name, password,
            profile_icon=profile_icon,
            require_password=settings.auth_mode == "local",
            provider=provider, subject=subject,
            identity_email=str(claims.get("email") or email),
        )
    except AuthenticationError as exc:
''',
)
replace_once(
    "app/main.py",
    "safety = install_database_backup(db.path, candidate)",
    "safety = install_database_backup(db.path, candidate, settings.media_browse_roots)",
)
replace_once(
    "app/main.py",
    "safety = install_database_backup(db.path, candidate_path)",
    "safety = install_database_backup(db.path, candidate_path, settings.media_browse_roots)",
)

replace_once(
    "app/main.py",
    '''LIBRARY_EXPORT_FIELDS = [
''',
    '''def csv_safe_row(row) -> dict:
    safe = {}
    for key, value in dict(row).items():
        if isinstance(value, str) and value.lstrip(" \\t\\r\\n")[:1] in {"=", "+", "-", "@"}:
            value = "'" + value
        safe[key] = value
    return safe


LIBRARY_EXPORT_FIELDS = [
''',
)
replace_once(
    "app/main.py",
    "            writer.writerows(rows)\n",
    "            writer.writerows(csv_safe_row(row) for row in rows)\n",
)
replace_once(
    "app/main.py",
    "    writer.writerows(dict(row) for row in rows)\n",
    "    writer.writerows(csv_safe_row(row) for row in rows)\n",
)

# ---------------------------------------------------------------------------
# Operator-facing configuration and remote-access documentation.
# ---------------------------------------------------------------------------
replace_once(
    ".env.example",
    '''INFOMANCER_COOKIE_SECURE=auto
# Required only when INFOMANCER_AUTH_MODE=cloudflare.
''',
    '''INFOMANCER_COOKIE_SECURE=auto
# Recommended for any reverse-proxied installation. Used for Secure-cookie
# decisions, generated invitation links, and Host validation.
INFOMANCER_PUBLIC_URL=
# Optional comma-separated exact hostnames accepted by the web app. Localhost
# and loopback remain accepted for health checks and local administration.
INFOMANCER_TRUSTED_HOSTS=
# Set true only when the origin is private and all non-loopback traffic reaches
# InfoMancer through Cloudflare. This allows CF-Connecting-IP and forwarded
# HTTPS metadata to be trusted even when InfoMancer itself uses local accounts.
INFOMANCER_TRUST_CLOUDFLARE_PROXY=false
# Required only when INFOMANCER_AUTH_MODE=cloudflare.
''',
)

remote = read("docs/REMOTE_ACCESS.md")
anchor = '''This outer policy can protect InfoMancer while the application continues using
local accounts. To make Cloudflare the application sign-in authority too, set:
'''
replacement = '''This outer policy can protect InfoMancer while the application continues using
local accounts. For that layout, tell InfoMancer its canonical HTTPS address and
explicitly trust Cloudflare proxy metadata:

```dotenv
INFOMANCER_PUBLIC_URL=https://infomancer.example.com
INFOMANCER_TRUSTED_HOSTS=infomancer.example.com
INFOMANCER_TRUST_CLOUDFLARE_PROXY=true
```

Only enable `INFOMANCER_TRUST_CLOUDFLARE_PROXY` while the origin remains private
(loopback-only or otherwise unreachable except through the trusted connector).
It lets local-account installations use the real Cloudflare client IP and HTTPS
scheme without trusting arbitrary forwarded headers from direct clients.

To make Cloudflare the application sign-in authority too, set:
'''
if remote.count(anchor) != 1:
    raise RuntimeError("Remote access documentation anchor changed")
remote = remote.replace(anchor, replacement, 1)
remote = remote.replace(
    "The first verified visitor completes Librarian setup. Afterward, a Librarian\n",
    "The first verified visitor must also enter the one-time bootstrap token shown in the InfoMancer server logs to complete Librarian setup. Afterward, a Librarian\n",
    1,
)
write("docs/REMOTE_ACCESS.md", remote)

# ---------------------------------------------------------------------------
# Regression coverage.
# ---------------------------------------------------------------------------
replace_once(
    "tests/test_security_hardening.py",
    '''from app.request_security import RequestBodyTooLarge, csrf_submission
''',
    '''from app.request_security import RequestBodyTooLarge, csrf_submission, host_is_allowed
''',
)
replace_once(
    "tests/test_security_hardening.py",
    '''    def test_invalid_cloudflare_ip_falls_back_to_socket_peer(self):
''',
    '''    def test_local_auth_can_explicitly_trust_private_cloudflare_proxy(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = settings_for(Path(temporary), auth_mode="local")
            settings = Settings(**{
                **settings.__dict__,
                "public_url": "https://media.example.test",
                "trusted_hosts": ("media.example.test",),
                "trust_cloudflare_proxy": True,
            })
            request = request_with(
                headers={
                    "host": "media.example.test",
                    "cf-connecting-ip": "203.0.113.60",
                    "x-forwarded-proto": "https",
                },
                client="172.20.0.4",
            )
            self.assertEqual(request_ip(request, settings), "203.0.113.60")
            self.assertTrue(secure_cookie_for(request, settings))
            self.assertTrue(host_is_allowed(request, settings))

    def test_disabled_auth_rejects_untrusted_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = settings_for(Path(temporary), auth_mode="disabled")
            self.assertFalse(host_is_allowed(
                request_with(headers={"host": "attacker.example"}), settings
            ))
            self.assertTrue(host_is_allowed(
                request_with(headers={"host": "127.0.0.1:8787"}), settings
            ))

    def test_invalid_cloudflare_ip_falls_back_to_socket_peer(self):
''',
)

replace_once(
    "tests/test_auth.py",
    '''    def test_sessions_store_only_token_hash_and_can_be_revoked(self):
''',
    '''    def test_initial_librarian_creation_is_atomic_and_single_use(self):
        user = self.auth.create_initial_librarian(
            "firstadmin", "first@example.com", "First Admin",
            "a strong initial password", provider="cloudflare",
            subject="cf-subject", require_password=True,
        )
        self.assertTrue(user.is_librarian)
        self.assertEqual(
            self.auth.user_for_identity("cloudflare", "cf-subject").id, user.id
        )
        with self.assertRaisesRegex(AuthenticationError, "already been completed"):
            self.auth.create_initial_librarian(
                "secondadmin", "second@example.com", "Second Admin",
                "another strong password",
            )

    def test_initial_external_identity_failure_does_not_leave_user(self):
        with self.assertRaisesRegex(AuthenticationError, "identity is incomplete"):
            self.auth.create_initial_librarian(
                "firstadmin", "first@example.com", "First Admin", "",
                require_password=False, provider="cloudflare", subject="",
            )
        self.assertEqual(self.auth.user_count(), 0)

    def test_distributed_failures_lock_an_identity_and_old_attempts_are_pruned(self):
        self.auth.create_user(
            "ratelimit", "rate@example.com", "Rate Limit",
            "a long rate limit password",
        )
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO login_attempts(identity,ip_address,failures,last_attempt_at)
                   VALUES ('stale','192.0.2.1',1,'2000-01-01 00:00:00')"""
            )
        for index in range(15):
            with self.assertRaises(AuthenticationError):
                self.auth.authenticate_local(
                    "ratelimit", "wrong password", f"198.51.100.{index + 1}"
                )
        from app.auth import LoginLocked
        with self.assertRaises(LoginLocked):
            self.auth.authenticate_local(
                "ratelimit", "a long rate limit password", "203.0.113.1"
            )
        with self.database.connect() as conn:
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM login_attempts WHERE identity='stale'"
            ).fetchone())

    def test_sessions_store_only_token_hash_and_can_be_revoked(self):
''',
)

replace_once(
    "tests/test_maintenance.py",
    '''    def test_non_infomancer_database_is_rejected(self):
''',
    '''    def test_restore_rejects_catalog_paths_outside_trusted_storage(self):
        media = self.base / "media"
        root = media / "Movies"
        title_folder = root / "Example"
        root.mkdir(parents=True)
        with self.database.connect() as connection:
            root_id = connection.execute(
                "INSERT INTO roots(path,kind,label) VALUES (?,'movie','Movies')",
                (str(root),),
            ).lastrowid
            title_id = connection.execute(
                """INSERT INTO titles(root_id,kind,title,folder_path)
                   VALUES (?,'movie','Example',?)""",
                (root_id, str(title_folder)),
            ).lastrowid
            connection.execute(
                """INSERT INTO files(title_id,path,filename,extension,seen_scan)
                   VALUES (?,?,?,?,?)""",
                (title_id, str(title_folder / "movie.mkv"), "movie.mkv", ".mkv", "scan"),
            )
        backup = create_database_backup(self.path)
        connection = sqlite3.connect(backup)
        try:
            connection.execute(
                "UPDATE files SET path='/outside/trusted/storage/movie.mkv'"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(MaintenanceError, "media-file path"):
            install_database_backup(self.path, backup, (media,))

    def test_non_infomancer_database_is_rejected(self):
''',
)

# Full disabled-mode middleware regression without depending on a logged-in
# session. The HttpOnly local CSRF cookie is surfaced to same-origin templates
# but is unavailable to a cross-site form.
replace_once(
    "tests/test_zz_auth_flow.py",
    '''class AuthenticationFlowTests(unittest.TestCase):
    def test_redirect_helper_rejects_external_destination(self):
''',
    '''class AuthenticationFlowTests(unittest.TestCase):
    def test_disabled_mode_keeps_host_and_csrf_boundaries(self):
        original_settings = main.settings
        main.settings = replace(
            main.settings, auth_mode="disabled", public_url="",
            trusted_hosts=(), trust_cloudflare_proxy=False,
        )
        try:
            with TestClient(main.app, follow_redirects=False) as client:
                page = client.get("/")
                self.assertEqual(page.status_code, 200)
                token = client.cookies.get("infomancer_local_csrf")
                self.assertTrue(token)
                rejected = client.post("/account/home-layout", data={})
                self.assertEqual(rejected.status_code, 403)
                accepted = client.post(
                    "/account/home-layout", data={"csrf_token": token}
                )
                self.assertEqual(accepted.status_code, 303)
                bad_host = client.get("/", headers={"host": "attacker.example"})
                self.assertEqual(bad_host.status_code, 400)
        finally:
            main.settings = original_settings

    def test_redirect_helper_rejects_external_destination(self):
''',
)

# ---------------------------------------------------------------------------
# CI dependency audit. The regular matrix still exercises all supported host
# operating systems; dependency auditing only needs one Linux runner.
# ---------------------------------------------------------------------------
write(
    ".github/workflows/tests.yml",
    '''name: Tests

on:
  push:
  pull_request:

permissions:
  contents: read

jobs:
  audit:
    name: Dependency audit
    runs-on: ubuntu-latest
    steps:
      - name: Check out repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.13"
          cache: pip

      - name: Audit Python dependencies
        run: |
          python -m pip install pip-audit==2.10.1
          python -m pip_audit -r requirements.txt

  test:
    name: Python ${{ matrix.python-version }} on ${{ matrix.os }}
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
        python-version: ["3.13"]
    steps:
      - name: Check out repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip

      - name: Install dependencies
        run: python -m pip install -r requirements.txt

      - name: Run tests
        run: python -m unittest discover -s tests -v

      - name: Compile application
        run: python -m compileall -q app
''',
)

print("Security hardening batch 2 applied successfully.")

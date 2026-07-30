from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from jwt import PyJWKClient

from .config import Settings
from .db import Database


SESSION_COOKIE = "infomancer_session"
PREAUTH_COOKIE = "infomancer_preauth"
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,49}$")
PROFILE_ICONS = {"initials", "film", "television", "star", "library"}
ROLES = {"member", "librarian"}

password_hasher = PasswordHasher(memory_cost=19_456, time_cost=2, parallelism=1)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_email(value: str) -> str:
    return value.strip().casefold()


def safe_next(value: str, fallback: str = "/") -> str:
    candidate = value.strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
        return fallback
    return candidate


def secure_cookie_for(request, settings: Settings) -> bool:
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


def profile_symbol(user) -> str:
    icon = user.profile_icon if hasattr(user, "profile_icon") else user["profile_icon"]
    if icon == "film":
        return "◆"
    if icon == "television":
        return "▣"
    if icon == "star":
        return "★"
    if icon == "library":
        return "▤"
    name = user.display_name if hasattr(user, "display_name") else user["display_name"]
    return (name.strip()[:1] or "?").upper()


@dataclass(frozen=True)
class AuthUser:
    id: int
    username: str
    email: str
    display_name: str
    profile_icon: str
    role: str
    active: bool
    force_password_change: bool
    last_login_at: str
    home_layout: str = "modern"
    show_home_hero: bool = True
    high_contrast: bool = False

    @property
    def is_librarian(self) -> bool:
        return self.role == "librarian"

    @property
    def symbol(self) -> str:
        return profile_symbol(self)


@dataclass(frozen=True)
class AuthSession:
    id: int
    user: AuthUser
    csrf_token: str
    created_at: str
    last_seen_at: str
    expires_at: str


class AuthenticationError(ValueError):
    pass


class LoginLocked(AuthenticationError):
    pass


def user_from_row(row: sqlite3.Row) -> AuthUser:
    return AuthUser(
        id=row["id"], username=row["username"], email=row["email"] or "",
        display_name=row["display_name"], profile_icon=row["profile_icon"],
        role=row["role"], active=bool(row["active"]),
        force_password_change=bool(row["force_password_change"]),
        last_login_at=row["last_login_at"] or "",
        home_layout=row["home_layout"] if "home_layout" in row.keys() else "modern",
        show_home_hero=(
            bool(row["show_home_hero"]) if "show_home_hero" in row.keys() else True
        ),
        high_contrast=(
            bool(row["high_contrast"]) if "high_contrast" in row.keys() else False
        ),
    )


class AuthService:
    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.settings = settings
        self._cloudflare_jwks: PyJWKClient | None = None

    def user_count(self) -> int:
        with self.database.connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def librarian_count(self, excluding: int | None = None) -> int:
        sql = "SELECT COUNT(*) FROM users WHERE role='librarian' AND active=1"
        parameters: tuple[Any, ...] = ()
        if excluding is not None:
            sql += " AND id<>?"
            parameters = (excluding,)
        with self.database.connect() as conn:
            return conn.execute(sql, parameters).fetchone()[0]

    def validate_password(self, password: str, label: str = "Password") -> None:
        minimum = self.settings.minimum_password_length
        if len(password) < minimum:
            if self.settings.sandbox:
                raise AuthenticationError(
                    f"{label} cannot be empty in the testing environment."
                )
            raise AuthenticationError(
                f"{label} must contain at least {minimum} characters."
            )
        if len(password) > 256:
            raise AuthenticationError(
                f"{label} is too long. Use no more than 256 characters."
            )

    def validate_account_fields(
        self, username: str, email: str, display_name: str, password: str = "",
        require_password: bool = False,
    ) -> tuple[str, str, str]:
        username = username.strip()
        email = normalize_email(email)
        display_name = display_name.strip()
        if not USERNAME_RE.fullmatch(username):
            raise AuthenticationError(
                "Username must be 3–50 characters and use letters, numbers, dots, dashes, or underscores."
            )
        if email and ("@" not in email or email.startswith("@") or email.endswith("@")):
            raise AuthenticationError("Enter a valid email address or leave it blank.")
        if not display_name or len(display_name) > 100:
            raise AuthenticationError("Display name must be between 1 and 100 characters.")
        if require_password or password:
            self.validate_password(password)
        return username, email, display_name

    def create_user(
        self, username: str, email: str, display_name: str, password: str = "",
        role: str = "member", profile_icon: str = "initials",
        force_password_change: bool = False, require_password: bool = True,
    ) -> AuthUser:
        username, email, display_name = self.validate_account_fields(
            username, email, display_name, password, require_password=require_password
        )
        if role not in ROLES:
            role = "member"
        if profile_icon not in PROFILE_ICONS:
            profile_icon = "initials"
        try:
            with self.database.connect() as conn:
                duplicate = conn.execute(
                    """SELECT username,email FROM users
                       WHERE LOWER(username)=LOWER(?)
                          OR (?<>'' AND LOWER(COALESCE(email,''))=LOWER(?))""",
                    (username, email, email),
                ).fetchone()
                if duplicate:
                    if duplicate["username"].casefold() == username.casefold():
                        raise AuthenticationError(
                            f'The username "{username}" is already in use. Choose a different username.'
                        )
                    raise AuthenticationError(
                        f'The email address "{email}" is already assigned to another account.'
                    )
                user_id = conn.execute(
                    """INSERT INTO users
                       (username,email,display_name,profile_icon,password_hash,role,
                        force_password_change,password_changed_at)
                       VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
                    (
                        username, email or None, display_name, profile_icon,
                        password_hasher.hash(password) if password else None,
                        role, int(force_password_change and bool(password)),
                    ),
                ).lastrowid
                row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        except sqlite3.IntegrityError as exc:
            raise AuthenticationError(
                "That username or email was claimed by another account. Choose a different value and try again."
            ) from exc
        return user_from_row(row)

    def get_user(self, user_id: int) -> AuthUser | None:
        with self.database.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return user_from_row(row) if row else None

    def get_user_by_username(self, username: str) -> AuthUser | None:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE LOWER(username)=LOWER(?)",
                (username.strip(),),
            ).fetchone()
        return user_from_row(row) if row else None

    def list_users(self) -> list[sqlite3.Row]:
        with self.database.connect() as conn:
            return conn.execute(
                """SELECT u.*,
                          (SELECT COUNT(*) FROM user_sessions s
                           WHERE s.user_id=u.id AND datetime(s.expires_at)>CURRENT_TIMESTAMP) session_count,
                          GROUP_CONCAT(i.provider, ', ') providers,
                          CASE WHEN u.password_hash IS NOT NULL THEN 1 ELSE 0 END has_password,
                          (SELECT MAX(a.expires_at) FROM account_invitations a
                           WHERE a.user_id=u.id AND a.used_at IS NULL
                             AND a.revoked_at IS NULL
                             AND datetime(a.expires_at)>CURRENT_TIMESTAMP) invitation_expires_at
                   FROM users u LEFT JOIN auth_identities i ON i.user_id=u.id
                   GROUP BY u.id ORDER BY u.role DESC, u.display_name COLLATE NOCASE"""
            ).fetchall()

    def create_invitation(
        self, user_id: int, created_by: int | None, hours: int = 24,
    ) -> tuple[str, str]:
        user = self.get_user(user_id)
        if not user:
            raise AuthenticationError(
                "This account no longer exists, so a setup link cannot be created."
            )
        if not user.active:
            raise AuthenticationError(
                f"{user.display_name}'s account is disabled. Enable it before creating a setup link."
            )
        raw_token = secrets.token_urlsafe(48)
        expires = iso_timestamp(utcnow() + timedelta(hours=max(1, min(hours, 168))))
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE account_invitations SET revoked_at=CURRENT_TIMESTAMP
                   WHERE user_id=? AND used_at IS NULL AND revoked_at IS NULL""",
                (user_id,),
            )
            conn.execute(
                """INSERT INTO account_invitations
                   (user_id,token_hash,created_by,expires_at) VALUES (?,?,?,?)""",
                (user_id, token_hash(raw_token), created_by, expires),
            )
        return raw_token, expires

    def invitation_for_token(self, raw_token: str) -> sqlite3.Row:
        if not raw_token:
            raise AuthenticationError(
                "This setup link is incomplete. Ask a Librarian to create a new link."
            )
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT a.*,u.username,u.email,u.display_name,u.profile_icon,
                          u.role,u.active,u.password_hash
                   FROM account_invitations a JOIN users u ON u.id=a.user_id
                   WHERE a.token_hash=?""",
                (token_hash(raw_token),),
            ).fetchone()
        if not row:
            raise AuthenticationError(
                "InfoMancer does not recognize this setup link. Ask a Librarian to create a new one."
            )
        if not row["active"]:
            raise AuthenticationError(
                "This account is disabled. Ask a Librarian to enable it before continuing."
            )
        if row["revoked_at"]:
            raise AuthenticationError(
                "This setup link was cancelled. Ask a Librarian to create a new one."
            )
        if row["used_at"]:
            raise AuthenticationError(
                "This setup link has already been used. Sign in with the password that was created."
            )
        try:
            expires = datetime.fromisoformat(row["expires_at"])
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise AuthenticationError(
                "This setup link has an invalid expiration time. Ask a Librarian to replace it."
            ) from exc
        if expires <= utcnow():
            raise AuthenticationError(
                "This setup link has expired. Ask a Librarian to create a fresh link."
            )
        return row

    def accept_invitation(self, raw_token: str, password: str) -> AuthUser:
        self.validate_password(password)
        invitation = self.invitation_for_token(raw_token)
        with self.database.connect() as conn:
            updated = conn.execute(
                """UPDATE account_invitations SET used_at=CURRENT_TIMESTAMP
                   WHERE id=? AND used_at IS NULL AND revoked_at IS NULL
                     AND datetime(expires_at)>CURRENT_TIMESTAMP""",
                (invitation["id"],),
            )
            if updated.rowcount != 1:
                raise AuthenticationError(
                    "This setup link changed while it was being used. Ask a Librarian for a new link."
                )
            conn.execute(
                """UPDATE users SET password_hash=?,force_password_change=0,
                   password_changed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (password_hasher.hash(password), invitation["user_id"]),
            )
            conn.execute(
                """UPDATE account_invitations SET revoked_at=CURRENT_TIMESTAMP
                   WHERE user_id=? AND id<>? AND used_at IS NULL AND revoked_at IS NULL""",
                (invitation["user_id"], invitation["id"]),
            )
            conn.execute(
                "DELETE FROM user_sessions WHERE user_id=?", (invitation["user_id"],)
            )
        return self.get_user(invitation["user_id"])

    def revoke_invitations(self, user_id: int) -> int:
        with self.database.connect() as conn:
            result = conn.execute(
                """UPDATE account_invitations SET revoked_at=CURRENT_TIMESTAMP
                   WHERE user_id=? AND used_at IS NULL AND revoked_at IS NULL""",
                (user_id,),
            )
        return result.rowcount

    def authenticate_local(self, identity: str, password: str, ip_address: str) -> AuthUser:
        identity = identity.strip().casefold()
        if not identity or not password:
            raise AuthenticationError("Incorrect username, email, or password.")
        with self.database.connect() as conn:
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
                raise AuthenticationError("Incorrect username, email, or password.")
            conn.execute(
                "DELETE FROM login_attempts WHERE identity=? AND ip_address=?",
                (identity, ip_address),
            )
            conn.execute(
                "UPDATE users SET last_login_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],)
            )
            refreshed = conn.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
        return user_from_row(refreshed)

    def create_session(self, user: AuthUser, request) -> tuple[str, AuthSession]:
        raw_token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        expires = utcnow() + timedelta(days=self.settings.session_days)
        with self.database.connect() as conn:
            conn.execute("DELETE FROM user_sessions WHERE datetime(expires_at)<=CURRENT_TIMESTAMP")
            session_id = conn.execute(
                """INSERT INTO user_sessions
                   (user_id,token_hash,csrf_token,expires_at,user_agent,ip_address)
                   VALUES (?,?,?,?,?,?)""",
                (
                    user.id, token_hash(raw_token), csrf_token, iso_timestamp(expires),
                    request.headers.get("user-agent", "")[:500], request_ip(request),
                ),
            ).lastrowid
            row = conn.execute(
                "SELECT * FROM user_sessions WHERE id=?", (session_id,)
            ).fetchone()
        return raw_token, AuthSession(
            id=row["id"], user=user, csrf_token=row["csrf_token"],
            created_at=row["created_at"], last_seen_at=row["last_seen_at"],
            expires_at=row["expires_at"],
        )

    def session_from_token(self, raw_token: str) -> AuthSession | None:
        if not raw_token:
            return None
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT s.*,u.username,u.email,u.display_name,u.profile_icon,u.role,
                          u.active,u.force_password_change,u.last_login_at,
                          u.home_layout,u.show_home_hero,u.high_contrast
                   FROM user_sessions s JOIN users u ON u.id=s.user_id
                   WHERE s.token_hash=? AND datetime(s.expires_at)>CURRENT_TIMESTAMP AND u.active=1""",
                (token_hash(raw_token),),
            ).fetchone()
            if not row:
                return None
            try:
                last_seen = datetime.fromisoformat(row["last_seen_at"])
                if last_seen.tzinfo is None:
                    last_seen = last_seen.replace(tzinfo=timezone.utc)
            except ValueError:
                last_seen = utcnow() - timedelta(minutes=10)
            if utcnow() - last_seen > timedelta(minutes=5):
                conn.execute(
                    "UPDATE user_sessions SET last_seen_at=CURRENT_TIMESTAMP WHERE id=?",
                    (row["id"],),
                )
        user = AuthUser(
            id=row["user_id"], username=row["username"], email=row["email"] or "",
            display_name=row["display_name"], profile_icon=row["profile_icon"],
            role=row["role"], active=bool(row["active"]),
            force_password_change=bool(row["force_password_change"]),
            last_login_at=row["last_login_at"] or "",
            home_layout=row["home_layout"] or "modern",
            show_home_hero=bool(row["show_home_hero"]),
            high_contrast=bool(row["high_contrast"]),
        )
        return AuthSession(
            id=row["id"], user=user, csrf_token=row["csrf_token"],
            created_at=row["created_at"], last_seen_at=row["last_seen_at"],
            expires_at=row["expires_at"],
        )

    def list_sessions(self, user_id: int) -> list[sqlite3.Row]:
        with self.database.connect() as conn:
            return conn.execute(
                """SELECT * FROM user_sessions WHERE user_id=?
                   AND datetime(expires_at)>CURRENT_TIMESTAMP ORDER BY last_seen_at DESC""",
                (user_id,),
            ).fetchall()

    def revoke_session(self, session_id: int, user_id: int | None = None) -> None:
        with self.database.connect() as conn:
            if user_id is None:
                conn.execute("DELETE FROM user_sessions WHERE id=?", (session_id,))
            else:
                conn.execute(
                    "DELETE FROM user_sessions WHERE id=? AND user_id=?",
                    (session_id, user_id),
                )

    def revoke_user_sessions(self, user_id: int, except_session: int | None = None) -> None:
        with self.database.connect() as conn:
            if except_session is None:
                conn.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
            else:
                conn.execute(
                    "DELETE FROM user_sessions WHERE user_id=? AND id<>?",
                    (user_id, except_session),
                )

    def update_profile(
        self, user_id: int, display_name: str, email: str, profile_icon: str,
        show_home_hero: bool | None = None, high_contrast: bool | None = None,
    ) -> AuthUser:
        current = self.get_user(user_id)
        if not current:
            raise AuthenticationError("Account not found.")
        _, email, display_name = self.validate_account_fields(
            current.username, email, display_name
        )
        if profile_icon not in PROFILE_ICONS:
            profile_icon = "initials"
        try:
            with self.database.connect() as conn:
                if show_home_hero is None and high_contrast is None:
                    conn.execute(
                        """UPDATE users SET display_name=?,email=?,profile_icon=?,
                           updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                        (display_name, email or None, profile_icon, user_id),
                    )
                else:
                    current_hero = (
                        bool(current.show_home_hero)
                        if show_home_hero is None else bool(show_home_hero)
                    )
                    current_contrast = (
                        bool(current.high_contrast)
                        if high_contrast is None else bool(high_contrast)
                    )
                    conn.execute(
                        """UPDATE users SET display_name=?,email=?,profile_icon=?,
                           show_home_hero=?,high_contrast=?,
                           updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                        (
                            display_name, email or None, profile_icon,
                            int(current_hero), int(current_contrast), user_id,
                        ),
                    )
        except sqlite3.IntegrityError as exc:
            raise AuthenticationError("That email is already used by another account.") from exc
        return self.get_user(user_id)

    def toggle_home_layout(self, user_id: int) -> AuthUser:
        current = self.get_user(user_id)
        if not current:
            raise AuthenticationError("Account not found.")
        layout = "classic" if current.home_layout == "modern" else "modern"
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE users SET home_layout=?,updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (layout, user_id),
            )
        return self.get_user(user_id)

    def change_password(self, user_id: int, current_password: str, new_password: str) -> None:
        self.validate_password(new_password, "New password")
        with self.database.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise AuthenticationError("Account not found.")
            if row["password_hash"]:
                try:
                    if not password_hasher.verify(row["password_hash"], current_password):
                        raise AuthenticationError("Current password is incorrect.")
                except (VerifyMismatchError, VerificationError, InvalidHashError) as exc:
                    raise AuthenticationError("Current password is incorrect.") from exc
            conn.execute(
                """UPDATE users SET password_hash=?,force_password_change=0,
                   password_changed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (password_hasher.hash(new_password), user_id),
            )

    def update_user_admin(
        self, user_id: int, display_name: str, email: str, role: str,
        active: bool, acting_user_id: int,
    ) -> AuthUser:
        current = self.get_user(user_id)
        if not current:
            raise AuthenticationError("Account not found.")
        _, email, display_name = self.validate_account_fields(
            current.username, email, display_name
        )
        if role not in ROLES:
            role = "member"
        removing_final_librarian = (
            current.role == "librarian" and current.active
            and (role != "librarian" or not active)
            and self.librarian_count(excluding=user_id) == 0
        )
        if removing_final_librarian:
            raise AuthenticationError("InfoMancer must retain at least one active Librarian.")
        if user_id == acting_user_id and not active:
            raise AuthenticationError("You cannot disable your current account.")
        try:
            with self.database.connect() as conn:
                conn.execute(
                    """UPDATE users SET display_name=?,email=?,role=?,active=?,
                       updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (display_name, email or None, role, int(active), user_id),
                )
                if not active:
                    conn.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
        except sqlite3.IntegrityError as exc:
            raise AuthenticationError("That email is already used by another account.") from exc
        return self.get_user(user_id)

    def set_temporary_password(self, user_id: int, password: str) -> None:
        self.validate_password(password, "Temporary password")
        with self.database.connect() as conn:
            if not conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone():
                raise AuthenticationError("Account not found.")
            conn.execute(
                """UPDATE users SET password_hash=?,force_password_change=1,
                   password_changed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (password_hasher.hash(password), user_id),
            )
            conn.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))

    def recover_librarian(self, username: str, password: str) -> AuthUser:
        user = self.get_user_by_username(username)
        if not user:
            raise AuthenticationError(
                f'No InfoMancer account uses the username "{username.strip()}". Check the spelling and try again.'
            )
        if not user.is_librarian:
            raise AuthenticationError(
                f'"{user.username}" is a Member account. A signed-in Librarian can reset that account from the Users page.'
            )
        self.validate_password(password, "Recovery password")
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE users SET password_hash=?,active=1,force_password_change=1,
                   password_changed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (password_hasher.hash(password), user.id),
            )
            conn.execute("DELETE FROM user_sessions WHERE user_id=?", (user.id,))
            conn.execute(
                """UPDATE account_invitations SET revoked_at=CURRENT_TIMESTAMP
                   WHERE user_id=? AND used_at IS NULL AND revoked_at IS NULL""",
                (user.id,),
            )
        return self.get_user(user.id)

    def cloudflare_claims(self, assertion: str) -> dict[str, Any]:
        domain = self.settings.cloudflare_team_domain
        audience = self.settings.cloudflare_audience
        if not domain or not audience:
            raise AuthenticationError("Cloudflare Access authentication is not configured.")
        issuer = domain if domain.startswith("https://") else f"https://{domain}"
        if self._cloudflare_jwks is None:
            self._cloudflare_jwks = PyJWKClient(f"{issuer}/cdn-cgi/access/certs", cache_keys=True)
        try:
            key = self._cloudflare_jwks.get_signing_key_from_jwt(assertion)
            return jwt.decode(
                assertion, key.key, algorithms=["RS256"], audience=audience, issuer=issuer
            )
        except Exception as exc:
            raise AuthenticationError("Cloudflare Access could not verify this request.") from exc

    def user_for_identity(self, provider: str, subject: str) -> AuthUser | None:
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT u.* FROM auth_identities i JOIN users u ON u.id=i.user_id
                   WHERE i.provider=? AND i.subject=? AND u.active=1""",
                (provider, subject),
            ).fetchone()
        return user_from_row(row) if row else None

    def claim_preassigned_identity(
        self, provider: str, subject: str, email: str,
    ) -> AuthUser | None:
        """Link an external identity only to an account explicitly pre-created by email."""
        normalized = normalize_email(email)
        if not normalized:
            return None
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT u.* FROM users u
                   WHERE u.active=1 AND LOWER(COALESCE(u.email,''))=?
                     AND NOT EXISTS (
                       SELECT 1 FROM auth_identities i WHERE i.user_id=u.id
                     )""",
                (normalized,),
            ).fetchone()
        if not row:
            return None
        self.link_identity(row["id"], provider, subject, normalized)
        return self.get_user(row["id"])

    def record_identity_login(self, provider: str, subject: str, user_id: int) -> None:
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE auth_identities SET last_login_at=CURRENT_TIMESTAMP
                   WHERE provider=? AND subject=? AND user_id=?""",
                (provider, subject, user_id),
            )
            conn.execute(
                "UPDATE users SET last_login_at=CURRENT_TIMESTAMP WHERE id=?", (user_id,)
            )

    def link_identity(self, user_id: int, provider: str, subject: str, email: str = "") -> None:
        try:
            with self.database.connect() as conn:
                conn.execute(
                    """INSERT INTO auth_identities(user_id,provider,subject,email)
                       VALUES (?,?,?,?)""",
                    (user_id, provider, subject, normalize_email(email) or None),
                )
        except sqlite3.IntegrityError as exc:
            raise AuthenticationError("That sign-in identity is already linked.") from exc

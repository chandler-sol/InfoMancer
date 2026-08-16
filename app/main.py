from __future__ import annotations

import os
import hmac
import csv
import base64
import io
import json
import re
import secrets
import sqlite3
import tempfile
import threading
import time
import zipfile
import urllib.error
import urllib.request
from xml.etree import ElementTree
from difflib import SequenceMatcher
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlencode, urlparse
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import BASE_DIR, get_settings
from .access import LibrarianAccessRequired, require_librarian
from .app_settings import AppSettingError, AppSettings
from .bootstrap import BootstrapTokenManager
from .auth import (
    AuthService, AuthSession, AuthUser, AuthenticationError, LoginLocked,
    PREAUTH_COOKIE, PROFILE_ICONS, SESSION_COOKIE, request_ip, safe_next,
    secure_cookie_for,
)
from .db import Database
from .duplicates import DuplicateService
from .editions import EditionVersionService, clean_label, identity
from .duplicate_trash import DuplicateTrashError, DuplicateTrashService
from .engagement import EngagementError, EngagementService, utc_from_local
from .event_log import EventLog
from .file_hashes import MediaHashService
from .imdb import sync_genres
from .media_info import MediaInspectionError, inspect_media
from .mie import CATEGORIES as MIE_CATEGORIES
from .mie import SEVERITIES as MIE_SEVERITIES
from .mie import MediaIntelligenceEngine
from .maintenance import (
    MaintenanceError, create_database_backup, install_database_backup,
    list_database_backups, read_update_status, resolve_backup,
    validate_database_backup,
    write_update_request, write_update_status,
)
from .naming import (
    contained_destination, plex_episode_filename, plex_movie_filename, plex_show_folder,
)
from .scanner import SourceUnavailableError, scan_root, scan_title
from .source_browser import SourceBrowserError, list_folders, preview_folder
from .smart_collections import decode_filters, encode_filters, matching_titles, normalize_filters
from .tvdb import TVDBClient, TVDBError
from .provider_secrets import ProviderSecretError, ProviderSecretStore
from .background import BackgroundCoordinator
from .title_metadata import TitleMetadataService
from .request_security import (
    LOCAL_CSRF_COOKIE, RequestBodyTooLarge, browser_request_is_same_origin,
    constant_time_equal, csrf_submission, host_is_allowed, replay_body,
    should_issue_session_cookie,
)
from .timezones import timezone_groups
from .routes import ROUTER_BUILDERS
from .routes.context import RouteContext


settings = get_settings()
db = Database(settings.database)
db.initialize()
auth_service = AuthService(db, settings)
bootstrap_tokens = BootstrapTokenManager(
    settings.database.parent / "bootstrap-token", settings.bootstrap_token
)
app_settings = AppSettings(db, settings.search_url_template)
engagement = EngagementService(db)
event_log = EventLog(db)
mie = MediaIntelligenceEngine(db)
media_hashes = MediaHashService(db)
duplicates = DuplicateService(db, media_hashes)
edition_versions = EditionVersionService(db)
duplicate_trash = DuplicateTrashService(db)
engagement.seed_official()
provider_secrets = ProviderSecretStore(
    settings.database.parent / "provider-secrets.enc", settings.application_secret
)
provider_secret_error = ""
try:
    stored_provider_secrets = provider_secrets.load()
except ProviderSecretError as exc:
    stored_provider_secrets = {}
    provider_secret_error = str(exc)
APP_VERSION = "0.8.0-alpha.1"
app = FastAPI(
    title="InfoMancer", version=APP_VERSION,
    docs_url=None, redoc_url=None, openapi_url=None,
)


def _librarian_route(method: str, path: str, **kwargs):
    dependencies = list(kwargs.pop("dependencies", ()))
    dependencies.append(Depends(require_librarian))
    return getattr(app, method)(path, dependencies=dependencies, **kwargs)


def librarian_get(path: str, **kwargs):
    return _librarian_route("get", path, **kwargs)


def librarian_post(path: str, **kwargs):
    return _librarian_route("post", path, **kwargs)

app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
COLLECTION_ART_DIR = settings.database.parent / "collection-art"
COLLECTION_ART_DIR.mkdir(parents=True, exist_ok=True)


def shared_template_context(request: Request) -> dict:
    preferences = app_settings.values()
    current_user = getattr(request.state, "user", None)
    show_tour = False
    next_announcement = None
    announcement_due_count = 0
    setup_choice_pending = False
    show_setup_choice = False
    if current_user and current_user.id > 0:
        show_tour = (
            request.query_params.get("tour") == "1"
            or engagement.tour_pending(current_user.id)
        )
        announcement_due_count = engagement.due_count(
            current_user.id, current_user.role
        )
        setup_choice_pending = engagement.setup_choice_pending(
            current_user.id, current_user.role
        )
        show_setup_choice = (
            setup_choice_pending and not show_tour and request.url.path == "/"
        )
        if not show_tour and not show_setup_choice and request.url.path != "/announcements":
            next_announcement = engagement.due(current_user.id, current_user.role)
    return {
        "current_user": current_user,
        "current_session": getattr(request.state, "auth_session", None),
        "csrf_token": (
            getattr(getattr(request.state, "auth_session", None), "csrf_token", "")
            or getattr(request.state, "local_csrf_token", "")
        ),
        "auth_mode": settings.auth_mode,
        "sandbox_mode": settings.sandbox,
        "minimum_password_length": settings.minimum_password_length,
        "app_version": APP_VERSION,
        # Retain installation_name in portable settings for compatibility while
        # presenting one consistent product identity everywhere.
        "app_name": "InfoMancer",
        "default_library_view": preferences["default_library_view"],
        "default_cover_size": int(preferences["default_cover_size"]),
        "file_protection_mode": app_settings.file_protection_mode(),
        "search_provider_name": preferences["search_provider_name"],
        "show_onboarding_tour": show_tour,
        "setup_choice_pending": setup_choice_pending,
        "show_setup_choice": show_setup_choice,
        "next_announcement": next_announcement,
        "announcement_due_count": announcement_due_count,
        "activity_unread_count": event_log.unread_count(current_user.id) if current_user else 0,
    }


templates = Jinja2Templates(
    directory=BASE_DIR / "app" / "templates",
    context_processors=[shared_template_context],
)
tvdb = TVDBClient(
    stored_provider_secrets.get("tvdb_api_key", settings.tvdb_api_key),
    stored_provider_secrets.get("tvdb_pin", settings.tvdb_pin),
)
def _file_signatures(*, root_id: int | None = None, title_id: int | None = None) -> dict[int, tuple[int, float]]:
    where, value = ("t.root_id", root_id) if root_id is not None else ("f.title_id", title_id)
    with db.connect() as conn:
        return {
            int(row["id"]): (int(row["size_bytes"] or 0), float(row["modified_at"] or 0))
            for row in conn.execute(
                f"""SELECT f.id,f.size_bytes,f.modified_at FROM files f
                    JOIN titles t ON t.id=f.title_id WHERE {where}=?""", (value,)
            )
        }


def _changed_file_ids(before: dict[int, tuple[int, float]], after: dict[int, tuple[int, float]]) -> list[int]:
    return [file_id for file_id, signature in after.items() if before.get(file_id) != signature]

def record_event(
    category: str, message: str, *, level: str = "info", detail: str = "",
    context: dict | None = None, user_id: int | None = None,
) -> None:
    configured = app_settings.get("log_level")
    if level == "debug" and configured != "debug":
        return
    if level == "verbose" and configured not in {"verbose", "debug"}:
        return
    stored_level = "debug" if level in {"debug", "verbose"} else level
    event_log.write(
        category, message, level=stored_level, detail=detail,
        context=context, user_id=user_id,
    )


def _primary_librarian_id() -> int | None:
    """Return the first active Librarian for targeted security notifications."""
    try:
        with db.connect() as conn:
            row = conn.execute(
                """SELECT id FROM users
                   WHERE role='librarian' AND active=1
                   ORDER BY id LIMIT 1"""
            ).fetchone()
        return int(row["id"]) if row else None
    except sqlite3.Error:
        return None


def record_security_event(
    message: str, *, level: str = "info", detail: str = "",
    context: dict | None = None, user_id: int | None = None,
    notify_librarian: bool = False,
) -> None:
    """Audit a security event and optionally surface it to the primary Librarian."""
    security_context = dict(context or {})
    security_context.setdefault("category", "authentication")
    record_event(
        "authentication", message, level=level, detail=detail,
        context=security_context, user_id=user_id,
    )
    if not notify_librarian:
        return
    librarian_id = _primary_librarian_id()
    if librarian_id is None:
        return
    record_event(
        "library", message, level=level, detail=detail,
        context=security_context, user_id=librarian_id,
    )


background = BackgroundCoordinator(
    db, app_settings, media_hashes, duplicate_trash, record_event,
)
job_registry = background.registry
runtime_lease = background.runtime_lease
scan_jobs = background.scan_jobs
scan_lock = background.scan_lock
scan_all_job = background.scan_all_job
scan_all_lock = background.scan_all_lock
title_scan_jobs = background.title_scan_jobs
title_scan_lock = background.title_scan_lock
imdb_genre_job = background.imdb_genre_job
imdb_genre_lock = background.imdb_genre_lock
movie_match_job = background.movie_match_job
movie_match_lock = background.movie_match_lock
tv_match_job = background.tv_match_job
tv_match_lock = background.tv_match_lock
media_info_job = background.media_info_job
media_info_lock = background.media_info_lock
duplicate_verify_job = background.duplicate_verify_job
duplicate_verify_lock = background.duplicate_verify_lock
media_hash_job = background.media_hash_job
media_hash_lock = background.media_hash_lock
media_hash_pause = background.media_hash_pause
media_hash_cancel = background.media_hash_cancel
background_scheduler_stop = background.scheduler_stop
trash_cleanup_job = background.trash_cleanup_job
trash_cleanup_lock = background.trash_cleanup_lock
run_media_hashing = background.run_media_hashing
start_media_hashing = background.start_media_hashing
handle_import_hashing = background.handle_import_hashing
_other_background_work_running = background.other_background_work_running
maybe_start_scheduled_hashing = background.maybe_start_scheduled_hashing
run_background_scheduler = background.run_scheduler
trash_retention_days = background.trash_retention_days
maybe_start_trash_cleanup = background.maybe_start_trash_cleanup


@app.on_event("startup")
def start_background_scheduler() -> None:
    background.start()


@app.on_event("shutdown")
def stop_background_scheduler() -> None:
    background.stop()


PUBLIC_PATHS = {"/health", "/login", "/setup", "/forgot-password"}

def public_path(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith("/activate/")


def auth_error_response(request: Request, status: int, title: str, detail: str):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": detail}, status_code=status)
    return templates.TemplateResponse(
        request, "auth_error.html",
        {"status": status, "heading": title, "detail": detail, "message": ""},
        status_code=status,
    )


@app.exception_handler(LibrarianAccessRequired)
async def librarian_access_required(request: Request, _exc: LibrarianAccessRequired):
    return auth_error_response(
        request, 403, "Librarian access required",
        "Your Member account can browse the library, but this operation requires a Librarian.",
    )


def set_session_cookie(response, request: Request, raw_token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, raw_token, httponly=True,
        secure=secure_cookie_for(request, settings), samesite="lax",
        max_age=settings.session_days * 86_400, path="/",
    )


@app.middleware("http")
async def authentication_middleware(request: Request, call_next):
    path = request.url.path
    request.state.user = None
    request.state.auth_session = None
    new_session_token = ""
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
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'",
        )
        return response

    if not host_is_allowed(request, settings):
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

    if settings.auth_mode != "disabled":
        users_exist = auth_service.user_count() > 0

        if settings.auth_mode == "cloudflare":
            assertion = request.headers.get("cf-access-jwt-assertion", "")
            try:
                claims = auth_service.cloudflare_claims(assertion)
            except AuthenticationError as exc:
                record_event(
                    "authentication",
                    "Cloudflare Access authentication was rejected.",
                    level="warning", detail=str(exc),
                    context={"operation": "cloudflare-claims"},
                )
                return await finish(auth_error_response(
                    request, 401, "Authentication required",
                    "Cloudflare Access could not verify this request. Sign in through Access again, then retry.",
                ))
            request.state.external_claims = claims
            if not users_exist:
                if path != "/setup":
                    if path.startswith("/api/"):
                        return await finish(JSONResponse(
                            {"detail": "Complete first-run setup"}, status_code=401
                        ))
                    return await finish(RedirectResponse("/setup", status_code=303))
            else:
                subject = str(claims.get("sub") or "")
                email = str(claims.get("email") or "")
                user = auth_service.user_for_identity("cloudflare", subject)
                if not user:
                    user = auth_service.claim_preassigned_identity(
                        "cloudflare", subject, email
                    )
                if not user:
                    return await finish(auth_error_response(
                        request, 403, "Account not assigned",
                        "Cloudflare verified your identity, but a Librarian has not assigned it an InfoMancer account.",
                    ))
                auth_service.record_identity_login("cloudflare", subject, user.id)
                existing = auth_service.session_from_token(
                    request.cookies.get(SESSION_COOKIE, "")
                )
                if (
                    (not existing or existing.user.id != user.id)
                    and should_issue_session_cookie(path)
                ):
                    new_session_token, existing = auth_service.create_session(user, request)
                request.state.user = user
                request.state.auth_session = existing
        else:
            if not users_exist:
                if path != "/setup":
                    if path.startswith("/api/"):
                        return await finish(JSONResponse(
                            {"detail": "Complete first-run setup"}, status_code=401
                        ))
                    return await finish(RedirectResponse("/setup", status_code=303))
            elif not public_path(path):
                session = auth_service.session_from_token(
                    request.cookies.get(SESSION_COOKIE, "")
                )
                if not session:
                    if path.startswith("/api/"):
                        return await finish(JSONResponse(
                            {"detail": "Authentication required"}, status_code=401
                        ))
                    destination = safe_next(path + (f"?{request.url.query}" if request.url.query else ""))
                    return await finish(RedirectResponse(
                        f"/login?{urlencode({'next': destination})}", status_code=303
                    ))
                request.state.user = session.user
                request.state.auth_session = session

    user = getattr(request.state, "user", None)
    session = getattr(request.state, "auth_session", None)
    if user and user.force_password_change and path not in {
        "/account/security", "/logout"
    }:
        return await finish(RedirectResponse(
            "/account/security?message=Choose+a+new+password+to+continue", status_code=303
        ))
    if user and request.method not in {"GET", "HEAD", "OPTIONS"}:
        if path not in {"/login", "/setup"}:
            if settings.auth_mode == "disabled":
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
                    or not constant_time_equal(submitted, local_csrf)
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
                if not submitted or not constant_time_equal(
                    submitted, session.csrf_token
                ):
                    return await finish(auth_error_response(
                        request, 403, "Request verification failed",
                        "Refresh the page and try the operation again.",
                    ))
                if buffered_body is not None:
                    replay_body(request, buffered_body)

    return await finish(await call_next(request))


def lifespan(row) -> str:
    start = row["metadata_year"] or row["year"]
    if not start:
        return ""
    continuing = (
        row["metadata_continuing"]
        if row["metadata_continuing"] is not None
        else row["continuing"]
    )
    end = row["metadata_end_year"] or row["end_year"]
    if continuing == 1:
        return f"{start} - Present"
    if end:
        return f"{start} - {end}"
    return str(start)


def title_initial(row) -> str:
    try:
        sort_title = str(row["sort_title"] or "").strip()
    except (KeyError, IndexError, TypeError):
        sort_title = ""
    name = sort_title or (row["metadata_title"] or row["title"] or "").strip()
    lowered = name.casefold()
    for article in ("the ", "an ", "a "):
        if lowered.startswith(article):
            name = name[len(article):].lstrip()
            break
    return name[0].upper() if name and name[0].isalpha() else "#"


def display_title_type(value: str) -> str:
    labels = {
        "movie": "Movie", "short": "Short", "tvEpisode": "TV Episode",
        "tvMiniSeries": "TV Miniseries", "tvMovie": "TV Movie",
        "tvSeries": "TV Series", "tvShort": "TV Short",
        "tvSpecial": "TV Special", "video": "Video",
        "videoGame": "Video Game",
    }
    return labels.get(value, value)


def format_bytes(value: int | None) -> str:
    size = float(value or 0)
    units = ("bytes", "KB", "MB", "GB", "TB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:,.0f} {unit}" if unit == "bytes" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size:,.1f} TB"


def local_time(value: str | None) -> str:
    if not value:
        return "Never"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        configured_zone = ZoneInfo(app_settings.get("timezone"))
        return parsed.astimezone(configured_zone).strftime("%Y-%m-%d %H:%M %Z")
    except (ValueError, KeyError):
        return str(value)


def scan_is_stale(value: str | None, hours: int = 24) -> bool:
    if not value:
        return False
    try:
        scanned = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if scanned.tzinfo is None:
            scanned = scanned.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - scanned).total_seconds() > hours * 3600
    except ValueError:
        return True


def provider_search_url(query: str) -> str:
    """Build a search URL without coupling callers to a provider's URL format."""
    return app_settings.get("search_url_template").replace("{query}", quote_plus(query))


def series_provider_search_url(title) -> str:
    """Prefer EXT's IMDb filter for a series, with keyword search as a fallback."""
    imdb_id = (title["imdb_id"] or "").strip()
    parsed = urlparse(app_settings.get("search_url_template"))
    if imdb_id and (parsed.hostname or "").casefold() in {"ext.to", "www.ext.to"}:
        base = f"{parsed.scheme or 'https'}://{parsed.netloc or 'ext.to'}/browse/"
        parameters = urlencode({"imdb_id": imdb_id, "order": "desc", "sort": "age"})
        return f"{base}?{parameters}"
    return provider_search_url(title["metadata_title"] or title["title"])


def _alias_names(record: dict) -> list[str]:
    names: list[str] = []
    for alias in record.get("aliases") or []:
        if isinstance(alias, dict):
            value = alias.get("name") or alias.get("title")
        else:
            value = alias
        value = str(value or "").strip()
        if value and value not in names:
            names.append(value)
    return names


def localized_tvdb_title(
    record: dict, existing_title: str = "",
) -> tuple[str, str]:
    """Resolve TVDB names without replacing cached English with a default name."""
    translation = record.get("_english_translation") or {}
    english_name = str(translation.get("name") or "").strip()
    if english_name:
        return english_name, "eng"

    # If an earlier fetch already supplied a usable title, retain it when TVDB
    # has no English translation. The default record name may be in the
    # original language and must not overwrite an existing English value.
    existing = str(existing_title or "").strip()
    if existing:
        return existing, ""

    default_name = str(record.get("_default_name") or record.get("name") or "").strip()
    if default_name:
        return default_name, "default"

    aliases = _alias_names(record)
    if aliases:
        return aliases[0], "alias"
    return "", ""


def refresh_cached_tvdb_title_localizations() -> dict[str, int]:
    """Refresh matched TV titles while preserving cached names without English data."""
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT id, tvdb_id, metadata_title
               FROM titles
               WHERE kind='tv' AND tvdb_id IS NOT NULL
                 AND COALESCE(metadata_title_language, '')!='eng'
               ORDER BY id"""
        ).fetchall()
    updated = preserved = errors = 0
    for row in rows:
        try:
            translation = tvdb.translation("series", row["tvdb_id"], "eng")
            english_name = str(translation.get("name") or "").strip()
            if english_name:
                with db.connect() as conn:
                    conn.execute(
                        """UPDATE titles SET metadata_title=?,
                           metadata_title_language='eng',
                           updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                        (english_name, row["id"]),
                    )
                updated += 1
            elif row["metadata_title"]:
                # An absent English translation must never replace the cached
                # title with the original-language name from the base record.
                with db.connect() as conn:
                    conn.execute(
                        """UPDATE titles SET metadata_title_language='preserved',
                           updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                        (row["id"],),
                    )
                preserved += 1
            else:
                series = tvdb.series(row["tvdb_id"])
                title, language = localized_tvdb_title(series)
                if title:
                    with db.connect() as conn:
                        conn.execute(
                            """UPDATE titles SET metadata_title=?,
                               metadata_title_language=?,
                               updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                            (title, language or "default", row["id"]),
                        )
                    updated += 1
        except (TVDBError, OSError, sqlite3.Error):
            errors += 1
        time.sleep(0.08)
    result = {
        "checked": len(rows), "updated": updated,
        "preserved": preserved, "errors": errors,
    }
    record_event(
        "metadata",
        (
            "TVDB English-title refresh finished: "
            f"{updated:,} updated, {preserved:,} preserved, {errors:,} failed."
        ),
        level="warning" if errors else "info",
        context=result,
    )
    return result


def _artwork_language(artwork: dict) -> tuple[str, str]:
    raw = (
        artwork.get("language")
        or artwork.get("languageCode")
        or artwork.get("language_code")
        or ""
    )
    if isinstance(raw, dict):
        code = str(raw.get("code") or raw.get("abbreviation") or raw.get("id") or "")
        label = str(raw.get("name") or code)
    else:
        code = str(raw)
        label = code
    normalized = code.strip().casefold()
    if normalized in {"7", "en", "eng", "english"}:
        return "eng", "English"
    if not normalized:
        return "", "Language not specified"
    return normalized, label.strip() or code


def _is_poster_artwork(artwork: dict) -> bool:
    raw_type = artwork.get("type") or artwork.get("artworkType")
    if isinstance(raw_type, dict):
        type_id = raw_type.get("id")
        type_name = str(raw_type.get("name") or "")
    else:
        type_id = raw_type
        type_name = str(raw_type or "")
    if "poster" in type_name.casefold():
        return True
    try:
        if int(type_id) in {2, 14}:  # TV series poster and movie poster.
            return True
    except (TypeError, ValueError):
        pass
    width, height = artwork.get("width"), artwork.get("height")
    try:
        return int(height) > int(width)
    except (TypeError, ValueError):
        # Some TVDB responses omit dimensions and artwork type details.
        return not raw_type


def poster_candidates(record: dict) -> list[dict]:
    """Return deduplicated portrait artwork with English options first."""
    candidates: list[dict] = []
    seen: set[str] = set()
    for artwork in record.get("artworks") or []:
        image = str(artwork.get("image") or artwork.get("thumbnail") or "").strip()
        if not image or image in seen or not _is_poster_artwork(artwork):
            continue
        seen.add(image)
        language, language_label = _artwork_language(artwork)
        try:
            score = float(artwork.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        candidates.append({
            "url": image,
            "thumbnail": str(artwork.get("thumbnail") or image),
            "language": language,
            "language_label": language_label,
            "score": score,
        })
    fallback = str(record.get("image") or "").strip()
    if fallback and fallback not in seen:
        candidates.append({
            "url": fallback, "thumbnail": fallback, "language": "",
            "language_label": "TVDB default", "score": -1,
        })
    candidates.sort(
        key=lambda item: (
            0 if item["language"] == "eng" else 1 if not item["language"] else 2,
            -item["score"],
        )
    )
    return candidates


def poster_from(record: dict) -> str:
    candidates = poster_candidates(record)
    return candidates[0]["url"] if candidates else ""


def changed_name_parts(old_name: str, new_name: str) -> list[dict]:
    """Split a proposed filename into unchanged and changed display segments."""
    parts = []
    for tag, _old_start, _old_end, new_start, new_end in SequenceMatcher(
        None, old_name, new_name
    ).get_opcodes():
        if new_start != new_end:
            parts.append({
                "text": new_name[new_start:new_end],
                "changed": tag != "equal",
            })
    if old_name != new_name and not any(part["changed"] for part in parts):
        return [{"text": new_name, "changed": True}]
    return parts


def expected_name_map(conn: sqlite3.Connection, title_id: int) -> dict[tuple[int, int], str]:
    rows = conn.execute(
        """SELECT season, episode, name FROM expected_episodes
           WHERE title_id=? ORDER BY season, episode""",
        (title_id,),
    ).fetchall()
    return {
        (row["season"], row["episode"]): row["name"]
        for row in rows if row["name"]
    }


def merged_episode_name(
    names: dict[tuple[int, int], str], season: int | None,
    episode_start: int | None, episode_end: int | None,
) -> str:
    if season is None or episode_start is None:
        return ""
    final_episode = max(episode_start, episode_end or episode_start)
    return " + ".join(
        name for episode in range(episode_start, final_episode + 1)
        if (name := names.get((season, episode)))
    )


def plex_movie_ids(record: dict) -> tuple[str, str]:
    tmdb_id, imdb_id = "", ""
    for remote in record.get("remoteIds") or record.get("remote_ids") or []:
        source = str(remote.get("sourceName") or "").lower()
        remote_id = str(remote.get("id") or "").strip()
        if "movie database" in source or "themoviedb" in source or source == "tmdb":
            tmdb_id = remote_id
        elif "imdb" in source:
            imdb_id = remote_id
    return tmdb_id, imdb_id


def normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def fuzzy_people(query: str, kind: str = "", limit: int = 10) -> list[dict]:
    """Find close local credit names without sending the query to a provider."""
    query = query.strip()
    normalized_query = normalized_name(query)
    if len(normalized_query) < 3:
        return []
    words = [word for word in re.findall(r"[a-z0-9]+", query.lower()) if len(word) >= 2]
    if not words:
        return []
    candidate_conditions = []
    candidate_params: list[str] = []
    for word in words:
        candidate_conditions.extend(["c.person_name LIKE ?", "c.person_name LIKE ?"])
        candidate_params.extend([f"%{word}%", f"{word[:2]}%"])
    kind_condition = " AND t.kind=?" if kind in {"movie", "tv"} else ""
    if kind_condition:
        candidate_params.append(kind)
    with db.connect() as conn:
        rows = conn.execute(
            f"""SELECT c.imdb_person_id, c.person_name,
                       GROUP_CONCAT(DISTINCT c.role) roles,
                       COUNT(DISTINCT c.title_id) title_count
                FROM title_credits c JOIN titles t ON t.id=c.title_id
                WHERE ({' OR '.join(candidate_conditions)}){kind_condition}
                GROUP BY c.imdb_person_id, c.person_name
                LIMIT 300""",
            candidate_params,
        ).fetchall()
    ranked = []
    for row in rows:
        candidate_name = row["person_name"]
        candidate_words = re.findall(r"[a-z0-9]+", candidate_name.lower())
        full_score = SequenceMatcher(
            None, normalized_query, normalized_name(row["person_name"])
        ).ratio()
        word_scores = []
        for word in words:
            word_scores.append(max(
                1.0 if candidate.startswith(word) else SequenceMatcher(
                    None, word, candidate
                ).ratio()
                for candidate in candidate_words
            ))
        word_score = sum(word_scores) / len(word_scores) if word_scores else 0
        score = max(full_score, word_score)
        if score >= 0.82:
            item = dict(row)
            item["similarity"] = score
            ranked.append(item)
    ranked.sort(
        key=lambda item: (
            -item["similarity"], -item["title_count"], item["person_name"].casefold()
        )
    )
    return ranked[:limit]


def match_confidence(title: str, year: int | None, candidate: dict) -> dict:
    """Return an explainable title/year confidence score for a search result."""
    expected = normalized_name(title)
    offered_names = [str(candidate.get("name") or "")]
    for alias in candidate.get("aliases") or []:
        if isinstance(alias, dict):
            alias = alias.get("name") or alias.get("title") or ""
        if alias:
            offered_names.append(str(alias))
    normalized_offers = [normalized_name(name) for name in offered_names]
    normalized_offers = [name for name in normalized_offers if name]
    title_similarity = max(
        (SequenceMatcher(None, expected, offered).ratio() for offered in normalized_offers),
        default=0,
    ) if expected else 0
    candidate_year_text = str(candidate.get("year") or "")[:4]
    candidate_year = int(candidate_year_text) if candidate_year_text.isdigit() else None
    if year:
        if candidate_year == year:
            year_score = 1.0
        elif candidate_year is not None and abs(candidate_year - year) == 1:
            year_score = 0.5
        else:
            year_score = 0.0
        score = round((title_similarity * 0.85 + year_score * 0.15) * 100)
    else:
        score = round(title_similarity * 100)
    label = "Very high" if score >= 95 else "High" if score >= 80 else "Medium" if score >= 60 else "Low"
    return {
        "score": score, "label": label,
        "exact_title": expected in normalized_offers,
        "exact_year": not year or candidate_year == year,
    }


def broader_movie_queries(title: str) -> list[str]:
    """Generate conservative alternate searches when TVDB returns no strict hit."""
    variants = []
    if re.search(r"\band\b", title, flags=re.IGNORECASE):
        variants.append(re.sub(r"\band\b", "&", title, flags=re.IGNORECASE))
    if "&" in title:
        variants.append(title.replace("&", "and"))
    without_format = re.sub(r"\b(?:3D|IMAX)\b", "", title, flags=re.IGNORECASE)
    without_format = re.sub(r"\s{2,}", " ", without_format).strip(" -:.")
    if without_format and without_format.casefold() != title.casefold():
        variants.append(without_format)
    return list(dict.fromkeys(query for query in variants if query.strip()))


def search_movies_broadly(query: str) -> list[dict]:
    merged: list[dict] = []
    seen_ids: set[str] = set()
    for movie_query in [query, *broader_movie_queries(query)]:
        for result in tvdb.search_movies(movie_query):
            result_id = str(result.get("tvdb_id") or result.get("id") or "")
            identity = result_id or normalized_name(str(result.get("name") or ""))
            if identity in seen_ids:
                continue
            seen_ids.add(identity)
            merged.append(result)
    return merged


def broader_series_queries(title: str) -> list[str]:
    """Generate safe TVDB query variants while leaving final choice to the user."""
    original = " ".join(title[:1000].split())
    variants: list[str] = []
    cleaned_parts: list[str] = []
    cursor = 0
    while cursor < len(original):
        opening = original.find("{", cursor)
        if opening < 0:
            cleaned_parts.append(original[cursor:])
            break
        closing = original.find("}", opening + 1)
        marker = original[opening + 1:closing].casefold() if closing >= 0 else ""
        if closing >= 0 and marker.startswith(("tvdb-", "tmdb-", "imdb-")):
            cleaned_parts.extend((original[cursor:opening], " "))
            cursor = closing + 1
        else:
            cleaned_parts.append(original[cursor:opening + 1])
            cursor = opening + 1
    cleaned = " ".join("".join(cleaned_parts).split())
    for opening, closing in (("(", ")"), ("[", "]")):
        if not cleaned.endswith(closing):
            continue
        start = cleaned.rfind(opening)
        if start < 0:
            continue
        value = cleaned[start + 1:-1].replace(" ", "").casefold()
        years = value.split("-", 1)
        first_year = len(years[0]) == 4 and years[0].isdigit()
        last_year = len(years) == 1 or years[1] == "present" or (
            len(years[1]) == 4 and years[1].isdigit()
        )
        if first_year and years[0][:2] in {"19", "20"} and last_year:
            cleaned = cleaned[:start].strip()
            break
    cleaned = cleaned.strip(" -:.")
    if cleaned and cleaned.casefold() != original.casefold():
        variants.append(cleaned)

    variant_base = cleaned or original
    words = variant_base.split()
    if any(word.casefold() == "and" for word in words):
        variants.append(" ".join("&" if word.casefold() == "and" else word for word in words))
    if "&" in variant_base:
        variants.append(variant_base.replace("&", "and"))

    punctuation_cleaned = " ".join("".join(
        character if character.isalnum() or character.isspace() or character in "_&" else " "
        for character in variant_base
    ).split())
    if punctuation_cleaned and punctuation_cleaned.casefold() != variant_base.casefold():
        variants.append(punctuation_cleaned)

    subtitle_positions = [
        variant_base.find(character) for character in (":", "–", "—")
        if character in variant_base
    ]
    subtitle = variant_base[:min(subtitle_positions)].strip() if subtitle_positions else variant_base
    if len(subtitle) >= 3 and subtitle.casefold() != variant_base.casefold():
        variants.append(subtitle)
    return list(dict.fromkeys(query for query in variants if query.strip()))[:5]


def search_series_broadly(query: str) -> list[dict]:
    merged: list[dict] = []
    seen_ids: set[str] = set()
    for series_query in [query, *broader_series_queries(query)]:
        for result in tvdb.search_series(series_query):
            result_id = str(result.get("tvdb_id") or result.get("id") or "")
            identity = result_id or normalized_name(str(result.get("name") or ""))
            if identity in seen_ids:
                continue
            seen_ids.add(identity)
            merged.append(result)
    return merged


def run_movie_match_analysis(title_ids: list[int]) -> None:
    with movie_match_lock:
        movie_match_job.update({"status": "running", "processed": 0, "matched": 0, "errors": 0})
    matched = errors = 0
    record_event("metadata", f"Movie match lookup started for {len(title_ids):,} titles.")
    for index, title_id in enumerate(title_ids, start=1):
        with db.connect() as conn:
            movie = conn.execute(
                "SELECT * FROM titles WHERE id=? AND kind='movie'", (title_id,)
            ).fetchone()
        if not movie:
            continue
        candidate = confidence = None
        result_count = 0
        error = ""
        for attempt in range(3):
            try:
                results = tvdb.search_movies(movie["title"], movie["year"])
                possible_query = ""
                if not results:
                    # A year filter can hide remakes, alternate release years, and
                    # records whose TVDB year differs from the filename year.
                    results = tvdb.search_movies(movie["title"])
                    possible_query = movie["title"] if results else ""
                if not results:
                    for alternate_query in broader_movie_queries(movie["title"]):
                        time.sleep(0.2)
                        results = tvdb.search_movies(alternate_query)
                        if results:
                            possible_query = alternate_query
                            break
                scored = [
                    (result, match_confidence(movie["title"], movie["year"], result))
                    for result in results
                ]
                scored.sort(key=lambda item: item[1]["score"], reverse=True)
                candidate, confidence = scored[0] if scored else (None, None)
                if candidate and possible_query:
                    candidate = {
                        **candidate, "_possible_match": True,
                        "_search_query": possible_query,
                    }
                result_count = len(results)
                break
            except Exception as exc:
                error = str(exc)
                if "429" in error:
                    record_event(
                        "api", "TheTVDB asked InfoMancer to slow down movie lookups.",
                        level="warning",
                        detail="The request will be retried automatically with a longer delay.",
                        context={"title_id": title_id, "attempt": attempt + 1},
                    )
                if attempt < 2 and ("429" in error or re.search(r"TVDB returned 5\d\d", error)):
                    time.sleep(2 ** (attempt + 1))
                    continue
                break
        if candidate:
            matched += 1
        if error:
            errors += 1
            record_event(
                "metadata",
                f"No movie match could be prepared for {movie['title']}.",
                level="warning", detail=error, context={"title_id": title_id},
            )
        exact = bool(
            confidence and confidence["exact_title"] and confidence["exact_year"]
            and not (candidate or {}).get("_possible_match")
        )
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO movie_match_suggestions
                   (title_id, candidate_json, confidence_score, confidence_label,
                    result_count, exact, error, analyzed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(title_id) DO UPDATE SET
                     candidate_json=excluded.candidate_json,
                     confidence_score=excluded.confidence_score,
                     confidence_label=excluded.confidence_label,
                     result_count=excluded.result_count, exact=excluded.exact,
                     error=excluded.error, analyzed_at=CURRENT_TIMESTAMP""",
                (
                    title_id, json.dumps(candidate) if candidate else None,
                    confidence["score"] if confidence else None,
                    confidence["label"] if confidence else None,
                    result_count, int(exact), error,
                ),
            )
        with movie_match_lock:
            movie_match_job.update({
                "processed": index, "matched": matched, "errors": errors,
                "current": movie["title"],
            })
        # Keep the queue gentle; transient 429/5xx responses receive longer retries above.
        if index < len(title_ids):
            time.sleep(0.35)
    with movie_match_lock:
        movie_match_job.update({
            "status": "complete", "processed": len(title_ids),
            "matched": matched, "errors": errors, "current": "",
        })
    record_event(
        "metadata",
        f"Movie match lookup finished: {matched:,} suggestions and {errors:,} errors.",
        level="warning" if errors else "info",
        context={"requested": len(title_ids), "suggestions": matched, "errors": errors},
    )


def run_tv_match_analysis(title_ids: list[int]) -> None:
    """Find series-level TVDB candidates; episode records are loaded only after approval."""
    with tv_match_lock:
        tv_match_job.update({"status": "running", "processed": 0, "matched": 0, "errors": 0})
    matched = errors = 0
    record_event("metadata", f"TV match lookup started for {len(title_ids):,} series.")
    for index, title_id in enumerate(title_ids, start=1):
        with db.connect() as conn:
            show = conn.execute("SELECT * FROM titles WHERE id=? AND kind='tv'", (title_id,)).fetchone()
        if not show:
            continue
        candidate = confidence = None
        result_count = 0
        error = ""
        for attempt in range(3):
            try:
                results = search_series_broadly(show["title"])
                scored = [(result, match_confidence(show["title"], show["year"], result)) for result in results]
                scored.sort(key=lambda item: item[1]["score"], reverse=True)
                candidate, confidence = scored[0] if scored else (None, None)
                result_count = len(results)
                break
            except Exception as exc:
                error = str(exc)
                if "429" in error:
                    record_event(
                        "api", "TheTVDB asked InfoMancer to slow down TV lookups.",
                        level="warning",
                        detail="The request will be retried automatically with a longer delay.",
                        context={"title_id": title_id, "attempt": attempt + 1},
                    )
                if attempt < 2 and ("429" in error or re.search(r"TVDB returned 5\d\d", error)):
                    time.sleep(2 ** (attempt + 1))
                    continue
                break
        matched += int(candidate is not None)
        errors += int(bool(error))
        if error:
            record_event(
                "metadata",
                f"No TV match could be prepared for {show['title']}.",
                level="warning", detail=error, context={"title_id": title_id},
            )
        exact = bool(confidence and confidence["exact_title"] and confidence["exact_year"])
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO tv_match_suggestions
                   (title_id, candidate_json, confidence_score, confidence_label,
                    result_count, exact, error, analyzed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(title_id) DO UPDATE SET
                     candidate_json=excluded.candidate_json,
                     confidence_score=excluded.confidence_score,
                     confidence_label=excluded.confidence_label,
                     result_count=excluded.result_count, exact=excluded.exact,
                     error=excluded.error, analyzed_at=CURRENT_TIMESTAMP""",
                (title_id, json.dumps(candidate) if candidate else None,
                 confidence["score"] if confidence else None,
                 confidence["label"] if confidence else None,
                 result_count, int(exact), error),
            )
        with tv_match_lock:
            tv_match_job.update({"processed": index, "matched": matched, "errors": errors, "current": show["title"]})
        if index < len(title_ids):
            time.sleep(0.35)
    with tv_match_lock:
        tv_match_job.update({"status": "complete", "processed": len(title_ids), "matched": matched, "errors": errors, "current": ""})
    record_event(
        "metadata",
        f"TV match lookup finished: {matched:,} suggestions and {errors:,} errors.",
        level="warning" if errors else "info",
        context={"requested": len(title_ids), "suggestions": matched, "errors": errors},
    )


def store_movie_match(title_id: int, movie_id: int) -> str:
    movie = tvdb.movie(movie_id)
    tmdb_id, imdb_id = plex_movie_ids(movie)
    year_value = str(movie.get("year") or "")[:4]
    metadata_year = int(year_value) if year_value.isdigit() else None
    with db.connect() as conn:
        title = conn.execute(
            "SELECT id, kind, metadata_title FROM titles WHERE id=?", (title_id,)
        ).fetchone()
        if not title or title["kind"] != "movie":
            raise ValueError("Movie not found")
        metadata_title, title_language = localized_tvdb_title(
            movie, title["metadata_title"]
        )
        conn.execute(
            """UPDATE titles SET tvdb_movie_id=?, tmdb_id=?, imdb_id=?, poster_url=?,
               metadata_title=?, metadata_title_language=?, metadata_year=?,
               overview=?,
               matched_at=CURRENT_TIMESTAMP,
               imdb_checked_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (movie_id, tmdb_id, imdb_id, poster_from(movie), metadata_title,
             title_language or None, metadata_year,
             str(movie.get("overview") or "").strip(), title_id),
        )
        conn.execute("DELETE FROM movie_match_suggestions WHERE title_id=?", (title_id,))
    return f"TMDB {tmdb_id}" if tmdb_id else (f"IMDb {imdb_id}" if imdb_id else "TVDB metadata")


templates.env.globals["lifespan"] = lifespan
templates.env.globals["title_initial"] = title_initial
templates.env.globals["display_title_type"] = display_title_type
templates.env.globals["format_bytes"] = format_bytes
templates.env.globals["local_time"] = local_time
templates.env.globals["static_version"] = str(int(time.time()))


def redirect(path: str, message: str = "") -> RedirectResponse:
    base, fragment_marker, fragment = path.partition("#")
    separator = "&" if "?" in base else "?"
    target = base + (f"{separator}message={quote_plus(message)}" if message else "")
    if fragment_marker:
        target += f"#{fragment}"
    return RedirectResponse(safe_next(target), status_code=303)


def preauth_response(request: Request, template_name: str, context: dict) -> HTMLResponse:
    token = request.cookies.get(PREAUTH_COOKIE, "") or secrets.token_urlsafe(32)
    context = {**context, "preauth_token": token, "message": context.get("message", "")}
    response = templates.TemplateResponse(request, template_name, context)
    response.set_cookie(
        PREAUTH_COOKIE, token, httponly=True,
        secure=secure_cookie_for(request, settings), samesite="lax",
        max_age=600, path="/",
    )
    return response


def valid_preauth(request: Request, submitted: str) -> bool:
    stored = request.cookies.get(PREAUTH_COOKIE, "")
    return bool(stored and submitted and constant_time_equal(stored, submitted))


def signed_in_response(request: Request, user: AuthUser, destination: str = "/"):
    raw_token, _ = auth_service.create_session(user, request)
    response = RedirectResponse(safe_next(destination), status_code=303)
    set_session_cookie(response, request, raw_token)
    response.delete_cookie(PREAUTH_COOKIE, path="/")
    return response


def activation_context(
    request: Request, token: str, error: str = "", invitation=None,
) -> dict:
    return {
        "token": token, "error": error, "invitation": invitation,
        "message": "",
    }


def public_activation_url(request: Request, token: str) -> str:
    generated = request.url_for("activate_page", token=token)
    if settings.public_url:
        return settings.public_url.rstrip("/") + generated.path
    return str(generated)


def user_admin_context(
    request: Request, error: str = "", invitation_url: str = "",
    invitation_expires: str = "", invitation_user: AuthUser | None = None,
) -> dict:
    return {
        "users": auth_service.list_users(), "profile_icons": PROFILE_ICONS,
        "message": request.query_params.get("message", ""), "error": error,
        "invitation_url": invitation_url,
        "invitation_expires": invitation_expires,
        "invitation_user": invitation_user,
    }


@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    if auth_service.user_count():
        bootstrap_tokens.clear()
        return redirect("/login" if settings.auth_mode == "local" else "/")
    if not settings.sandbox:
        bootstrap_tokens.token()
    claims = getattr(request.state, "external_claims", {})
    email = str(claims.get("email") or "")
    suggested = re.sub(r"[^A-Za-z0-9._-]", "", email.split("@", 1)[0])[:50]
    if len(suggested) < 3:
        suggested = "librarian"
    return preauth_response(request, "setup.html", {
        "email": email, "username": suggested,
        "display_name": str(claims.get("name") or suggested),
        "requires_password": settings.auth_mode == "local",
        "error": request.query_params.get("message", ""),
    })


@app.post("/setup")
def setup_account(
    request: Request, username: str = Form(...), email: str = Form(""),
    display_name: str = Form(...), profile_icon: str = Form("initials"),
    password: str = Form(""), password_confirm: str = Form(""),
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
        return preauth_response(request, "setup.html", {
            "username": username, "email": email, "display_name": display_name,
            "requires_password": True, "error": "Passwords do not match.",
        })
    try:
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
        return preauth_response(request, "setup.html", {
            "username": username, "email": email, "display_name": display_name,
            "requires_password": settings.auth_mode == "local", "error": str(exc),
        })
    bootstrap_tokens.clear()
    welcome = quote_plus(
        f"Librarian account created successfully. Welcome, {user.display_name}!"
    )
    return signed_in_response(
        request, user, f"/?message={welcome}&account_notice=1"
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    if settings.auth_mode != "local":
        return redirect("/")
    if not auth_service.user_count():
        return redirect("/setup")
    return preauth_response(request, "login.html", {
        "next": safe_next(next), "identity": "", "error": "",
        "message": request.query_params.get("message", ""),
    })


@app.post("/login")
def login(
    request: Request, identity: str = Form(...), password: str = Form(...),
    next: str = Form("/"), preauth_token: str = Form(""),
):
    if settings.auth_mode != "local":
        return redirect("/")
    if not valid_preauth(request, preauth_token):
        return redirect("/login", "Sign-in form expired. Please try again.")
    client_ip = request_ip(request, settings)
    try:
        user = auth_service.authenticate_local(identity, password, client_ip)
    except LoginLocked as exc:
        if exc.new_lockout:
            locked_user = auth_service.get_user(exc.user_id) if exc.user_id else None
            subject = locked_user.display_name if locked_user else "an account"
            record_security_event(
                f"Repeated sign-in attempts were blocked for {subject}.",
                level="warning",
                detail=(
                    f"Temporary lock scope: {exc.scope or 'existing'}. "
                    f"Source IP: {client_ip or 'unknown'}."
                ),
                context={
                    "operation": "login_lockout", "scope": exc.scope,
                    "ip_address": client_ip,
                },
                user_id=exc.user_id, notify_librarian=True,
            )
        return preauth_response(request, "login.html", {
            "next": safe_next(next), "identity": identity, "error": str(exc),
        })
    except AuthenticationError as exc:
        return preauth_response(request, "login.html", {
            "next": safe_next(next), "identity": identity, "error": str(exc),
        })
    record_security_event(
        "Local account signed in.",
        context={"operation": "login_success", "ip_address": client_ip},
        user_id=user.id,
    )
    return signed_in_response(request, user, next)


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    if settings.auth_mode != "local":
        return redirect("/")
    return templates.TemplateResponse(request, "forgot_password.html", {
        "message": request.query_params.get("message", ""),
    })


@app.get("/activate/{token}", response_class=HTMLResponse, name="activate_page")
def activate_page(request: Request, token: str):
    if settings.auth_mode != "local":
        return templates.TemplateResponse(request, "activate.html", activation_context(
            request, token,
            "This installation uses an external sign-in provider, so local setup links are not accepted.",
        ), status_code=400)
    try:
        invitation = auth_service.invitation_for_token(token)
    except AuthenticationError as exc:
        return templates.TemplateResponse(request, "activate.html", activation_context(
            request, token, str(exc),
        ), status_code=400)
    return preauth_response(request, "activate.html", activation_context(
        request, token, request.query_params.get("message", ""), invitation,
    ))


@app.post("/activate/{token}")
def activate_account(
    request: Request, token: str, password: str = Form(...),
    password_confirm: str = Form(...), preauth_token: str = Form(""),
):
    if not valid_preauth(request, preauth_token):
        return redirect(
            f"/activate/{token}",
            "The setup form expired before it was submitted. Reload this link and try again.",
        )
    if password != password_confirm:
        try:
            invitation = auth_service.invitation_for_token(token)
        except AuthenticationError as exc:
            return templates.TemplateResponse(request, "activate.html", activation_context(
                request, token, str(exc),
            ), status_code=400)
        return preauth_response(request, "activate.html", activation_context(
            request, token, "The two passwords do not match. Enter the same password twice.",
            invitation,
        ))
    try:
        user = auth_service.accept_invitation(token, password)
    except AuthenticationError as exc:
        return templates.TemplateResponse(request, "activate.html", activation_context(
            request, token, str(exc),
        ), status_code=400)
    welcome = quote_plus(
        f"Your account is ready. Welcome to InfoMancer, {user.display_name}!"
    )
    return signed_in_response(
        request, user, f"/?message={welcome}&account_notice=1"
    )


@app.post("/logout")
def logout(request: Request):
    session = request.state.auth_session
    if session:
        auth_service.revoke_session(session.id, request.state.user.id)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/account/profile", response_class=HTMLResponse)
def account_profile(request: Request):
    return templates.TemplateResponse(request, "account_profile.html", {
        "profile_icons": PROFILE_ICONS,
        "message": request.query_params.get("message", ""), "error": "",
    })


@app.post("/account/profile")
def update_account_profile(
    request: Request, display_name: str = Form(...), email: str = Form(""),
    profile_icon: str = Form("initials"), show_home_hero: str = Form(""),
    high_contrast: str = Form(""),
):
    try:
        auth_service.update_profile(
            request.state.user.id, display_name, email, profile_icon,
            show_home_hero == "1", high_contrast == "1",
        )
    except AuthenticationError as exc:
        return templates.TemplateResponse(request, "account_profile.html", {
            "profile_icons": PROFILE_ICONS, "message": "", "error": str(exc),
        }, status_code=400)
    return redirect("/account/profile", "Profile saved")


@app.post("/account/home-layout")
def toggle_account_home_layout(request: Request):
    if request.state.user.id <= 0:
        return redirect(
            "/",
            "Home layout preferences require a signed-in account.",
        )
    auth_service.toggle_home_layout(request.state.user.id)
    return RedirectResponse("/", status_code=303)


@app.get("/account/security", response_class=HTMLResponse)
def account_security(request: Request):
    return templates.TemplateResponse(request, "account_security.html", {
        "message": request.query_params.get("message", ""), "error": "",
        "local_password": settings.auth_mode == "local",
    })


@app.post("/account/security")
def change_account_password(
    request: Request, current_password: str = Form(""),
    new_password: str = Form(...), password_confirm: str = Form(...),
):
    if new_password != password_confirm:
        return templates.TemplateResponse(request, "account_security.html", {
            "message": "", "error": "Passwords do not match.",
            "local_password": settings.auth_mode == "local",
        }, status_code=400)
    try:
        auth_service.change_password(request.state.user.id, current_password, new_password)
        auth_service.revoke_user_sessions(
            request.state.user.id, except_session=request.state.auth_session.id
        )
        record_security_event(
            "Account password was changed and other sessions were revoked.",
            context={"operation": "password_changed"},
            user_id=request.state.user.id,
        )
    except AuthenticationError as exc:
        return templates.TemplateResponse(request, "account_security.html", {
            "message": "", "error": str(exc),
            "local_password": settings.auth_mode == "local",
        }, status_code=400)
    return redirect("/account/security", "Password changed; other sessions were signed out")


@app.get("/account/sessions", response_class=HTMLResponse)
def account_sessions(request: Request):
    return templates.TemplateResponse(request, "account_sessions.html", {
        "sessions": auth_service.list_sessions(request.state.user.id),
        "message": request.query_params.get("message", ""),
    })


@app.post("/account/sessions/{session_id}/revoke")
def revoke_account_session(request: Request, session_id: int):
    current = request.state.auth_session
    auth_service.revoke_session(session_id, request.state.user.id)
    if current and current.id == session_id:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response
    return redirect("/account/sessions", "Session signed out")


@app.post("/account/sessions/revoke-others")
def revoke_other_sessions(request: Request):
    auth_service.revoke_user_sessions(
        request.state.user.id, except_session=request.state.auth_session.id
    )
    return redirect("/account/sessions", "Other sessions signed out")


@app.get("/tour", response_class=HTMLResponse)
def onboarding_tour(request: Request):
    return redirect("/?tour=1&tour_step=0")


@app.post("/engagement/tour")
def save_tour_state(request: Request, state: str = Form(...)):
    if state not in {"completed", "dismissed"}:
        return auth_error_response(
            request, 400, "Tour status not saved",
            "Choose Finish or Skip from the tour and try again.",
        )
    engagement.set_tour_state(
        request.state.user.id, completed=state == "completed"
    )
    return JSONResponse({"saved": True, "state": state})


SETUP_STEPS = ("general", "metadata", "sources", "finish")


def setup_assistant_context(request: Request, step: str, error: str = "") -> dict:
    preferences = app_settings.values()
    state = engagement.setup_state(request.state.user.id)
    with db.connect() as conn:
        roots = conn.execute(
            """SELECT r.*, COUNT(DISTINCT t.id) title_count, COUNT(f.id) file_count
               FROM roots r LEFT JOIN titles t ON t.root_id=r.id
               LEFT JOIN files f ON f.title_id=t.id GROUP BY r.id
               ORDER BY r.kind,r.label,r.path"""
        ).fetchall()
    return {
        "step": step, "steps": SETUP_STEPS, "setup_state": state,
        "preferences": preferences, "roots": roots, "error": error,
        "timezone_groups": timezone_groups(),
        "message": request.query_params.get("message", ""),
        "tvdb_status": {
            "configured": bool(tvdb.api_key),
            "pin_configured": bool(tvdb.pin),
            "storage_error": provider_secret_error,
            "key_hint": (
                f"Connected · key ending in {tvdb.api_key[-4:]}"
                if tvdb.api_key else "Not connected"
            ),
        },
    }


@librarian_get("/getting-started", response_class=HTMLResponse)
def getting_started(request: Request):
    if request.state.user.id <= 0:
        return redirect("/sources", "Setup Assistant requires local or external user accounts.")
    state = engagement.setup_state(request.state.user.id)
    step = state["current_step"] if state and not state["completed_at"] else "general"
    return redirect(f"/getting-started/{step}")


@librarian_post("/getting-started/choice")
def choose_getting_started(request: Request, mode: str = Form(...)):
    if request.state.user.id <= 0:
        return redirect("/sources", "Setup Assistant requires local or external user accounts.")
    if mode not in {"guided", "manual"}:
        return redirect("/", "Choose Guided setup or Set up manually.")
    engagement.begin_setup(request.state.user.id, mode)
    if mode == "manual":
        return redirect("/", "Manual setup selected. Add a source whenever you are ready.")
    return redirect("/getting-started/general")


@librarian_post("/getting-started/restart")
def restart_getting_started(request: Request):
    if request.state.user.id <= 0:
        return redirect("/sources", "Setup Assistant requires local or external user accounts.")
    engagement.begin_setup(request.state.user.id, "guided")
    return redirect("/getting-started/general")


@librarian_get("/getting-started/{step}", response_class=HTMLResponse)
def getting_started_step(request: Request, step: str):
    if request.state.user.id <= 0:
        return redirect("/sources", "Setup Assistant requires local or external user accounts.")
    if step not in SETUP_STEPS:
        return auth_error_response(
            request, 404, "Setup step not found",
            "That setup step does not exist. Open the Setup Assistant and try again.",
        )
    return templates.TemplateResponse(
        request, "getting_started.html", setup_assistant_context(request, step)
    )


@librarian_post("/getting-started/general")
def save_getting_started_general(
    request: Request, timezone_name: str = Form(...),
):
    if request.state.user.id <= 0:
        return redirect("/sources", "Setup Assistant requires local or external user accounts.")
    try:
        values = app_settings.validate_general(
            app_settings.get("installation_name"), timezone_name, "list",
            app_settings.get("default_cover_size"),
        )
        app_settings.update(values, request.state.user.id)
    except AppSettingError as exc:
        context = setup_assistant_context(request, "general", str(exc))
        context["preferences"].update({"timezone": timezone_name})
        return templates.TemplateResponse(
            request, "getting_started.html", context, status_code=400
        )
    engagement.set_setup_step(request.state.user.id, "metadata")
    return redirect("/getting-started/metadata", "Installation preferences saved.")


@librarian_post("/getting-started/metadata")
def continue_getting_started_metadata(
    request: Request, api_key: str = Form(""), subscriber_pin: str = Form(""),
    testing_skip: str = Form(""),
):
    global tvdb, provider_secret_error, stored_provider_secrets
    if request.state.user.id <= 0:
        return redirect("/sources", "Setup Assistant requires local or external user accounts.")
    if settings.sandbox and testing_skip == "1":
        engagement.set_setup_step(request.state.user.id, "sources")
        return redirect(
            "/getting-started/sources",
            "TVDB was skipped for this testing environment. Add a media source to continue.",
        )
    candidate_key = api_key.strip() or tvdb.api_key
    candidate_pin = (
        subscriber_pin.strip()
        if api_key.strip() else subscriber_pin.strip() or tvdb.pin
    )
    if not candidate_key:
        context = setup_assistant_context(
            request, "metadata",
            "Enter your TVDB API key before continuing. If TVDB supplied a subscriber PIN, enter that too.",
        )
        return templates.TemplateResponse(
            request, "getting_started.html", context, status_code=400
        )
    candidate = TVDBClient(candidate_key, candidate_pin)
    try:
        candidate.test_connection()
        if api_key.strip() or subscriber_pin.strip():
            provider_secrets.update({
                "tvdb_api_key": candidate_key, "tvdb_pin": candidate_pin,
            })
            stored_provider_secrets.update({
                "tvdb_api_key": candidate_key, "tvdb_pin": candidate_pin,
            })
            provider_secret_error = ""
    except ProviderSecretError as exc:
        context = setup_assistant_context(request, "metadata", str(exc))
        return templates.TemplateResponse(
            request, "getting_started.html", context, status_code=500
        )
    except TVDBError:
        context = setup_assistant_context(
            request, "metadata",
            "TVDB did not accept that API key and PIN. Check them in your TVDB account, then try again.",
        )
        return templates.TemplateResponse(
            request, "getting_started.html", context, status_code=400
        )
    except Exception:
        context = setup_assistant_context(
            request, "metadata",
            "InfoMancer could not reach TVDB. Check this server's internet connection, then try again.",
        )
        return templates.TemplateResponse(
            request, "getting_started.html", context, status_code=503
        )
    tvdb = candidate
    engagement.set_setup_step(request.state.user.id, "sources")
    return redirect(
        "/getting-started/sources",
        "TVDB connection verified and saved securely. Add a media source to continue.",
    )


@librarian_post("/getting-started/sources")
def continue_getting_started_sources(request: Request):
    if request.state.user.id <= 0:
        return redirect("/sources", "Setup Assistant requires local or external user accounts.")
    with db.connect() as conn:
        has_source = conn.execute("SELECT 1 FROM roots LIMIT 1").fetchone()
    if not has_source:
        context = setup_assistant_context(
            request, "sources",
            "Add at least one Movie or TV Shows folder before continuing. Use Browse folders below to choose one.",
        )
        return templates.TemplateResponse(
            request, "getting_started.html", context, status_code=400
        )
    engagement.set_setup_step(request.state.user.id, "finish")
    return redirect("/getting-started/finish")


@librarian_post("/getting-started/complete")
def complete_getting_started(request: Request):
    if request.state.user.id <= 0:
        return redirect("/sources", "Setup Assistant requires local or external user accounts.")
    engagement.complete_setup(request.state.user.id)
    return redirect("/", "Setup Assistant completed. Your library is ready.")


def announcement_page_context(
    request: Request, error: str = "", submitted: dict | None = None,
) -> dict:
    user = request.state.user
    rows = engagement.list_for_user(user.id, user.role)
    now_local = datetime.now(ZoneInfo(app_settings.get("timezone")))
    return {
        "announcements": rows,
        "managed_announcements": (
            engagement.list_managed() if user.is_librarian else []
        ),
        "announcement_error": error,
        "announcement_form": submitted or {},
        "announcement_now": now_local.strftime("%Y-%m-%dT%H:%M"),
        "message": request.query_params.get("message", ""),
    }


@librarian_post("/admin/announcements")
def create_announcement(
    request: Request, title: str = Form(...), body: str = Form(...),
    category: str = Form("information"), audience: str = Form("members"),
    starts_at: str = Form(...), ends_at: str = Form(""),
    recurrence: str = Form("once"),
):
    submitted = {
        "title": title, "body": body, "category": category,
        "audience": audience, "starts_at": starts_at,
        "ends_at": ends_at, "recurrence": recurrence,
    }
    recurrence_days = {"once": None, "daily": 1, "weekly": 7}.get(recurrence)
    try:
        if recurrence not in {"once", "daily", "weekly"}:
            raise EngagementError("Choose Once, Daily, or Weekly for announcement delivery.")
        timezone_name = app_settings.get("timezone")
        start_utc = utc_from_local(starts_at, timezone_name)
        end_utc = utc_from_local(ends_at, timezone_name) if ends_at.strip() else None
        engagement.create(
            title, body, category, audience, start_utc, end_utc,
            recurrence_days, request.state.user.id,
        )
    except EngagementError as exc:
        return templates.TemplateResponse(
            request, "announcements.html",
            announcement_page_context(request, str(exc), submitted),
            status_code=400,
        )
    return redirect(
        "/announcements",
        "Announcement published. It will appear for the selected audience at the scheduled time.",
    )


@librarian_post("/admin/announcements/{announcement_id}/deactivate")
def deactivate_announcement(announcement_id: int):
    try:
        engagement.deactivate(announcement_id)
    except EngagementError as exc:
        return redirect("/announcements", str(exc))
    return redirect(
        "/announcements",
        "Announcement ended. It will no longer appear as a popup.",
    )


@librarian_get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request):
    return templates.TemplateResponse(
        request, "admin_users.html", user_admin_context(request)
    )


@librarian_post("/admin/users")
def create_admin_user(
    request: Request, username: str = Form(...), email: str = Form(""),
    display_name: str = Form(...), role: str = Form("member"),
):
    try:
        user = auth_service.create_user(
            username, email, display_name, "", role=role,
            require_password=False,
        )
        if settings.auth_mode == "local":
            raw_token, expires = auth_service.create_invitation(
                user.id, request.state.user.id
            )
            invitation_url = public_activation_url(request, raw_token)
            return templates.TemplateResponse(
                request, "admin_users.html", user_admin_context(
                    request, invitation_url=invitation_url,
                    invitation_expires=expires, invitation_user=user,
                ),
            )
    except AuthenticationError as exc:
        return templates.TemplateResponse(
            request, "admin_users.html", user_admin_context(request, error=str(exc)),
            status_code=400,
        )
    return redirect(
        "/admin/users?account_notice=1",
        f"{display_name.strip()} account created and is ready for Cloudflare sign-in",
    )


@librarian_post("/admin/users/{user_id}/invitation")
def create_user_invitation(request: Request, user_id: int):
    try:
        raw_token, expires = auth_service.create_invitation(
            user_id, request.state.user.id
        )
        user = auth_service.get_user(user_id)
        invitation_url = public_activation_url(request, raw_token)
    except AuthenticationError as exc:
        return templates.TemplateResponse(
            request, "admin_users.html", user_admin_context(request, error=str(exc)),
            status_code=400,
        )
    return templates.TemplateResponse(
        request, "admin_users.html", user_admin_context(
            request, invitation_url=invitation_url,
            invitation_expires=expires, invitation_user=user,
        ),
    )


@librarian_post("/admin/users/{user_id}/invitation/revoke")
def revoke_user_invitation(request: Request, user_id: int):
    revoked = auth_service.revoke_invitations(user_id)
    if revoked:
        return redirect(
            "/admin/users", "The pending setup link was revoked successfully."
        )
    return redirect(
        "/admin/users",
        "There was no active setup link to revoke; the account was not changed.",
    )


@librarian_post("/admin/users/{user_id}")
def update_admin_user(
    request: Request, user_id: int, display_name: str = Form(...),
    email: str = Form(""), role: str = Form("member"), active: str = Form("0"),
):
    try:
        auth_service.update_user_admin(
            user_id, display_name, email, role, active == "1", request.state.user.id
        )
    except AuthenticationError as exc:
        return redirect("/admin/users", str(exc))
    return redirect("/admin/users", "User updated")


@librarian_post("/admin/users/{user_id}/sessions/revoke")
def revoke_admin_sessions(request: Request, user_id: int):
    auth_service.revoke_user_sessions(user_id)
    return redirect("/admin/users", "User sessions signed out")


def match_success_redirect(
    title_id: int, message: str, return_to: str = "", match_origin: str = "",
) -> RedirectResponse:
    bulk_returns = {
        "bulk-movie": "/movies/bulk-match?review=true",
        "bulk-movie-selected": "/movies/bulk-match?review=true&selected=true",
        "bulk-tv": "/shows/bulk-match?review=true",
        "bulk-tv-selected": "/shows/bulk-match?review=true&selected=true",
    }
    safe_return = bulk_returns.get(match_origin, "")
    return_label = "Back to Bulk Match" if safe_return else "Back to search results"
    if not safe_return:
        parsed = urlparse(return_to)
        expected_path = f"/titles/{title_id}/tvdb"
        is_library_return = parsed.path in {"/library", "/movies", "/shows"}
        if not parsed.scheme and not parsed.netloc and (
            parsed.path == expected_path or is_library_return
        ):
            safe_return = return_to
            if is_library_return:
                return_label = "Back to Library"
    path = f"/titles/{title_id}"
    if safe_return:
        path += "?" + urlencode({
            "return_to": safe_return, "return_label": return_label, "match_notice": "1",
        })
    return redirect(path, message)


def run_media_inspection(file_ids: list[int] | None = None) -> None:
    with media_info_lock:
        media_info_job.clear()
        media_info_job.update({
            "status": "running", "processed": 0, "total": 0,
            "updated": 0, "errors": 0, "current": "",
        })
    with db.connect() as conn:
        if file_ids:
            placeholders = ",".join("?" for _ in file_ids)
            rows = conn.execute(
                f"""SELECT f.id,f.path,f.filename,t.metadata_title,t.title
                    FROM files f JOIN titles t ON t.id=f.title_id
                    WHERE f.id IN ({placeholders}) ORDER BY f.id""",
                tuple(file_ids),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT f.id,f.path,f.filename,t.metadata_title,t.title
                   FROM files f JOIN titles t ON t.id=f.title_id
                   WHERE f.media_info_at IS NULL OR
                     (f.media_info_error IS NOT NULL AND f.media_info_error!='')
                   ORDER BY f.id"""
            ).fetchall()
    with media_info_lock:
        media_info_job["total"] = len(rows)
    record_event(
        "media", f"Media inspection started for {len(rows):,} files.",
        context={"file_count": len(rows)},
    )
    updated = errors = 0
    for index, row in enumerate(rows, start=1):
        label = f"{row['metadata_title'] or row['title']} · {row['filename']}"
        with media_info_lock:
            media_info_job.update({"processed": index - 1, "current": label})
        try:
            values = inspect_media(Path(row["path"]))
            with db.connect() as conn:
                conn.execute(
                    """UPDATE files SET runtime_seconds=?,width=?,height=?,
                       video_codec=?,audio_codec=?,audio_channels=?,bitrate=?,
                       container=?,dynamic_range=?,media_info_at=CURRENT_TIMESTAMP,
                       media_info_error=NULL WHERE id=?""",
                    (
                        values["runtime_seconds"], values["width"], values["height"],
                        values["video_codec"], values["audio_codec"],
                        values["audio_channels"], values["bitrate"],
                        values["container"], values["dynamic_range"], row["id"],
                    ),
                )
            updated += 1
            record_event(
                "media", f"Media details collected for {row['filename']}.",
                level="verbose", context={"file_id": row["id"], **values},
            )
        except MediaInspectionError as exc:
            errors += 1
            with db.connect() as conn:
                conn.execute(
                    """UPDATE files SET media_info_at=CURRENT_TIMESTAMP,
                       media_info_error=? WHERE id=?""",
                    (str(exc), row["id"]),
                )
            record_event(
                "media",
                f"{exc.headline}: {row['filename']}",
                level="warning", detail=exc.log_detail,
                context={"file_id": row["id"], "path": row["path"]},
            )
        with media_info_lock:
            media_info_job.update({
                "processed": index, "updated": updated, "errors": errors,
            })
    with media_info_lock:
        media_info_job.update({
            "status": "complete", "processed": len(rows), "updated": updated,
            "errors": errors, "current": "",
        })
    record_event(
        "media",
        f"Media inspection finished: {updated:,} files updated and {errors:,} could not be read.",
        level="warning" if errors else "info",
        context={"updated": updated, "errors": errors},
    )


def run_scan(
    root_id: int, *, hash_after: bool = True, force_cleanup: bool = False,
) -> list[int]:
    before = _file_signatures(root_id=root_id)
    with scan_lock:
        scan_jobs[root_id] = {"status": "running", "files": 0, "titles": 0}

    def report_progress(files: int, titles: int) -> None:
        with scan_lock:
            scan_jobs[root_id] = {
                "status": "running", "files": files, "titles": titles
            }
        with scan_all_lock:
            if (scan_all_job.get("status") == "running"
                    and scan_all_job.get("current_root_id") == root_id):
                scan_all_job.update({"files": files, "titles": titles})

    try:
        with db.connect() as conn:
            root = conn.execute("SELECT * FROM roots WHERE id=?", (root_id,)).fetchone()
            if not root:
                raise ValueError("Library root no longer exists")
            result = scan_root(
                conn, root, report_progress, force_cleanup=force_cleanup,
            )
        with scan_lock:
            scan_jobs[root_id] = {"status": "complete", **result}
        if result.get("source_status") == "degraded":
            record_event(
                "scan",
                f"Source Guard preserved {result['preserved']:,} catalog files because the source scan was incomplete.",
                level="warning", context={"root_id": root_id, **result},
            )
        else:
            record_event(
                "scan",
                f"Source scan finished: {result['files']:,} video files across {result['titles']:,} titles.",
                context={"root_id": root_id, **result},
            )
        changed = _changed_file_ids(before, _file_signatures(root_id=root_id))
        if hash_after:
            handle_import_hashing(changed, "New or changed media found during a source scan")
        try:
            analyze_library_health_with_activity()
        except sqlite3.Error as exc:
            record_event(
                "mie", "Library Health could not refresh after a source scan.",
                level="error", detail=str(exc), context={"root_id": root_id},
            )
        return changed
    except Exception as exc:
        if isinstance(exc, (SourceUnavailableError, OSError)):
            checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with db.connect() as conn:
                conn.execute(
                    """UPDATE roots SET health_status='offline',last_checked_at=?,
                       last_error=? WHERE id=?""",
                    (checked_at, str(exc)[:1000], root_id),
                )
        with scan_lock:
            scan_jobs[root_id] = {"status": "error", "error": str(exc)}
        record_event(
            "scan", "Source scan could not finish.",
            level="error", detail=str(exc), context={"root_id": root_id},
        )
        if isinstance(exc, (SourceUnavailableError, OSError)):
            try:
                analyze_library_health_with_activity()
            except sqlite3.Error:
                pass
        return []


def check_source_health(root_id: int) -> dict:
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.connect() as conn:
        root = conn.execute("SELECT * FROM roots WHERE id=?", (root_id,)).fetchone()
        if not root:
            raise ValueError("That source no longer exists.")
        path = Path(root["path"])
        try:
            if not path.is_dir():
                raise OSError("The configured folder is not available.")
            with os.scandir(path) as entries:
                has_entries = next(entries, None) is not None
        except OSError as exc:
            conn.execute(
                """UPDATE roots SET health_status='offline',last_checked_at=?,
                   last_error=? WHERE id=?""",
                (checked_at, str(exc)[:1000], root_id),
            )
            return {"status": "offline", "path": str(path), "error": str(exc)}
        baseline = int(root["last_file_count"] or 0)
        if baseline and not has_entries:
            message = (
                f"The folder opened, but it appears empty while {baseline:,} catalog files are protected."
            )
            conn.execute(
                """UPDATE roots SET health_status='degraded',last_checked_at=?,last_seen_at=?,
                   last_error=?,last_observed_file_count=0,guard_preserved_count=? WHERE id=?""",
                (checked_at, checked_at, message, baseline, root_id),
            )
            return {"status": "degraded", "path": str(path), "error": message}
        if root["health_status"] == "degraded":
            message = (
                "The source root is reachable, but a complete guarded scan is still required "
                "to confirm that every catalog location is available."
            )
            conn.execute(
                """UPDATE roots SET last_checked_at=?,last_seen_at=?,last_error=?
                   WHERE id=?""",
                (checked_at, checked_at, message, root_id),
            )
            return {"status": "degraded", "path": str(path), "error": message}
        conn.execute(
            """UPDATE roots SET health_status='healthy',last_checked_at=?,last_seen_at=?,
               last_error='' WHERE id=?""",
            (checked_at, checked_at, root_id),
        )
        return {"status": "healthy", "path": str(path), "error": ""}


def run_scan_all(roots: list[tuple[int, str]]) -> None:
    with scan_all_lock:
        scan_all_job.clear()
        scan_all_job.update({
            "status": "running", "total": len(roots), "completed": 0,
            "errors": 0, "protected": 0, "current_root_id": None, "current_label": "",
            "files": 0, "titles": 0,
        })
    errors = 0
    protected = 0
    record_event("scan", f"Scan all started for {len(roots):,} sources.")
    changed_files: list[int] = []
    for completed, (root_id, label) in enumerate(roots):
        with scan_all_lock:
            scan_all_job.update({
                "current_root_id": root_id, "current_label": label,
                "completed": completed, "files": 0, "titles": 0,
            })
        changed_files.extend(run_scan(root_id, hash_after=False))
        with scan_lock:
            if scan_jobs.get(root_id, {}).get("status") == "error":
                errors += 1
            elif scan_jobs.get(root_id, {}).get("source_status") == "degraded":
                protected += 1
        with scan_all_lock:
            scan_all_job.update({
                "completed": completed + 1, "errors": errors,
                "protected": protected,
            })
    with scan_all_lock:
        scan_all_job.update({
            "status": "complete", "completed": len(roots), "errors": errors,
            "current_root_id": None, "current_label": "",
        })
    handle_import_hashing(
        changed_files, "Fingerprinting new or changed media from all sources"
    )


def analyze_library_health_with_activity() -> int:
    with db.connect() as conn:
        before = {row["fingerprint"] for row in conn.execute(
            "SELECT fingerprint FROM mie_findings WHERE status='active'"
        )}
    count = mie.analyze()
    with db.connect() as conn:
        new_rows = conn.execute(
            """SELECT id,fingerprint,severity,summary,title_id,root_id,file_id
               FROM mie_findings WHERE status='active' ORDER BY id DESC"""
        ).fetchall()
    for finding in new_rows:
        if finding["fingerprint"] in before:
            continue
        record_event(
            "mie", f"New Library Health finding: {finding['summary']}",
            level="warning" if finding["severity"] in {"critical", "warning"} else "info",
            context={"finding_id": finding["id"]} | {
                key: finding[key] for key in ("title_id", "root_id", "file_id")
                if finding[key] is not None
            },
        )
    return count


def run_title_scan(title_id: int) -> None:
    before = _file_signatures(title_id=title_id)
    with title_scan_lock:
        title_scan_jobs[title_id] = {"status": "running", "files": 0, "label": "Series"}

    def report(files: int, _titles: int) -> None:
        with title_scan_lock:
            title_scan_jobs[title_id].update({"status": "running", "files": files})

    try:
        with db.connect() as conn:
            title = conn.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
            if not title:
                raise ValueError("Series no longer exists")
            with title_scan_lock:
                title_scan_jobs[title_id]["label"] = title["metadata_title"] or title["title"]
            result = scan_title(conn, title, report)
        with title_scan_lock:
            title_scan_jobs[title_id] = {
                "status": "complete", "label": title["metadata_title"] or title["title"],
                **result,
            }
        if result.get("source_status") == "degraded":
            record_event(
                "source-guard",
                f"Series rescan preserved {result['preserved']:,} catalog files because the source view was incomplete.",
                level="warning", context={"title_id": title_id, **result},
            )
        else:
            record_event(
                "scan",
                f"Series rescan finished for {title['metadata_title'] or title['title']}: {result['files']:,} files found.",
                context={"title_id": title_id, **result},
            )
        handle_import_hashing(
            _changed_file_ids(before, _file_signatures(title_id=title_id)),
            f"New or changed media found while rescanning {title['metadata_title'] or title['title']}",
        )
        try:
            analyze_library_health_with_activity()
        except sqlite3.Error as exc:
            record_event(
                "mie", "Library Health could not refresh after a series rescan.",
                level="error", detail=str(exc), context={"title_id": title_id},
            )
    except Exception as exc:
        if isinstance(exc, (SourceUnavailableError, OSError)) and 'title' in locals() and title:
            checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with db.connect() as conn:
                conn.execute(
                    """UPDATE roots SET health_status='offline',last_checked_at=?,
                       last_error=? WHERE id=?""",
                    (checked_at, str(exc)[:1000], title["root_id"]),
                )
        with title_scan_lock:
            title_scan_jobs[title_id] = {"status": "error", "error": str(exc)}
        record_event(
            "scan", "Series rescan could not finish.",
            level="error", detail=str(exc), context={"title_id": title_id},
        )
        if isinstance(exc, (SourceUnavailableError, OSError)):
            try:
                analyze_library_health_with_activity()
            except sqlite3.Error:
                pass


def run_imdb_genre_sync(
    title_ids: list[int] | None = None,
    episode_ids: list[int] | None = None,
    scope_label: str = "",
) -> None:
    title_scope = tuple(dict.fromkeys(title_ids or ()))
    with imdb_genre_lock:
        imdb_genre_job.clear()
        imdb_genre_job.update({
            "status": "running", "phase": "ids", "id_processed": 0,
            "id_total": 0, "id_found": 0, "id_missing": 0, "id_errors": 0,
            "records": 0, "matched": 0, "requested": 0,
            "scope_label": scope_label,
            "title_ids": list(title_scope) if title_ids is not None else None,
        })
    if title_scope:
        with db.connect() as conn:
            conn.execute(
                f"""UPDATE metadata_refresh_queue SET status='running',
                    started_at=CURRENT_TIMESTAMP, attempts=attempts+1, error=''
                    WHERE title_id IN ({','.join('?' for _ in title_scope)})""",
                title_scope,
            )

    with db.connect() as conn:
        scope_filter = ""
        scope_parameters: tuple[int, ...] = ()
        if title_ids is not None:
            scope_filter = (
                f" AND id IN ({','.join('?' for _ in title_scope)})"
                if title_scope else " AND 0"
            )
            scope_parameters = title_scope
        checked_filter = "" if title_ids is not None else " AND imdb_checked_at IS NULL"
        unmatched = conn.execute(
            f"""SELECT id, kind, tvdb_id, tvdb_movie_id FROM titles
               WHERE (imdb_id IS NULL OR imdb_id=''){checked_filter}
                 AND (tvdb_id IS NOT NULL OR tvdb_movie_id IS NOT NULL){scope_filter}
               ORDER BY kind, id""",
            scope_parameters,
        ).fetchall()
    id_total = len(unmatched)
    id_found = 0
    id_missing = 0
    id_errors = 0
    consecutive_errors = 0
    id_processed = 0
    for index, title in enumerate(unmatched, start=1):
        try:
            record = (
                tvdb.series(title["tvdb_id"])
                if title["kind"] == "tv"
                else tvdb.movie(title["tvdb_movie_id"])
            )
            _tmdb_id, imdb_id = plex_movie_ids(record)
            with db.connect() as conn:
                conn.execute(
                    """UPDATE titles SET imdb_id=COALESCE(NULLIF(?, ''), imdb_id),
                       imdb_checked_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (imdb_id, title["id"]),
                )
            id_found += bool(imdb_id)
            id_missing += not bool(imdb_id)
            consecutive_errors = 0
        except TVDBError:
            id_errors += 1
            consecutive_errors += 1
        id_processed = index
        with imdb_genre_lock:
            imdb_genre_job.update({
                "phase": "ids", "id_processed": id_processed,
                "id_total": id_total, "id_found": id_found,
                "id_missing": id_missing, "id_errors": id_errors,
            })
        if consecutive_errors >= 5:
            break
        if index % 20 == 0:
            time.sleep(1)

    def report(phase: str, records: int, matched: int, requested: int) -> None:
        with imdb_genre_lock:
            imdb_genre_job.update({
                "status": "running", "phase": phase, "records": records,
                "matched": matched, "requested": requested,
            })

    try:
        result = sync_genres(
            db, report, title_ids=title_ids, episode_ids=episode_ids,
        )
        with db.connect() as conn:
            pending = conn.execute(
                """SELECT COUNT(*) count FROM titles
                   WHERE (imdb_id IS NULL OR imdb_id='') AND imdb_checked_at IS NULL
                     AND (tvdb_id IS NOT NULL OR tvdb_movie_id IS NOT NULL)"""
            ).fetchone()["count"]
            if title_scope:
                placeholders = ",".join("?" for _ in title_scope)
                conn.execute(
                    f"""UPDATE titles SET metadata_refreshed_at=CURRENT_TIMESTAMP,
                        metadata_refresh_error='', metadata_provider='IMDb/TVDB'
                        WHERE id IN ({placeholders})""", title_scope,
                )
                conn.execute(
                    f"""UPDATE metadata_refresh_queue SET status='complete',
                        completed_at=CURRENT_TIMESTAMP, provider='IMDb/TVDB', error=''
                        WHERE title_id IN ({placeholders})""", title_scope,
                )
        with imdb_genre_lock:
            imdb_genre_job.clear()
            imdb_genre_job.update({
                "status": "complete", "phase": "complete",
                "id_processed": id_processed, "id_total": id_total,
                "id_found": id_found, "id_missing": id_missing,
                "id_errors": id_errors, "id_pending": pending, **result,
            })
        record_event(
            "metadata",
            (f"Metadata refresh finished for {len(title_scope):,} selected title(s)."
             if title_scope else "Catalog metadata refresh finished."),
            context=({"title_id": title_scope[0]} if len(title_scope) == 1 else
                     {"refreshed": len(title_scope), "category": "metadata"}),
        )
    except Exception as exc:
        if title_scope:
            with db.connect() as conn:
                placeholders = ",".join("?" for _ in title_scope)
                conn.execute(
                    f"UPDATE titles SET metadata_refresh_error=? WHERE id IN ({placeholders})",
                    (str(exc), *title_scope),
                )
                conn.execute(
                    f"""UPDATE metadata_refresh_queue SET status='failed',
                        completed_at=CURRENT_TIMESTAMP, error=?
                        WHERE title_id IN ({placeholders})""", (str(exc), *title_scope),
                )
        with imdb_genre_lock:
            imdb_genre_job.clear()
            imdb_genre_job.update({"status": "error", "error": str(exc)})
        record_event(
            "metadata", "Metadata refresh failed. It can be retried from Metadata Settings.",
            level="error", detail=str(exc),
            context=({"title_id": title_scope[0]} if len(title_scope) == 1 else
                     {"failed": len(title_scope), "category": "metadata"}),
        )


def start_scoped_imdb_sync(
    title_ids: list[int], episode_ids: list[int] | None, label: str,
) -> str | None:
    with imdb_genre_lock:
        if imdb_genre_job.get("status") in {"starting", "running"}:
            return "Another IMDb metadata update is already running"
        imdb_genre_job.clear()
        imdb_genre_job.update({
            "status": "starting", "scope_label": label,
            "title_ids": list(title_ids),
        })
    threading.Thread(
        target=run_imdb_genre_sync,
        args=(title_ids, episode_ids, label),
        daemon=True,
    ).start()
    return None


def queue_metadata_refresh(title_ids: list[int], user_id: int, label: str) -> str:
    ids = list(dict.fromkeys(int(value) for value in title_ids if int(value) > 0))[:1000]
    if not ids:
        return "No titles were selected for metadata refresh."
    with imdb_genre_lock:
        if imdb_genre_job.get("status") in {"starting", "running"}:
            return "Another metadata refresh is already running. Try again when it finishes."
    with db.connect() as conn:
        placeholders = ",".join("?" for _ in ids)
        valid = [row["id"] for row in conn.execute(
            f"SELECT id FROM titles WHERE id IN ({placeholders})", ids,
        )]
        conn.executemany(
            """INSERT INTO metadata_refresh_queue(title_id,status,requested_by,requested_at,error)
               VALUES (?,'queued',?,CURRENT_TIMESTAMP,'')
               ON CONFLICT(title_id) DO UPDATE SET status='queued',requested_by=excluded.requested_by,
               requested_at=CURRENT_TIMESTAMP,started_at=NULL,completed_at=NULL,error=''""",
            [(title_id, user_id if user_id > 0 else None) for title_id in valid],
        )
    error = start_scoped_imdb_sync(valid, None, label)
    return error or f"Metadata refresh queued for {len(valid):,} title(s)."


def dashboard_counts(user_id: int):
    with db.connect() as conn:
        return conn.execute(
            """SELECT
              (SELECT COUNT(*) FROM titles WHERE kind='movie') movies,
              (SELECT COUNT(*) FROM titles WHERE kind='tv') shows,
              (SELECT COUNT(*) FROM collections) collections,
              (SELECT COUNT(*) FROM user_title_state
                 WHERE user_id=? AND favorite=1)
              + (SELECT COUNT(*) FROM user_episode_favorites
                 WHERE user_id=?) favorites,
              (SELECT COALESCE(SUM(
                 CASE WHEN f.episode_start IS NOT NULL
                   THEN COALESCE(f.episode_end, f.episode_start) - f.episode_start + 1
                   ELSE 0 END
               ), 0)
               FROM files f JOIN titles t ON t.id=f.title_id WHERE t.kind='tv') episodes,
              (SELECT COALESCE(SUM(size_bytes),0) FROM files) bytes,
              (SELECT COUNT(*) FROM expected_episodes e
                 WHERE e.season > 0 AND (e.aired IS NULL OR e.aired <= date('now'))
                 AND NOT EXISTS (SELECT 1 FROM files f WHERE f.title_id=e.title_id
                   AND f.season=e.season AND e.episode BETWEEN f.episode_start AND f.episode_end)) missing,
              (SELECT COUNT(*) FROM titles t WHERE t.discovered_at IS NOT NULL AND
                 ((t.kind='tv' AND t.tvdb_id IS NULL) OR
                  (t.kind='movie' AND t.tvdb_movie_id IS NULL))) ready"""
        , (user_id, user_id)).fetchone()


def remediation_context(finding_id: int) -> dict | None:
    with db.connect() as conn:
        row = conn.execute(
            """SELECT mf.*,r.label root_label,r.path root_path,r.health_status,
                      r.last_file_count,r.last_observed_file_count,
                      r.guard_preserved_count,r.last_error
               FROM mie_findings mf LEFT JOIN roots r ON r.id=mf.root_id
               WHERE mf.id=? AND mf.status='active'""",
            (finding_id,),
        ).fetchone()
    if not row:
        return None
    finding = dict(row)
    try:
        finding["evidence"] = json.loads(finding["evidence_json"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        finding["evidence"] = {}
    actions = []
    if finding["rule_key"] in {"source-offline", "source-degraded"}:
        actions.append({
            "key": "check", "label": "Check connection", "confirm": "CHECK",
            "changes": "Reads the source root and updates only its availability status. No catalog or media files are removed.",
            "danger": False,
        })
    if finding["rule_key"] == "source-stale":
        actions.append({
            "key": "scan", "label": "Scan source", "confirm": "SCAN",
            "changes": "Starts a source scan. Source Guard blocks catalog cleanup if the source is offline, unreadable, unexpectedly empty, or sharply incomplete. Media files are never changed.",
            "danger": False,
        })
    if finding["rule_key"] == "source-degraded":
        actions.append({
            "key": "reconcile", "label": "Accept current source contents",
            "confirm": "RECONCILE",
            "changes": (
                f"Runs a complete scan and, only if no read errors occur, permits removal of up to "
                f"{int(finding['guard_preserved_count'] or 0):,} stale catalog file records. "
                "Files on disk are never deleted."
            ),
            "danger": True,
        })
    if finding["rule_key"] == "missing-episodes" and finding.get("title_id"):
        actions.append({
            "key": "rescan_title", "label": "Rescan this series",
            "confirm": "RESCAN",
            "changes": (
                "Reads this series folder and refreshes its catalog entries. Source Guard "
                "preserves existing records if the folder is unavailable, empty, or incomplete. "
                "Media files are never changed."
            ),
            "danger": False,
        })
    if finding["rule_key"] == "technical-details-missing":
        actions.append({
            "key": "inspect_source", "label": "Inspect missing media details",
            "confirm": "INSPECT",
            "changes": (
                f"Runs FFprobe against the {int(finding['evidence'].get('file_count') or 0):,} "
                "catalog files missing technical details. It updates catalog metadata only and "
                "does not alter media files."
            ),
            "danger": False,
        })
    if finding["rule_key"] == "media-unreadable" and finding.get("file_id"):
        actions.append({
            "key": "inspect_file", "label": "Reinspect this file",
            "confirm": "INSPECT",
            "changes": (
                "Runs FFprobe against this one cataloged file and replaces its stored inspection "
                "result. The media file is not altered."
            ),
            "danger": False,
        })
    return {"finding": finding, "actions": actions}


SETTINGS_SECTIONS = {"general", "metadata", "external-search", "system"}


def settings_page_context(
    request: Request, section: str, error: str = "",
    submitted: dict[str, str] | None = None,
) -> dict:
    preferences = app_settings.values()
    if submitted:
        preferences.update(submitted)
    context = {
        "section": section,
        "preferences": preferences,
        "error": error,
        "message": request.query_params.get("message", ""),
        "app_version": APP_VERSION,
    }
    if section == "general":
        context["timezone_groups"] = timezone_groups()
    if section == "metadata":
        with db.connect() as conn:
            context["metadata_counts"] = conn.execute(
                """SELECT
                   COUNT(*) total_titles,
                   SUM(CASE WHEN imdb_id IS NOT NULL AND imdb_id!='' THEN 1 ELSE 0 END) imdb_ids,
                   SUM(CASE WHEN imdb_rating IS NOT NULL THEN 1 ELSE 0 END) ratings,
                   MAX(imdb_checked_at) last_checked,
                   (SELECT COUNT(*) FROM title_credits) title_credits,
                   (SELECT COUNT(*) FROM episode_credits) episode_credits
                   FROM titles"""
            ).fetchone()
            context["metadata_freshness"] = conn.execute(
                """SELECT COUNT(*) total,
                   SUM(CASE WHEN metadata_refreshed_at >= datetime('now','-30 days') THEN 1 ELSE 0 END) fresh,
                   SUM(CASE WHEN metadata_refreshed_at IS NULL OR metadata_refreshed_at < datetime('now','-30 days') THEN 1 ELSE 0 END) stale,
                   SUM(CASE WHEN COALESCE(poster_url,'')='' THEN 1 ELSE 0 END) artwork_missing,
                   SUM(CASE WHEN COALESCE(imdb_id,'')='' AND tvdb_id IS NULL AND tvdb_movie_id IS NULL AND COALESCE(tmdb_id,'')='' THEN 1 ELSE 0 END) identifiers_missing,
                   SUM(CASE WHEN NOT EXISTS (SELECT 1 FROM title_credits tc WHERE tc.title_id=titles.id) THEN 1 ELSE 0 END) credits_missing
                   FROM titles"""
            ).fetchone()
            context["metadata_queue"] = conn.execute(
                """SELECT q.*,COALESCE(t.metadata_title,t.title) display_title
                   FROM metadata_refresh_queue q JOIN titles t ON t.id=q.title_id
                   ORDER BY CASE q.status WHEN 'failed' THEN 0 WHEN 'running' THEN 1
                            WHEN 'queued' THEN 2 ELSE 3 END,q.requested_at DESC LIMIT 25"""
            ).fetchall()
        with imdb_genre_lock:
            context["imdb_job"] = dict(imdb_genre_job)
        context["tvdb_status"] = {
            "configured": bool(tvdb.api_key),
            "key_hint": (
                f"Configured · ends in {tvdb.api_key[-4:]}"
                if tvdb.api_key else "Not configured"
            ),
            "pin_configured": bool(tvdb.pin),
        }
    elif section == "external-search":
        context["test_search_url"] = preferences["search_url_template"].replace(
            "{query}", quote_plus("House of the Dragon S01E01")
        )
    elif section == "system":
        with db.connect() as conn:
            counts = conn.execute(
                """SELECT
                 (SELECT COUNT(*) FROM titles) titles,
                 (SELECT COUNT(*) FROM files) files,
                 (SELECT COUNT(*) FROM roots) roots,
                 (SELECT COUNT(*) FROM expected_episodes) expected_episodes,
                 (SELECT COUNT(*) FROM users WHERE active=1) active_users"""
            ).fetchone()
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
            page_count = conn.execute("PRAGMA page_count").fetchone()[0]
            free_pages = conn.execute("PRAGMA freelist_count").fetchone()[0]
            sqlite_version = conn.execute("SELECT sqlite_version()").fetchone()[0]
            media_counts = conn.execute(
                """SELECT COUNT(*) total,
                   SUM(CASE WHEN media_info_at IS NOT NULL THEN 1 ELSE 0 END) inspected,
                   SUM(CASE WHEN media_info_error IS NOT NULL AND media_info_error!='' THEN 1 ELSE 0 END) failed
                   FROM files"""
            ).fetchone()
        wal_path = Path(f"{db.path}-wal")
        context.update({
            "system_counts": counts,
            "database_stats": {
                "size": page_size * page_count,
                "wal_size": wal_path.stat().st_size if wal_path.exists() else 0,
                "free_size": page_size * free_pages,
            },
            "sqlite_version": sqlite_version,
            "database_path": str(db.path),
            "browse_roots": settings.media_browse_roots,
            "settings_session_days": settings.session_days,
            "cookie_secure_setting": settings.cookie_secure,
            "cloudflare_configured": bool(
                settings.cloudflare_team_domain and settings.cloudflare_audience
            ),
            "setting_history": app_settings.history(),
            "media_counts": media_counts,
            "hash_counts": media_hashes.counts(),
            "hash_job": dict(media_hash_job),
            "log_categories": event_log.categories(),
            "database_backups": list_database_backups(db.path),
            "update_status": read_update_status(db.path),
            "update_repository": os.getenv(
                "INFOMANCER_UPDATE_REPOSITORY", "chandler-sol/InfoMancer"
            ).strip(),
        })
    return context


def render_settings(
    request: Request, section: str, error: str = "",
    submitted: dict[str, str] | None = None, status_code: int = 200,
):
    return templates.TemplateResponse(
        request, "settings.html",
        settings_page_context(request, section, error, submitted),
        status_code=status_code,
    )


def csv_safe_row(row) -> dict:
    safe = {}
    for key, value in dict(row).items():
        if isinstance(value, str) and value.lstrip(" \t\r\n")[:1] in {"=", "+", "-", "@"}:
            value = "'" + value
        safe[key] = value
    return safe


LIBRARY_EXPORT_FIELDS = [
    "title_id", "kind", "title", "release_year", "end_year", "continuing",
    "tvdb_id", "tvdb_movie_id", "tmdb_id", "imdb_id", "imdb_rating",
    "imdb_votes", "imdb_title_type", "genres", "date_added", "source",
    "source_path", "file_id", "file_path", "filename", "size_bytes",
    "season", "episode_start", "episode_end", "runtime_seconds", "width",
    "height", "video_codec", "audio_codec", "audio_channels", "bitrate",
    "container", "dynamic_range", "media_info_at", "media_info_error",
    "edition_name", "version_name", "identity_confirmed", "version_preferred",
    "tags", "collections", "custom_fields",
]


def library_export_rows(user_id: int) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT t.id title_id, t.kind, COALESCE(t.metadata_title,t.title) title,
               COALESCE(t.metadata_year,t.year) release_year,
               COALESCE(t.metadata_end_year,t.end_year) end_year,
               COALESCE(t.metadata_continuing,t.continuing) continuing,
               t.tvdb_id, t.tvdb_movie_id, t.tmdb_id, t.imdb_id,
               t.imdb_rating, t.imdb_votes, t.imdb_title_type, t.genres,
               t.discovered_at date_added, r.label source, r.path source_path,
               f.id file_id, f.path file_path, f.filename, f.size_bytes,
               f.season, f.episode_start, f.episode_end,
               f.runtime_seconds, f.width, f.height, f.video_codec,
               f.audio_codec, f.audio_channels, f.bitrate, f.container,
               f.dynamic_range, f.media_info_at, f.media_info_error,
               f.edition_name, f.version_name, f.identity_confirmed,
               f.version_preferred,
               COALESCE(uts.favorite,0) favorite, uts.personal_rating,
               uts.custom_order, uts.sort_title,
               COALESCE((SELECT GROUP_CONCAT(ut.name, ', ')
                 FROM title_tags tt JOIN user_tags ut ON ut.id=tt.tag_id
                 WHERE tt.title_id=t.id AND ut.user_id=?),'') tags,
               COALESCE((SELECT GROUP_CONCAT(c.name, ', ')
                 FROM collection_titles ct JOIN collections c ON c.id=ct.collection_id
                 WHERE ct.title_id=t.id),'') collections
               FROM titles t JOIN roots r ON r.id=t.root_id
               LEFT JOIN files f ON f.title_id=t.id
               LEFT JOIN user_title_state uts
                 ON uts.title_id=t.id AND uts.user_id=?
               ORDER BY t.kind, title COLLATE NOCASE, f.season,
                        f.episode_start, f.filename COLLATE NOCASE""",
            (user_id, user_id),
        ).fetchall()
    exported = []
    for row in rows:
        item = dict(row)
        item["custom_fields"] = json.dumps({
            "favorite": bool(item.pop("favorite")),
            "personal_rating": item.pop("personal_rating"),
            "custom_order": item.pop("custom_order"),
            "sort_title": item.pop("sort_title"),
        }, ensure_ascii=False)
        exported.append(item)
    return exported


def restart_after_restore() -> None:
    time.sleep(2.0)
    os._exit(0)


def release_version_key(value: str) -> tuple:
    parts = re.split(r"[.+-]", value.lstrip("vV"))
    numbers = tuple(int(part) if part.isdigit() else -1 for part in parts[:3])
    return numbers + (0 if "-" in value else 1,)


def title_return_path(title_id: int, return_to: str = "") -> str:
    parsed = urlparse(return_to)
    collection_return = (
        parsed.path.startswith("/collections/")
        and parsed.path.removeprefix("/collections/").isdigit()
    )
    if (
        return_to and not parsed.scheme and not parsed.netloc
        and (
            parsed.path in {
                "/", "/library", "/movies", "/shows", "/favorites",
                f"/titles/{title_id}",
            }
            or collection_return
        )
    ):
        return return_to
    return f"/titles/{title_id}"


def favorite_return_path(file_row) -> str:
    return f"/titles/{file_row['title_id']}#season-{file_row['season']}"


def collection_artwork_url(row) -> str:
    if row["artwork_filename"]:
        return f"/collections/art/{row['artwork_filename']}"
    return row["fallback_poster"] or ""


def normalize_collection_positions(conn, collection_id: int) -> None:
    rows = conn.execute(
        """SELECT 'title' item_type,title_id item_id,position
           FROM collection_titles WHERE collection_id=?
           UNION ALL
           SELECT 'episode',expected_episode_id,position
           FROM collection_episodes WHERE collection_id=?
           ORDER BY position,item_type,item_id""",
        (collection_id, collection_id),
    ).fetchall()
    for position, row in enumerate(rows):
        table = "collection_titles" if row["item_type"] == "title" else "collection_episodes"
        column = "title_id" if row["item_type"] == "title" else "expected_episode_id"
        conn.execute(
            f"UPDATE {table} SET position=? WHERE collection_id=? AND {column}=?",
            (position, collection_id, row["item_id"]),
        )


def next_collection_position(conn, collection_id: int) -> int:
    row = conn.execute(
        """SELECT COALESCE(MAX(position),-1)+1 next_position FROM (
             SELECT position FROM collection_titles WHERE collection_id=?
             UNION ALL
             SELECT position FROM collection_episodes WHERE collection_id=?
           )""",
        (collection_id, collection_id),
    ).fetchone()
    return row["next_position"]


def collection_items(conn, collection_id: int, user_id: int = 0):
    return conn.execute(
        """SELECT 'title' item_type,t.id item_id,t.id title_id,
                  COALESCE(NULLIF(t.metadata_title,''),t.title) display_title,
                  CASE WHEN t.kind='tv' THEN 'TV series' ELSE 'Movie' END item_label,
                  COALESCE(t.metadata_year,t.year) display_year,
                  t.poster_url,NULL season,NULL episode,ct.position,t.kind,
                  t.tvdb_id,t.tvdb_movie_id,t.tmdb_id,t.imdb_id,
                  COALESCE((SELECT uts.favorite FROM user_title_state uts
                            WHERE uts.user_id=? AND uts.title_id=t.id),0) favorite
           FROM collection_titles ct JOIN titles t ON t.id=ct.title_id
           WHERE ct.collection_id=?
           UNION ALL
           SELECT 'episode',e.id,t.id,
                  COALESCE(NULLIF(e.name,''),
                    printf('S%02dE%02d',e.season,e.episode)),
                  COALESCE(NULLIF(t.metadata_title,''),t.title),
                  COALESCE(t.metadata_year,t.year),t.poster_url,
                  e.season,e.episode,ce.position,t.kind,
                  t.tvdb_id,t.tvdb_movie_id,t.tmdb_id,t.imdb_id,
                  COALESCE((SELECT uts.favorite FROM user_title_state uts
                            WHERE uts.user_id=? AND uts.title_id=t.id),0)
           FROM collection_episodes ce
           JOIN expected_episodes e ON e.id=ce.expected_episode_id
           JOIN titles t ON t.id=e.title_id
           WHERE ce.collection_id=?
           ORDER BY position,item_type,item_id""",
        (user_id, collection_id, user_id, collection_id),
    ).fetchall()


async def save_collection_artwork(upload: UploadFile | None) -> str:
    if not upload or not upload.filename:
        return ""
    content = await upload.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise ValueError(
            "The collection image is larger than 5 MB. Choose a smaller JPEG, PNG, or WebP image."
        )
    signatures = (
        (b"\xff\xd8\xff", ".jpg"),
        (b"\x89PNG\r\n\x1a\n", ".png"),
    )
    extension = next(
        (extension for signature, extension in signatures if content.startswith(signature)),
        "",
    )
    if not extension and len(content) >= 12 \
            and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        extension = ".webp"
    if not extension:
        raise ValueError(
            "InfoMancer could not recognize that image. Choose a JPEG, PNG, or WebP file."
        )
    filename = f"{secrets.token_hex(20)}{extension}"
    (COLLECTION_ART_DIR / filename).write_bytes(content)
    return filename


def smart_filter_form(form) -> dict[str, str]:
    return normalize_filters({key: form.get(key, "") for key in (
        "genre", "year_from", "year_to", "resolution", "quality", "root_id",
        "favorite", "missing_episodes", "health_category",
    )})


def move_collection_item(
    collection_id: int, item_type: str, item_id: int, direction: str,
):
    if direction not in {"up", "down"}:
        return redirect(
            f"/collections/{collection_id}",
            "The collection item was not moved because the requested direction was not recognized.",
        )
    with db.connect() as conn:
        normalize_collection_positions(conn, collection_id)
        items = collection_items(conn, collection_id)
        current_index = next(
            (
                index for index, item in enumerate(items)
                if item["item_type"] == item_type and item["item_id"] == item_id
            ),
            None,
        )
        current = items[current_index] if current_index is not None else None
        if not current:
            return redirect(
                f"/collections/{collection_id}",
                "That item could not be moved because it is no longer in this collection.",
            )
        target_index = current_index + (-1 if direction == "up" else 1)
        if 0 <= target_index < len(items):
            reordered = list(items)
            reordered[current_index], reordered[target_index] = (
                reordered[target_index], reordered[current_index]
            )
            for position, item in enumerate(reordered):
                table = (
                    "collection_titles"
                    if item["item_type"] == "title" else "collection_episodes"
                )
                column = (
                    "title_id"
                    if item["item_type"] == "title" else "expected_episode_id"
                )
                conn.execute(
                    f"UPDATE {table} SET position=? WHERE collection_id=? AND {column}=?",
                    (position, collection_id, item["item_id"]),
                )
    return redirect(f"/collections/{collection_id}#{item_type}-{item_id}")


def edition_version_context(file_id: int) -> dict:
    file = edition_versions.file(file_id)
    if not file:
        raise HTTPException(404, "File not found")
    return {
        "file": file,
        "current": {
            "edition_name": file["edition_name"],
            "version_name": file["version_name"],
            "preferred": bool(file["version_preferred"]),
        },
        "return_to": f"/titles/{file['title_id']}",
        "message": "",
    }


def is_tvdb_series_reference(reference: str) -> bool:
    value = reference.strip()
    if not value:
        return False
    candidate_url = value if "://" in value else f"https://{value}"
    parsed = urlparse(candidate_url)
    return (parsed.hostname or "").casefold() in {"thetvdb.com", "www.thetvdb.com"}


def tvdb_series_id_from_reference(reference: str) -> int:
    value = reference.strip()
    if not value:
        raise ValueError("Paste a TVDB series link or numeric series ID")
    if value.isdigit():
        return int(value)

    candidate_url = value if "://" in value else f"https://{value}"
    parsed = urlparse(candidate_url)
    hostname = (parsed.hostname or "").lower()
    if hostname not in {"thetvdb.com", "www.thetvdb.com"}:
        raise ValueError("Use a link from thetvdb.com or enter the numeric TVDB series ID")
    parts = [unquote(part).strip() for part in parsed.path.split("/") if part.strip()]
    lowered = [part.lower() for part in parts]
    if "series" not in lowered:
        raise ValueError("That TVDB link is not a series page")
    series_index = lowered.index("series")
    if series_index + 1 >= len(parts):
        raise ValueError("The TVDB series link is incomplete")
    identifier = parts[series_index + 1]
    if "dereferrer" in lowered and identifier.isdigit():
        return int(identifier)

    query = re.sub(r"[-_]+", " ", identifier).strip()
    results = tvdb.search_series(query)
    normalized_slug = identifier.strip("/").casefold()
    exact = [
        result for result in results
        if str(result.get("slug") or "").strip("/").casefold() == normalized_slug
    ]
    choices = exact or (results if len(results) == 1 else [])
    if not choices:
        raise ValueError(
            "That TVDB page could not be resolved uniquely. Paste its numeric series ID instead."
        )
    raw_id = str(choices[0].get("tvdb_id") or choices[0].get("id") or "")
    id_match = re.search(r"(\d+)$", raw_id)
    if not id_match:
        raise ValueError("TVDB did not return a valid numeric series ID for that link")
    return int(id_match.group(1))


def store_tv_match(title_id: int, series_id: int) -> int:
    series = tvdb.series(series_id)
    episodes = tvdb.episodes(series_id)
    year_value = str(series.get("firstAired") or "")[:4]
    metadata_year = int(year_value) if year_value.isdigit() else None
    status_value = series.get("status") or ""
    metadata_status = (
        status_value.get("name", "") if isinstance(status_value, dict) else str(status_value)
    ).strip()
    normalized_status = metadata_status.lower()
    if any(word in normalized_status for word in ("continuing", "returning", "in production")):
        metadata_continuing = True
    elif any(word in normalized_status for word in ("ended", "cancelled", "canceled")):
        metadata_continuing = False
    else:
        metadata_continuing = None

    today = date.today().isoformat()
    aired_years = []
    for episode in episodes:
        aired = str(episode.get("aired") or "")
        if (episode.get("seasonNumber") or 0) > 0 and len(aired) >= 4 and aired <= today:
            aired_years.append(int(aired[:4]))
    metadata_end_year = (
        max(aired_years) if metadata_continuing is False and aired_years else None
    )
    _tmdb_id, imdb_id = plex_movie_ids(series)
    with db.connect() as conn:
        title = conn.execute(
            "SELECT id, metadata_title FROM titles WHERE id=?", (title_id,)
        ).fetchone()
        if not title:
            raise HTTPException(404, "Title not found")
        metadata_title, title_language = localized_tvdb_title(
            series, title["metadata_title"]
        )
        conn.execute(
            """UPDATE titles SET tvdb_id=?, metadata_title=?,
               metadata_title_language=?, metadata_year=?,
               metadata_end_year=?, metadata_continuing=?, metadata_status=?,
               overview=?, poster_url=?, imdb_id=?, imdb_checked_at=CURRENT_TIMESTAMP,
               matched_at=CURRENT_TIMESTAMP,
               updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (series_id, metadata_title, title_language or None, metadata_year,
             metadata_end_year, metadata_continuing, metadata_status,
             str(series.get("overview") or "").strip(), poster_from(series), imdb_id,
             title_id),
        )
        conn.execute("DELETE FROM expected_episodes WHERE title_id=?", (title_id,))
        conn.execute("DELETE FROM tv_match_suggestions WHERE title_id=?", (title_id,))
        for episode in episodes:
            season = episode.get("seasonNumber")
            number = episode.get("number")
            episode_id = episode.get("id")
            if season is None or number is None or episode_id is None:
                continue
            conn.execute(
                """INSERT OR REPLACE INTO expected_episodes
                   (title_id, tvdb_episode_id, season, episode, name, aired)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (title_id, episode_id, season, number, episode.get("name") or "",
                 episode.get("aired")),
            )
    return len(episodes)


def episode_rename_proposals(conn: sqlite3.Connection, title_id: int):
    title = conn.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
    if not title or title["kind"] != "tv" or not title["tvdb_id"]:
        return title, []
    rows = conn.execute(
        """SELECT f.* FROM files f
           WHERE f.title_id=? AND f.season IS NOT NULL AND f.episode_start IS NOT NULL
           ORDER BY f.season, f.episode_start, f.filename""", (title_id,)
    ).fetchall()
    episode_names = expected_name_map(conn, title_id)
    proposals = []
    destinations: set[str] = set()
    for row in rows:
        new_name = plex_episode_filename(
            title["metadata_title"] or title["title"],
            title["metadata_year"] or title["year"], row["season"],
            row["episode_start"], merged_episode_name(
                episode_names, row["season"], row["episode_start"], row["episode_end"]
            ), row["extension"],
            row["episode_end"],
        )
        source = Path(row["path"])
        destination = contained_destination(source, new_name)
        destination_key = str(destination).casefold()
        if destination == source:
            status = "unchanged"
        elif not source.exists():
            status = "missing"
        elif destination.exists() or destination_key in destinations:
            status = "conflict"
        else:
            status = "ready"
            destinations.add(destination_key)
        proposals.append({
            "file_id": row["id"], "source": source, "destination": destination,
            "old_name": source.name, "new_name": destination.name, "status": status,
            "new_name_parts": changed_name_parts(source.name, destination.name),
            "season": row["season"], "episode": row["episode_start"],
        })
    return title, proposals


def restore_filename_proposals(conn: sqlite3.Connection, title_id: int):
    title = conn.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
    if not title:
        return None, []
    rows = conn.execute(
        """SELECT * FROM files WHERE title_id=? AND original_filename IS NOT NULL
           ORDER BY season, episode_start, filename""",
        (title_id,),
    ).fetchall()
    proposals = []
    destinations: set[str] = set()
    for row in rows:
        source = Path(row["path"])
        destination = contained_destination(source, Path(row["original_filename"]).name)
        destination_key = str(destination).casefold()
        if destination == source:
            status = "unchanged"
        elif not source.exists():
            status = "missing"
        elif destination.exists() or destination_key in destinations:
            status = "conflict"
        else:
            status = "ready"
            destinations.add(destination_key)
        proposals.append({
            "file_id": row["id"], "source": source, "destination": destination,
            "old_name": source.name, "new_name": destination.name, "status": status,
            "season": row["season"], "episode": row["episode_start"],
            "new_name_parts": changed_name_parts(source.name, destination.name),
        })
    return title, proposals

# Domain routes are assembled after helpers/services are defined. Authentication,
# bootstrap, middleware, lifecycle, and admin-account wiring intentionally remain
# in this composition root during W1.5.
_route_context = RouteContext(globals())
for _build_route_bundle in ROUTER_BUILDERS:
    _domain_router, _domain_handlers = _build_route_bundle(_route_context)
    app.include_router(_domain_router)
    # Preserve app.main.<handler> compatibility for existing tests/internal callers
    # while the source of truth lives in app.routes.*.
    globals().update(_domain_handlers)


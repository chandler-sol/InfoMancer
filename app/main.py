from __future__ import annotations

import os
import hmac
import csv
import io
import json
import re
import secrets
import sqlite3
import threading
import time
from xml.etree import ElementTree
from difflib import SequenceMatcher
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlencode, urlparse
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import BASE_DIR, get_settings
from .app_settings import AppSettingError, AppSettings
from .auth import (
    AuthService, AuthSession, AuthUser, AuthenticationError, LoginLocked,
    PREAUTH_COOKIE, PROFILE_ICONS, SESSION_COOKIE, request_ip, safe_next,
    secure_cookie_for,
)
from .db import Database
from .engagement import EngagementError, EngagementService, utc_from_local
from .event_log import EventLog
from .imdb import sync_genres
from .media_info import MediaInspectionError, inspect_media
from .naming import (
    contained_destination, plex_episode_filename, plex_movie_filename, plex_show_folder,
)
from .scanner import scan_root, scan_title
from .source_browser import SourceBrowserError, list_folders, preview_folder
from .tvdb import TVDBClient, TVDBError
from .provider_secrets import ProviderSecretError, ProviderSecretStore
from .timezones import timezone_groups


settings = get_settings()
db = Database(settings.database)
db.initialize()
auth_service = AuthService(db, settings)
app_settings = AppSettings(db, settings.search_url_template)
engagement = EngagementService(db)
event_log = EventLog(db)
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
APP_VERSION = "0.3.0-alpha.1"
app = FastAPI(title="InfoMancer", version=APP_VERSION)
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")


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
        "csrf_token": getattr(getattr(request.state, "auth_session", None), "csrf_token", ""),
        "auth_mode": settings.auth_mode,
        "sandbox_mode": settings.sandbox,
        "minimum_password_length": settings.minimum_password_length,
        "app_version": APP_VERSION,
        "app_name": preferences["installation_name"],
        "default_library_view": preferences["default_library_view"],
        "default_cover_size": int(preferences["default_cover_size"]),
        "search_provider_name": preferences["search_provider_name"],
        "show_onboarding_tour": show_tour,
        "setup_choice_pending": setup_choice_pending,
        "show_setup_choice": show_setup_choice,
        "next_announcement": next_announcement,
        "announcement_due_count": announcement_due_count,
    }


templates = Jinja2Templates(
    directory=BASE_DIR / "app" / "templates",
    context_processors=[shared_template_context],
)
tvdb = TVDBClient(
    stored_provider_secrets.get("tvdb_api_key", settings.tvdb_api_key),
    stored_provider_secrets.get("tvdb_pin", settings.tvdb_pin),
)
scan_jobs: dict[int, dict] = {}
scan_lock = threading.Lock()
scan_all_job: dict = {"status": "idle"}
scan_all_lock = threading.Lock()
title_scan_jobs: dict[int, dict] = {}
title_scan_lock = threading.Lock()
imdb_genre_job: dict = {"status": "idle"}
imdb_genre_lock = threading.Lock()
movie_match_job: dict = {"status": "idle"}
movie_match_lock = threading.Lock()
tv_match_job: dict = {"status": "idle"}
tv_match_lock = threading.Lock()
media_info_job: dict = {"status": "idle"}
media_info_lock = threading.Lock()


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


PUBLIC_PATHS = {"/health", "/login", "/setup"}
LIBRARIAN_GET_PREFIXES = (
    "/sources", "/intake", "/bulk-match", "/movies/bulk-match",
    "/shows/bulk-match", "/admin", "/api/source-", "/api/scans",
    "/api/scan-all", "/api/movie-match-analysis", "/api/tv-match-analysis",
    "/api/logs",
    "/settings", "/getting-started",
    "/logs", "/exports", "/media-info/failures",
)


def librarian_only_path(path: str) -> bool:
    if path.startswith(LIBRARIAN_GET_PREFIXES):
        return True
    return bool(re.match(
        r"^/(?:titles/\d+/(?:tvdb|rename|restore|cover)|files/\d+/rename)", path
    ))


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

    async def finish(response):
        if new_session_token:
            set_session_cookie(response, request, new_session_token)
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

    if path.startswith("/static/") or path == "/health":
        return await finish(await call_next(request))

    if settings.auth_mode == "disabled":
        request.state.user = AuthUser(
            id=0, username="local", email="", display_name="Local Librarian",
            profile_icon="library", role="librarian", active=True,
            force_password_change=False, last_login_at="",
        )
        return await finish(await call_next(request))

    users_exist = auth_service.user_count() > 0

    if settings.auth_mode == "cloudflare":
        assertion = request.headers.get("cf-access-jwt-assertion", "")
        try:
            claims = auth_service.cloudflare_claims(assertion)
        except AuthenticationError as exc:
            return await finish(auth_error_response(
                request, 401, "Authentication required", str(exc)
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
            if not existing or existing.user.id != user.id:
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
    if user and request.method == "GET" and librarian_only_path(path) and not user.is_librarian:
        return await finish(auth_error_response(
            request, 403, "Librarian access required",
            "Your Member account can browse the library, but this operation requires a Librarian.",
        ))
    if user and request.method not in {"GET", "HEAD", "OPTIONS"}:
        if path not in {"/login", "/setup"}:
            if path not in {
                "/logout", "/account/profile", "/account/security",
                "/account/sessions/revoke-others",
            } and not re.match(r"^/titles/\d+/(?:favorite|organize)$", path) \
              and path not in {
                  "/titles/organize-bulk", "/tags/create",
              } and not re.match(r"^/tags/\d+/(?:rename|delete)$", path) \
              and not path.startswith("/account/sessions/") \
              and not path.startswith("/engagement/") and not user.is_librarian:
                return await finish(auth_error_response(
                    request, 403, "Librarian access required",
                    "Your Member account cannot make administrative or filesystem changes.",
                ))
            if not session:
                return await finish(auth_error_response(
                    request, 403, "Session required", "Start a fresh session and try again."
                ))
            body = await request.body()
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
    name = (row["metadata_title"] or row["title"] or "").strip()
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
    original = re.sub(r"\s+", " ", title).strip()
    variants: list[str] = []
    cleaned = re.sub(r"\s*\{(?:tvdb|tmdb|imdb)-[^}]+\}\s*", " ", original, flags=re.I)
    cleaned = re.sub(
        r"\s*[([]\s*(?:19|20)\d{2}(?:\s*-\s*(?:(?:19|20)\d{2}|present))?\s*[)\]]\s*$",
        "", cleaned, flags=re.I,
    ).strip(" -:.")
    if cleaned and cleaned.casefold() != original.casefold():
        variants.append(cleaned)

    variant_base = cleaned or original
    if re.search(r"\band\b", variant_base, flags=re.I):
        variants.append(re.sub(r"\band\b", "&", variant_base, flags=re.I))
    if "&" in variant_base:
        variants.append(variant_base.replace("&", "and"))

    punctuation_cleaned = re.sub(r"[^\w\s&]", " ", variant_base, flags=re.UNICODE)
    punctuation_cleaned = re.sub(r"\s+", " ", punctuation_cleaned).strip()
    if punctuation_cleaned and punctuation_cleaned.casefold() != variant_base.casefold():
        variants.append(punctuation_cleaned)

    subtitle = re.split(r"\s*[:–—]\s*", variant_base, maxsplit=1)[0].strip()
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
               matched_at=CURRENT_TIMESTAMP,
               imdb_checked_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (movie_id, tmdb_id, imdb_id, poster_from(movie), metadata_title,
             title_language or None, metadata_year, title_id),
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
    separator = "&" if "?" in path else "?"
    target = path + (f"{separator}message={quote_plus(message)}" if message else "")
    return RedirectResponse(target, status_code=303)


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
    return bool(stored and submitted and hmac.compare_digest(stored, submitted))


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
        return redirect("/login" if settings.auth_mode == "local" else "/")
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
    preauth_token: str = Form(""),
):
    if auth_service.user_count():
        return redirect("/login")
    if not valid_preauth(request, preauth_token):
        return redirect("/setup", "Setup form expired. Please try again.")
    if settings.auth_mode == "local" and password != password_confirm:
        return preauth_response(request, "setup.html", {
            "username": username, "email": email, "display_name": display_name,
            "requires_password": True, "error": "Passwords do not match.",
        })
    try:
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
        return preauth_response(request, "setup.html", {
            "username": username, "email": email, "display_name": display_name,
            "requires_password": settings.auth_mode == "local", "error": str(exc),
        })
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
    try:
        user = auth_service.authenticate_local(identity, password, request_ip(request))
    except AuthenticationError as exc:
        return preauth_response(request, "login.html", {
            "next": safe_next(next), "identity": identity, "error": str(exc),
        })
    return signed_in_response(request, user, next)


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
    profile_icon: str = Form("initials"),
):
    try:
        auth_service.update_profile(request.state.user.id, display_name, email, profile_icon)
    except AuthenticationError as exc:
        return templates.TemplateResponse(request, "account_profile.html", {
            "profile_icons": PROFILE_ICONS, "message": "", "error": str(exc),
        }, status_code=400)
    return redirect("/account/profile", "Profile saved")


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


@app.get("/getting-started", response_class=HTMLResponse)
def getting_started(request: Request):
    if request.state.user.id <= 0:
        return redirect("/sources", "Setup Assistant requires local or external user accounts.")
    state = engagement.setup_state(request.state.user.id)
    step = state["current_step"] if state and not state["completed_at"] else "general"
    return redirect(f"/getting-started/{step}")


@app.post("/getting-started/choice")
def choose_getting_started(request: Request, mode: str = Form(...)):
    if request.state.user.id <= 0:
        return redirect("/sources", "Setup Assistant requires local or external user accounts.")
    if mode not in {"guided", "manual"}:
        return redirect("/", "Choose Guided setup or Set up manually.")
    engagement.begin_setup(request.state.user.id, mode)
    if mode == "manual":
        return redirect("/", "Manual setup selected. Add a source whenever you are ready.")
    return redirect("/getting-started/general")


@app.post("/getting-started/restart")
def restart_getting_started(request: Request):
    if request.state.user.id <= 0:
        return redirect("/sources", "Setup Assistant requires local or external user accounts.")
    engagement.begin_setup(request.state.user.id, "guided")
    return redirect("/getting-started/general")


@app.get("/getting-started/{step}", response_class=HTMLResponse)
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


@app.post("/getting-started/general")
def save_getting_started_general(
    request: Request, installation_name: str = Form(...),
    timezone_name: str = Form(...),
):
    if request.state.user.id <= 0:
        return redirect("/sources", "Setup Assistant requires local or external user accounts.")
    try:
        values = app_settings.validate_general(
            installation_name, timezone_name, "list",
            app_settings.get("default_cover_size"),
        )
        app_settings.update(values, request.state.user.id)
    except AppSettingError as exc:
        context = setup_assistant_context(request, "general", str(exc))
        context["preferences"].update({
            "installation_name": installation_name, "timezone": timezone_name,
        })
        return templates.TemplateResponse(
            request, "getting_started.html", context, status_code=400
        )
    engagement.set_setup_step(request.state.user.id, "metadata")
    return redirect("/getting-started/metadata", "Installation preferences saved.")


@app.post("/getting-started/metadata")
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


@app.post("/getting-started/sources")
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


@app.post("/getting-started/complete")
def complete_getting_started(request: Request):
    if request.state.user.id <= 0:
        return redirect("/sources", "Setup Assistant requires local or external user accounts.")
    engagement.complete_setup(request.state.user.id)
    return redirect("/", "Setup Assistant completed. Your library is ready.")


@app.post("/engagement/announcements/{announcement_id}/seen")
def mark_announcement_seen(request: Request, announcement_id: int):
    try:
        engagement.mark_seen(announcement_id, request.state.user.id)
    except EngagementError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=404)
    return JSONResponse({"saved": True})


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


@app.get("/announcements", response_class=HTMLResponse)
def announcements_page(request: Request):
    user = request.state.user
    rows = engagement.list_for_user(user.id, user.role)
    for row in rows:
        if row["due_now"]:
            engagement.mark_seen(row["id"], user.id)
    return templates.TemplateResponse(
        request, "announcements.html", announcement_page_context(request)
    )


@app.get("/help", response_class=HTMLResponse)
def help_page(request: Request):
    return templates.TemplateResponse(request, "help.html", {
        "message": request.query_params.get("message", ""),
    })


@app.get("/about", response_class=HTMLResponse)
def about_page(request: Request):
    return templates.TemplateResponse(request, "about.html", {
        "message": request.query_params.get("message", ""),
    })


@app.post("/admin/announcements")
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


@app.post("/admin/announcements/{announcement_id}/deactivate")
def deactivate_announcement(announcement_id: int):
    try:
        engagement.deactivate(announcement_id)
    except EngagementError as exc:
        return redirect("/announcements", str(exc))
    return redirect(
        "/announcements",
        "Announcement ended. It will no longer appear as a popup.",
    )


@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request):
    return templates.TemplateResponse(
        request, "admin_users.html", user_admin_context(request)
    )


@app.post("/admin/users")
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
            invitation_url = str(request.url_for("activate_page", token=raw_token))
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


@app.post("/admin/users/{user_id}/invitation")
def create_user_invitation(request: Request, user_id: int):
    try:
        raw_token, expires = auth_service.create_invitation(
            user_id, request.state.user.id
        )
        user = auth_service.get_user(user_id)
        invitation_url = str(request.url_for("activate_page", token=raw_token))
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


@app.post("/admin/users/{user_id}/invitation/revoke")
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


@app.post("/admin/users/{user_id}")
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


@app.post("/admin/users/{user_id}/sessions/revoke")
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


def run_scan(root_id: int) -> None:
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
            result = scan_root(conn, root, report_progress)
        with scan_lock:
            scan_jobs[root_id] = {"status": "complete", **result}
        record_event(
            "scan",
            f"Source scan finished: {result['files']:,} video files across {result['titles']:,} titles.",
            context={"root_id": root_id, **result},
        )
    except Exception as exc:
        with scan_lock:
            scan_jobs[root_id] = {"status": "error", "error": str(exc)}
        record_event(
            "scan", "Source scan could not finish.",
            level="error", detail=str(exc), context={"root_id": root_id},
        )


def run_scan_all(roots: list[tuple[int, str]]) -> None:
    with scan_all_lock:
        scan_all_job.clear()
        scan_all_job.update({
            "status": "running", "total": len(roots), "completed": 0,
            "errors": 0, "current_root_id": None, "current_label": "",
            "files": 0, "titles": 0,
        })
    errors = 0
    record_event("scan", f"Scan all started for {len(roots):,} sources.")
    for completed, (root_id, label) in enumerate(roots):
        with scan_all_lock:
            scan_all_job.update({
                "current_root_id": root_id, "current_label": label,
                "completed": completed, "files": 0, "titles": 0,
            })
        run_scan(root_id)
        with scan_lock:
            if scan_jobs.get(root_id, {}).get("status") == "error":
                errors += 1
        with scan_all_lock:
            scan_all_job.update({"completed": completed + 1, "errors": errors})
    with scan_all_lock:
        scan_all_job.update({
            "status": "complete", "completed": len(roots), "errors": errors,
            "current_root_id": None, "current_label": "",
        })
    record_event(
        "scan",
        f"Scan all finished: {len(roots) - errors:,} sources completed and {errors:,} failed.",
        level="warning" if errors else "info",
        context={"sources": len(roots), "errors": errors},
    )


def run_title_scan(title_id: int) -> None:
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
        record_event(
            "scan",
            f"Series rescan finished for {title['metadata_title'] or title['title']}: {result['files']:,} files found.",
            context={"title_id": title_id, **result},
        )
    except Exception as exc:
        with title_scan_lock:
            title_scan_jobs[title_id] = {"status": "error", "error": str(exc)}
        record_event(
            "scan", "Series rescan could not finish.",
            level="error", detail=str(exc), context={"title_id": title_id},
        )


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
        with imdb_genre_lock:
            imdb_genre_job.clear()
            imdb_genre_job.update({
                "status": "complete", "phase": "complete",
                "id_processed": id_processed, "id_total": id_total,
                "id_found": id_found, "id_missing": id_missing,
                "id_errors": id_errors, "id_pending": pending, **result,
            })
    except Exception as exc:
        with imdb_genre_lock:
            imdb_genre_job.clear()
            imdb_genre_job.update({"status": "error", "error": str(exc)})


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/scans/{root_id}")
def scan_status(root_id: int) -> dict:
    with scan_lock:
        return dict(scan_jobs.get(root_id, {"status": "idle"}))


@app.get("/api/scan-all")
def scan_all_status() -> dict:
    with scan_all_lock:
        return dict(scan_all_job)


@app.post("/scan-all")
def start_scan_all():
    with scan_all_lock:
        if scan_all_job.get("status") in {"starting", "running"}:
            return redirect("/sources", "All sources are already being scanned")
    with scan_lock:
        if any(job.get("status") in {"starting", "running"} for job in scan_jobs.values()):
            return redirect("/sources", "Wait for the current source scan to finish")
    with db.connect() as conn:
        root_rows = conn.execute(
            "SELECT id, label, path FROM roots WHERE enabled=1 ORDER BY kind, label, path"
        ).fetchall()
    roots = [
        (row["id"], row["label"] or Path(row["path"]).name or row["path"])
        for row in root_rows
    ]
    if not roots:
        return redirect("/sources", "No enabled sources are configured")
    with scan_all_lock:
        scan_all_job.clear()
        scan_all_job.update({"status": "starting", "total": len(roots), "completed": 0})
    threading.Thread(target=run_scan_all, args=(roots,), daemon=True).start()
    return redirect("/sources", f"Scanning all {len(roots)} sources")


@app.get("/api/imdb-genres")
def imdb_genre_status() -> dict:
    with imdb_genre_lock:
        return dict(imdb_genre_job)


@app.get("/api/tasks")
def active_tasks() -> dict:
    tasks = []
    with scan_all_lock:
        all_scan = dict(scan_all_job)
    with scan_lock:
        scans = {
            root_id: dict(job) for root_id, job in scan_jobs.items()
            if job.get("status") in {"starting", "running"}
        }
    if all_scan.get("status") in {"starting", "running"}:
        current = all_scan.get("current_label") or "Preparing sources"
        tasks.append({
            "id": "scan-all", "label": "Scanning all sources",
            "detail": (
                f"{all_scan.get('completed', 0):,} of {all_scan.get('total', 0):,} "
                f"complete · {current}"
            ),
        })
        scans = {}
    if scans:
        placeholders = ",".join("?" for _ in scans)
        with db.connect() as conn:
            roots = {
                row["id"]: row["label"] or Path(row["path"]).name or row["path"]
                for row in conn.execute(
                    f"SELECT id, label, path FROM roots WHERE id IN ({placeholders})",
                    tuple(scans),
                )
            }
        for root_id, job in scans.items():
            tasks.append({
                "id": f"scan-{root_id}",
                "label": f"Scanning {roots.get(root_id, f'library {root_id}')}",
                "detail": (
                    f"{job.get('files', 0):,} video files · "
                    f"{job.get('titles', 0):,} titles"
                ),
            })
    with title_scan_lock:
        title_scans = {
            title_id: dict(job) for title_id, job in title_scan_jobs.items()
            if job.get("status") in {"starting", "running"}
        }
    for title_id, job in title_scans.items():
        tasks.append({
            "id": f"title-scan-{title_id}",
            "label": f"Rescanning {job.get('label') or 'series'}",
            "detail": f"{job.get('files', 0):,} video files found",
        })
    with imdb_genre_lock:
        genre_job = dict(imdb_genre_job)
    if genre_job.get("status") in {"starting", "running"}:
        if genre_job.get("phase") == "ids":
            label = "Backfilling IMDb IDs"
            detail = (
                f"{genre_job.get('id_processed', 0):,} of "
                f"{genre_job.get('id_total', 0):,} older matches checked"
            )
        elif genre_job.get("phase") == "basics":
            label = "Updating IMDb genres and types"
            detail = (
                f"{genre_job.get('records', 0):,} IMDb records checked · "
                f"{genre_job.get('matched', 0):,} titles found"
            )
        elif genre_job.get("phase") == "ratings":
            label = "Updating IMDb ratings"
            detail = (
                f"{genre_job.get('records', 0):,} rating records checked · "
                f"{genre_job.get('matched', 0):,} titles found"
            )
        elif genre_job.get("phase") == "episodes":
            label = "Linking IMDb episode records"
            detail = (
                f"{genre_job.get('records', 0):,} episode records checked · "
                f"{genre_job.get('matched', 0):,} library episodes found"
            )
        elif genre_job.get("phase") == "crew":
            label = "Updating title and episode credits"
            detail = (
                f"{genre_job.get('records', 0):,} crew records checked · "
                f"{genre_job.get('matched', 0):,} titles found"
            )
        elif genre_job.get("phase") == "principals":
            label = "Updating top-billed cast"
            detail = (
                f"{genre_job.get('records', 0):,} billing records checked · "
                f"{genre_job.get('matched', 0):,} titles found"
            )
        elif genre_job.get("phase") == "names":
            label = "Resolving IMDb people"
            detail = (
                f"{genre_job.get('records', 0):,} name records checked · "
                f"{genre_job.get('matched', 0):,} people found"
            )
        else:
            label = "Preparing IMDb metadata update"
            detail = "Starting background task"
        if genre_job.get("scope_label"):
            label = f"{label}: {genre_job['scope_label']}"
        tasks.append({"id": "imdb-metadata", "label": label, "detail": detail})
    with movie_match_lock:
        match_job = dict(movie_match_job)
    if match_job.get("status") in {"starting", "running"}:
        tasks.append({
            "id": "movie-match-analysis",
            "label": "Analyzing movie matches",
            "detail": (
                f"{match_job.get('processed', 0):,} of {match_job.get('total', 0):,} checked"
                + (f" · {match_job.get('matched', 0):,} suggestions found" if match_job.get("processed") else "")
            ),
        })
    with tv_match_lock:
        show_match_job = dict(tv_match_job)
    if show_match_job.get("status") in {"starting", "running"}:
        tasks.append({
            "id": "tv-match-analysis",
            "label": "Analyzing TV series matches",
            "detail": (
                f"{show_match_job.get('processed', 0):,} of {show_match_job.get('total', 0):,} checked"
                + (f" · {show_match_job.get('matched', 0):,} suggestions found" if show_match_job.get("processed") else "")
            ),
        })
    with media_info_lock:
        media_job = dict(media_info_job)
    if media_job.get("status") in {"starting", "running"}:
        tasks.append({
            "id": "media-info",
            "label": "Inspecting media files",
            "detail": (
                f"{media_job.get('processed', 0):,} of "
                f"{media_job.get('total', 0):,} checked"
                + (
                    f" Â· {media_job.get('current', '')}"
                    if media_job.get("current") else ""
                )
            ),
        })
    return {"tasks": tasks}


@app.get("/api/movie-match-analysis")
def movie_match_analysis_status() -> dict:
    with movie_match_lock:
        return dict(movie_match_job)


@app.get("/api/tv-match-analysis")
def tv_match_analysis_status() -> dict:
    with tv_match_lock:
        return dict(tv_match_job)


@app.get("/api/media-info")
def media_info_status() -> dict:
    with media_info_lock:
        return dict(media_info_job)


@app.get("/media-info/failures", response_class=HTMLResponse)
def media_info_failures(request: Request):
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT f.id, f.title_id, f.filename, f.path, f.media_info_error,
                      t.kind, COALESCE(t.metadata_title, t.title) display_title,
                      COALESCE(r.label, r.path) source_name
               FROM files f
               JOIN titles t ON t.id=f.title_id
               JOIN roots r ON r.id=t.root_id
               WHERE f.media_info_error IS NOT NULL
                 AND f.media_info_error!=''
               ORDER BY display_title COLLATE NOCASE, f.filename COLLATE NOCASE"""
        ).fetchall()
    failures = []
    for row in rows:
        failure = dict(row)
        explanation, separator, _technical = failure["media_info_error"].partition(
            " Technical detail:"
        )
        failure["explanation"] = explanation.strip() or (
            "InfoMancer could not read technical information from this file."
        )
        failure["logs_url"] = "/logs?" + urlencode({
            "category": "media", "search": failure["filename"],
        })
        failures.append(failure)
    return templates.TemplateResponse(request, "media_info_failures.html", {
        "failures": failures,
        "message": request.query_params.get("message", ""),
    })


@app.post("/media-info/scan")
def start_media_info_scan(request: Request, scope: str = Form("missing")):
    with media_info_lock:
        if media_info_job.get("status") in {"starting", "running"}:
            return redirect(
                "/settings/system",
                "Media inspection is already running. Progress remains visible in the task widget.",
            )
        media_info_job.clear()
        media_info_job.update({"status": "starting", "processed": 0, "total": 0})
    file_ids = None
    if scope == "all":
        with db.connect() as conn:
            file_ids = [
                row["id"] for row in conn.execute("SELECT id FROM files ORDER BY id")
            ]
    threading.Thread(target=run_media_inspection, args=(file_ids,), daemon=True).start()
    record_event(
        "media", "Media inspection was requested from System Settings.",
        user_id=request.state.user.id, context={"scope": scope},
    )
    return redirect(
        "/settings/system",
        "Media inspection started. You can continue using InfoMancer while it runs.",
    )


@app.post("/imdb-genres/sync")
def start_imdb_genre_sync(return_to: str = Form("")):
    destination = "/settings/metadata" if return_to == "/settings/metadata" else "/sources"
    with imdb_genre_lock:
        if imdb_genre_job.get("status") in {"starting", "running"}:
            return redirect(destination, "IMDb metadata update is already running.")
        imdb_genre_job.clear()
        imdb_genre_job.update({"status": "starting"})
    threading.Thread(target=run_imdb_genre_sync, daemon=True).start()
    return redirect(destination, "IMDb metadata update started.")


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


@app.post("/titles/{title_id}/imdb-refresh")
def refresh_title_imdb_metadata(title_id: int):
    with db.connect() as conn:
        title = conn.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
    if not title:
        raise HTTPException(404, "Title not found")
    if not (title["imdb_id"] or title["tvdb_id"] or title["tvdb_movie_id"]):
        return redirect(f"/titles/{title_id}", "Match this title before pulling IMDb metadata")
    label = title["metadata_title"] or title["title"]
    error = start_scoped_imdb_sync([title_id], None, label)
    return redirect(
        f"/titles/{title_id}",
        error or f"IMDb metadata refresh started for {label}",
    )


@app.post("/files/{file_id}/imdb-refresh")
def refresh_episode_imdb_metadata(file_id: int):
    with db.connect() as conn:
        file_row = conn.execute(
            """SELECT f.*, t.id title_id, t.kind, t.title, t.metadata_title,
                      t.imdb_id, t.tvdb_id
               FROM files f JOIN titles t ON t.id=f.title_id WHERE f.id=?""",
            (file_id,),
        ).fetchone()
        if not file_row:
            raise HTTPException(404, "Media file not found")
        if file_row["kind"] != "tv" or file_row["season"] is None:
            return redirect(f"/titles/{file_row['title_id']}", "IMDb episode metadata is available for parsed TV episodes")
        episode_ids = [
            row["id"] for row in conn.execute(
                """SELECT id FROM expected_episodes
                   WHERE title_id=? AND season=? AND episode BETWEEN ? AND ?
                   ORDER BY episode""",
                (
                    file_row["title_id"], file_row["season"],
                    file_row["episode_start"],
                    file_row["episode_end"] or file_row["episode_start"],
                ),
            )
        ]
    if not episode_ids:
        return redirect(f"/titles/{file_row['title_id']}", "Match this series and load its episode list first")
    label = (
        f"{file_row['metadata_title'] or file_row['title']} "
        f"S{file_row['season']:02d}E{file_row['episode_start']:02d}"
    )
    error = start_scoped_imdb_sync(
        [file_row["title_id"]], episode_ids, label,
    )
    return redirect(
        f"/titles/{file_row['title_id']}",
        error or f"IMDb metadata refresh started for {label}",
    )


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    with db.connect() as conn:
        counts = conn.execute(
            """SELECT
              (SELECT COUNT(*) FROM titles WHERE kind='movie') movies,
              (SELECT COUNT(*) FROM titles WHERE kind='tv') shows,
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
        ).fetchone()
        roots = conn.execute(
            """SELECT r.*, COUNT(DISTINCT t.id) title_count, COUNT(f.id) file_count
               FROM roots r LEFT JOIN titles t ON t.root_id=r.id
               LEFT JOIN files f ON f.title_id=t.id GROUP BY r.id ORDER BY r.kind, r.label, r.path"""
        ).fetchall()
        recent = conn.execute(
            "SELECT * FROM titles ORDER BY updated_at DESC LIMIT 8"
        ).fetchall()
    with scan_all_lock:
        all_scan_job = dict(scan_all_job)
    return templates.TemplateResponse(request, "dashboard.html", {
        "counts": counts, "roots": roots, "recent": recent, "jobs": scan_jobs,
        "scan_all_job": all_scan_job,
        "message": request.query_params.get("message", ""),
    })


@app.get("/intake", response_class=HTMLResponse)
def intake(request: Request):
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT t.*, r.label root_label, r.path root_path,
               (SELECT COUNT(*) FROM files f WHERE f.title_id=t.id) file_count
               FROM titles t JOIN roots r ON r.id=t.root_id
               WHERE t.discovered_at IS NOT NULL AND
                 ((t.kind='tv' AND t.tvdb_id IS NULL) OR
                  (t.kind='movie' AND t.tvdb_movie_id IS NULL))
               ORDER BY t.discovered_at DESC, t.kind, t.title COLLATE NOCASE"""
        ).fetchall()
    return templates.TemplateResponse(request, "intake.html", {
        "rows": rows, "message": request.query_params.get("message", ""),
    })


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
            "log_categories": event_log.categories(),
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


LIBRARY_EXPORT_FIELDS = [
    "title_id", "kind", "title", "release_year", "end_year", "continuing",
    "tvdb_id", "tvdb_movie_id", "tmdb_id", "imdb_id", "imdb_rating",
    "imdb_votes", "imdb_title_type", "genres", "date_added", "source",
    "source_path", "file_id", "file_path", "filename", "size_bytes",
    "season", "episode_start", "episode_end", "runtime_seconds", "width",
    "height", "video_codec", "audio_codec", "audio_channels", "bitrate",
    "container", "dynamic_range", "media_info_at", "media_info_error",
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
               COALESCE(uts.favorite,0) favorite, uts.personal_rating,
               uts.custom_order,
               COALESCE((SELECT GROUP_CONCAT(ut.name, ', ')
                 FROM title_tags tt JOIN user_tags ut ON ut.id=tt.tag_id
                 WHERE tt.title_id=t.id AND ut.user_id=?),'') tags
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
        item["collections"] = ""
        item["custom_fields"] = json.dumps({
            "favorite": bool(item.pop("favorite")),
            "personal_rating": item.pop("personal_rating"),
            "custom_order": item.pop("custom_order"),
        }, ensure_ascii=False)
        exported.append(item)
    return exported


@app.get("/exports/library")
def export_library(request: Request, format: str = "csv"):
    normalized = format.strip().casefold()
    if normalized not in {"csv", "json", "xml"}:
        return auth_error_response(
            request, 400, "Export format not supported",
            "Choose CSV, JSON, or XML, then try the export again.",
        )
    try:
        rows = library_export_rows(request.state.user.id)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"infomancer-library-{stamp}.{normalized}"
        if normalized == "csv":
            output = io.StringIO(newline="")
            writer = csv.DictWriter(output, fieldnames=LIBRARY_EXPORT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
            body = output.getvalue().encode("utf-8-sig")
            media_type = "text/csv; charset=utf-8"
        elif normalized == "json":
            body = json.dumps(
                {"exported_at": datetime.now(timezone.utc).isoformat(), "items": rows},
                ensure_ascii=False, indent=2,
            ).encode("utf-8")
            media_type = "application/json"
        else:
            root = ElementTree.Element(
                "infomancer-library",
                exported_at=datetime.now(timezone.utc).isoformat(),
            )
            for row in rows:
                item = ElementTree.SubElement(root, "media-file")
                for key, value in row.items():
                    field = ElementTree.SubElement(item, key.replace("_", "-"))
                    field.text = "" if value is None else str(value)
            body = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
            media_type = "application/xml"
    except (sqlite3.Error, OSError, ValueError) as exc:
        record_event(
            "export", "Library export could not be created.", level="error",
            detail=str(exc), user_id=request.state.user.id,
        )
        return auth_error_response(
            request, 500, "Library export could not be created",
            "InfoMancer could not read or format the catalog. Your library was not changed. Review Logs for the technical cause, then try again.",
        )
    record_event(
        "export", f"Library exported as {normalized.upper()}.",
        context={"rows": len(rows)}, user_id=request.state.user.id,
    )
    return Response(
        body, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/logs", response_class=HTMLResponse)
def logs_page(
    request: Request, level: str = "", category: str = "", search: str = "",
):
    return templates.TemplateResponse(request, "logs.html", {
        "events": event_log.query(level=level, category=category, search=search),
        "categories": event_log.categories(), "level": level,
        "category": category, "search": search,
        "message": request.query_params.get("message", ""),
    })


@app.get("/api/logs")
def logs_api(level: str = "", category: str = "", search: str = "", limit: int = 250):
    return {
        "events": [
            dict(row) for row in event_log.query(
                level=level, category=category, search=search, limit=limit
            )
        ]
    }


@app.get("/logs/export")
def export_logs():
    rows = event_log.query(limit=50000)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "id", "created_at", "level", "category", "message",
            "detail", "context_json", "user_name",
        ],
        extrasaction="ignore",
    )
    writer.writeheader()
    writer.writerows(dict(row) for row in rows)
    filename = f"infomancer-logs-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    return Response(
        output.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/settings")
def settings_index():
    return RedirectResponse("/settings/general", status_code=303)


@app.get("/settings/{section}", response_class=HTMLResponse)
def settings_section(request: Request, section: str):
    if section not in SETTINGS_SECTIONS:
        return auth_error_response(
            request, 404, "Settings page not found",
            "That Settings section does not exist. Choose one of the available sections.",
        )
    return render_settings(request, section)


@app.post("/settings/general")
def save_general_settings(
    request: Request,
    installation_name: str = Form(...), timezone_name: str = Form(...),
    default_library_view: str = Form(...), default_cover_size: str = Form(...),
):
    submitted = {
        "installation_name": installation_name,
        "timezone": timezone_name,
        "default_library_view": default_library_view,
        "default_cover_size": default_cover_size,
    }
    try:
        validated = app_settings.validate_general(
            installation_name, timezone_name, default_library_view, default_cover_size,
        )
        changed = app_settings.update(validated, request.state.user.id)
    except AppSettingError as exc:
        return render_settings(request, "general", str(exc), submitted, 400)
    message = (
        f"General settings saved. {changed} setting{'s' if changed != 1 else ''} changed."
        if changed else "General settings were already up to date; nothing changed."
    )
    return redirect("/settings/general", message)


@app.post("/settings/external-search")
def save_external_search_settings(
    request: Request, search_provider_name: str = Form(...),
    search_url_template: str = Form(...),
):
    submitted = {
        "search_provider_name": search_provider_name,
        "search_url_template": search_url_template,
    }
    try:
        validated = app_settings.validate_external_search(
            search_provider_name, search_url_template,
        )
        changed = app_settings.update(validated, request.state.user.id)
    except AppSettingError as exc:
        return render_settings(request, "external-search", str(exc), submitted, 400)
    message = (
        f"External-search settings saved. {changed} setting{'s' if changed != 1 else ''} changed."
        if changed else "External-search settings were already up to date; nothing changed."
    )
    return redirect("/settings/external-search", message)


@app.post("/settings/logging")
def save_logging_settings(request: Request, log_level: str = Form(...)):
    try:
        validated = app_settings.validate_logging(log_level)
        changed = app_settings.update(validated, request.state.user.id)
    except AppSettingError as exc:
        return render_settings(
            request, "system", str(exc), {"log_level": log_level}, 400
        )
    label = {"info": "Standard", "verbose": "Verbose", "debug": "Debug"}[
        validated["log_level"]
    ]
    record_event(
        "settings", f"Application logging changed to {label}.",
        user_id=request.state.user.id,
    )
    message = (
        f"Logging changed to {label}. New events will use this level."
        if changed else f"Logging was already set to {label}; nothing changed."
    )
    return redirect("/settings/system", message)


@app.post("/settings/metadata/tvdb-test")
def test_tvdb_settings_connection():
    if not tvdb.api_key:
        return redirect(
            "/settings/metadata",
            "TVDB connection was not tested because TVDB_API_KEY is not configured on the server.",
        )
    try:
        tvdb.test_connection()
    except TVDBError:
        return redirect(
            "/settings/metadata",
            "TVDB could not verify the configured key and PIN. Check the server configuration and TVDB account, then try again.",
        )
    except Exception:
        return redirect(
            "/settings/metadata",
            "TVDB could not be reached. Check the InfoMancer server's internet connection and try again.",
        )
    return redirect("/settings/metadata", "TVDB connection verified successfully.")


@app.get("/sources", response_class=HTMLResponse)
def sources(request: Request):
    with db.connect() as conn:
        roots = conn.execute(
            """SELECT r.*, COUNT(DISTINCT t.id) title_count, COUNT(f.id) file_count
               FROM roots r LEFT JOIN titles t ON t.root_id=r.id
               LEFT JOIN files f ON f.title_id=t.id GROUP BY r.id
               ORDER BY r.kind, r.label, r.path"""
        ).fetchall()
    setup_state = engagement.setup_state(request.state.user.id)
    return templates.TemplateResponse(request, "sources.html", {
        "roots": roots, "jobs": scan_jobs,
        "setup_assistant_active": bool(
            setup_state and setup_state["mode"] == "guided"
            and not setup_state["completed_at"]
            and setup_state["current_step"] == "sources"
        ),
        "message": request.query_params.get("message", ""),
    })


@app.get("/api/source-browser")
def source_browser(path: str = ""):
    try:
        return list_folders(path, settings.media_browse_roots)
    except SourceBrowserError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/source-preview")
def source_preview(path: str):
    try:
        return preview_folder(path, settings.media_browse_roots)
    except SourceBrowserError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/maintenance/optimize-database")
def optimize_database(return_to: str = Form("")):
    destination = "/settings/system" if return_to == "/settings/system" else "/sources"
    try:
        with db.connect() as conn:
            conn.execute("ANALYZE")
            conn.execute("PRAGMA optimize")
        with db.connect() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error as exc:
        record_event(
            "database", "Database optimization could not finish.",
            level="error", detail=str(exc),
        )
        return redirect(
            destination,
            "Database optimization could not finish. The catalog was not deleted; check the application logs for the technical cause.",
        )
    record_event("database", "Database indexes and query statistics optimized successfully.")
    return redirect(destination, "Database indexes and query statistics optimized successfully.")


@app.post("/maintenance/restart")
def restart_application(confirm: str = Form(""), return_to: str = Form("")):
    destination = "/settings/system" if return_to == "/settings/system" else "/sources"
    if confirm != "RESTART":
        return redirect(destination, "Restart cancelled; InfoMancer was not interrupted.")

    def exit_for_container_restart() -> None:
        time.sleep(1.0)
        os._exit(0)

    threading.Thread(target=exit_for_container_restart, daemon=True).start()
    return redirect(destination, "Restart requested; InfoMancer will be available again shortly.")


@app.post("/roots")
def add_root(
    path: str = Form(...), kind: str = Form(...), label: str = Form(""),
    scan_after: str = Form(""), return_to: str = Form(""),
):
    destination = (
        "/getting-started/sources"
        if return_to == "/getting-started/sources" else "/sources"
    )
    if kind not in {"movie", "tv"}:
        return redirect(
            destination,
            "Choose Movies or TV Shows as the library type, then try again.",
        )
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        return redirect(
            destination,
            f"InfoMancer cannot access {resolved}. Check the folder and server permissions, then try again.",
        )
    try:
        with db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO roots(path, kind, label) VALUES (?, ?, ?)",
                (str(resolved), kind, label.strip()),
            )
            root_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        return redirect(
            destination,
            "That folder is already configured as a media source; nothing was added.",
        )
    if scan_after and root_id:
        with scan_lock:
            scan_jobs[root_id] = {"status": "starting", "files": 0, "titles": 0}
        threading.Thread(target=run_scan, args=(root_id,), daemon=True).start()
        return redirect(
            destination, "Media source added successfully; its first scan has started."
        )
    return redirect(destination, "Media source added successfully.")


@app.post("/roots/{root_id}/scan")
def start_scan(root_id: int):
    with scan_all_lock:
        if scan_all_job.get("status") in {"starting", "running"}:
            return redirect("/sources", "That source is already included in Scan all")
    with scan_lock:
        if scan_jobs.get(root_id, {}).get("status") in {"starting", "running"}:
            return redirect("/sources", "That library is already scanning")
        scan_jobs[root_id] = {"status": "starting", "files": 0, "titles": 0}
    thread = threading.Thread(target=run_scan, args=(root_id,), daemon=True)
    thread.start()
    return redirect("/sources", "Scan started")


@app.post("/roots/{root_id}/label")
def update_root_label(root_id: int, label: str = Form("")):
    cleaned = " ".join(label.split())[:120]
    with db.connect() as conn:
        result = conn.execute(
            "UPDATE roots SET label=? WHERE id=?", (cleaned, root_id)
        )
    if result.rowcount == 0:
        return redirect("/sources", "That library root no longer exists")
    return redirect(
        "/sources", "Source name updated" if cleaned else "Source name cleared"
    )


@app.post("/titles/{title_id}/scan")
def start_title_scan(title_id: int):
    with scan_all_lock:
        if scan_all_job.get("status") in {"starting", "running"}:
            return redirect(f"/titles/{title_id}", "Wait for Scan all to finish")
    with scan_lock:
        if any(job.get("status") in {"starting", "running"} for job in scan_jobs.values()):
            return redirect(f"/titles/{title_id}", "Wait for the library scan to finish")
    with title_scan_lock:
        if title_scan_jobs.get(title_id, {}).get("status") in {"starting", "running"}:
            return redirect(f"/titles/{title_id}", "This series is already scanning")
        title_scan_jobs[title_id] = {"status": "starting", "files": 0, "label": "Series"}
    threading.Thread(target=run_title_scan, args=(title_id,), daemon=True).start()
    return redirect(f"/titles/{title_id}", "Series rescan started")


@app.post("/roots/{root_id}/delete")
def delete_root(root_id: int, confirm: str = Form("")):
    if confirm != "REMOVE":
        return redirect("/sources", "Type REMOVE to remove a catalog root")
    with db.connect() as conn:
        conn.execute("DELETE FROM roots WHERE id=?", (root_id,))
    return redirect("/sources", "Catalog root removed; media files were untouched")


def title_return_path(title_id: int, return_to: str = "") -> str:
    parsed = urlparse(return_to)
    if (
        return_to and not parsed.scheme and not parsed.netloc
        and parsed.path in {"/library", "/movies", "/shows", f"/titles/{title_id}"}
    ):
        return return_to
    return f"/titles/{title_id}"


@app.post("/titles/{title_id}/favorite")
def toggle_favorite(
    request: Request, title_id: int, return_to: str = Form(""),
):
    if request.state.user.id <= 0:
        return redirect(
            title_return_path(title_id, return_to),
            "Favorites require a signed-in user account so InfoMancer knows whose list to update.",
        )
    with db.connect() as conn:
        title = conn.execute("SELECT id FROM titles WHERE id=?", (title_id,)).fetchone()
        if not title:
            raise HTTPException(404, "Title not found")
        current = conn.execute(
            "SELECT favorite FROM user_title_state WHERE user_id=? AND title_id=?",
            (request.state.user.id, title_id),
        ).fetchone()
        favorite = not bool(current and current["favorite"])
        conn.execute(
            """INSERT INTO user_title_state(user_id,title_id,favorite,updated_at)
               VALUES (?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(user_id,title_id) DO UPDATE SET
                 favorite=excluded.favorite,updated_at=CURRENT_TIMESTAMP""",
            (request.state.user.id, title_id, int(favorite)),
        )
    record_event(
        "library", "Title added to favorites." if favorite else "Title removed from favorites.",
        user_id=request.state.user.id, context={"title_id": title_id},
    )
    return redirect(
        title_return_path(title_id, return_to),
        "Added to your favorites." if favorite else "Removed from your favorites.",
    )


@app.get("/titles/{title_id}/organize", response_class=HTMLResponse)
def organize_title_page(request: Request, title_id: int):
    with db.connect() as conn:
        title = conn.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
        if not title:
            raise HTTPException(404, "Title not found")
        state = conn.execute(
            "SELECT * FROM user_title_state WHERE user_id=? AND title_id=?",
            (request.state.user.id, title_id),
        ).fetchone()
        tags = conn.execute(
            """SELECT ut.*,tt.title_id IS NOT NULL selected
               FROM user_tags ut LEFT JOIN title_tags tt
                 ON tt.tag_id=ut.id AND tt.title_id=?
               WHERE ut.user_id=? ORDER BY ut.name COLLATE NOCASE""",
            (title_id, request.state.user.id),
        ).fetchall()
    return templates.TemplateResponse(request, "organize.html", {
        "title": title, "title_state": state, "tags": tags,
        "message": request.query_params.get("message", ""),
    })


@app.post("/titles/{title_id}/organize")
def save_title_organization(
    request: Request, title_id: int, favorite: str = Form(""),
    personal_rating: str = Form(""), custom_order: str = Form(""),
    tag_names: str = Form(""), selected_tags: list[int] = Form(default=[]),
):
    if request.state.user.id <= 0:
        return redirect(
            f"/titles/{title_id}",
            "Personal organization requires a signed-in user account.",
        )
    try:
        rating = float(personal_rating) if personal_rating.strip() else None
        if rating is not None and not 0 <= rating <= 10:
            raise ValueError
    except ValueError:
        return redirect(
            f"/titles/{title_id}/organize",
            "Personal rating must be a number from 0 to 10, or left blank.",
        )
    try:
        order_value = int(custom_order) if custom_order.strip() else None
    except ValueError:
        return redirect(
            f"/titles/{title_id}/organize",
            "Custom order must be a whole number, or left blank.",
        )
    new_names = []
    for raw_name in tag_names.split(","):
        cleaned = " ".join(raw_name.strip().split())
        if cleaned and cleaned.casefold() not in {item.casefold() for item in new_names}:
            new_names.append(cleaned[:40])
    if len(new_names) > 20:
        return redirect(
            f"/titles/{title_id}/organize",
            "Add no more than 20 new tags at a time so they remain easy to review.",
        )
    with db.connect() as conn:
        if not conn.execute("SELECT id FROM titles WHERE id=?", (title_id,)).fetchone():
            raise HTTPException(404, "Title not found")
        conn.execute(
            """INSERT INTO user_title_state
               (user_id,title_id,favorite,personal_rating,custom_order,updated_at)
               VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(user_id,title_id) DO UPDATE SET
                 favorite=excluded.favorite,
                 personal_rating=excluded.personal_rating,
                 custom_order=excluded.custom_order,
                 updated_at=CURRENT_TIMESTAMP""",
            (
                request.state.user.id, title_id, int(favorite == "1"),
                rating, order_value,
            ),
        )
        allowed_ids = {
            row["id"] for row in conn.execute(
                "SELECT id FROM user_tags WHERE user_id=?",
                (request.state.user.id,),
            )
        }
        chosen_ids = {tag_id for tag_id in selected_tags if tag_id in allowed_ids}
        for name in new_names:
            conn.execute(
                """INSERT INTO user_tags(user_id,name) VALUES (?,?)
                   ON CONFLICT(user_id,name) DO NOTHING""",
                (request.state.user.id, name),
            )
            row = conn.execute(
                "SELECT id FROM user_tags WHERE user_id=? AND name=? COLLATE NOCASE",
                (request.state.user.id, name),
            ).fetchone()
            if row:
                chosen_ids.add(row["id"])
        conn.execute(
            """DELETE FROM title_tags WHERE title_id=? AND tag_id IN
               (SELECT id FROM user_tags WHERE user_id=?)""",
            (title_id, request.state.user.id),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO title_tags(title_id,tag_id) VALUES (?,?)",
            [(title_id, tag_id) for tag_id in chosen_ids],
        )
    record_event(
        "library", "Personal title organization updated.",
        user_id=request.state.user.id,
        context={"title_id": title_id, "tags": len(chosen_ids)},
    )
    return redirect(f"/titles/{title_id}", "Favorites, rating, order, and tags saved.")


@app.post("/titles/organize-bulk", response_class=HTMLResponse)
def organize_titles_bulk(
    request: Request, selected: list[int] = Form(default=[]),
    apply: str = Form(""), selected_tags: list[int] = Form(default=[]),
    tag_names: str = Form(""),
):
    title_ids = list(dict.fromkeys(selected))[:1000]
    if not title_ids:
        return redirect(
            "/library",
            "Select at least one movie or TV series before organizing tags.",
        )
    user_id = request.state.user.id
    with db.connect() as conn:
        placeholders = ",".join("?" for _ in title_ids)
        titles = conn.execute(
            f"""SELECT id,kind,COALESCE(metadata_title,title) display_title
                FROM titles WHERE id IN ({placeholders})
                ORDER BY display_title COLLATE NOCASE""",
            title_ids,
        ).fetchall()
        valid_ids = {row["id"] for row in titles}
        title_ids = [title_id for title_id in title_ids if title_id in valid_ids]
        tags = conn.execute(
            """SELECT ut.*,COUNT(tt.title_id) usage_count
               FROM user_tags ut LEFT JOIN title_tags tt ON tt.tag_id=ut.id
               WHERE ut.user_id=? GROUP BY ut.id ORDER BY ut.name COLLATE NOCASE""",
            (user_id,),
        ).fetchall()
        if apply == "1":
            allowed = {row["id"] for row in tags}
            tag_ids = {tag_id for tag_id in selected_tags if tag_id in allowed}
            new_names = []
            for raw_name in tag_names.split(","):
                name = " ".join(raw_name.strip().split())[:40]
                if name and name.casefold() not in {item.casefold() for item in new_names}:
                    new_names.append(name)
            if len(new_names) > 20:
                return redirect(
                    "/library",
                    "Tags were not changed. Add no more than 20 new tags at one time.",
                )
            for name in new_names:
                conn.execute(
                    """INSERT INTO user_tags(user_id,name) VALUES (?,?)
                       ON CONFLICT(user_id,name) DO NOTHING""",
                    (user_id, name),
                )
                row = conn.execute(
                    "SELECT id FROM user_tags WHERE user_id=? AND name=? COLLATE NOCASE",
                    (user_id, name),
                ).fetchone()
                if row:
                    tag_ids.add(row["id"])
            conn.executemany(
                "INSERT OR IGNORE INTO title_tags(title_id,tag_id) VALUES (?,?)",
                [(title_id, tag_id) for title_id in title_ids for tag_id in tag_ids],
            )
            record_event(
                "library",
                f"Tags added to {len(title_ids)} selected titles.",
                user_id=user_id,
                context={"titles": len(title_ids), "tags": len(tag_ids)},
            )
            return redirect(
                "/library",
                f"Tags added to {len(title_ids)} selected title{'s' if len(title_ids) != 1 else ''}.",
            )
    return templates.TemplateResponse(request, "organize_bulk.html", {
        "titles": titles, "title_ids": title_ids, "tags": tags, "message": "",
    })


@app.get("/tags", response_class=HTMLResponse)
def manage_tags(request: Request):
    with db.connect() as conn:
        tags = conn.execute(
            """SELECT ut.*,COUNT(tt.title_id) usage_count
               FROM user_tags ut LEFT JOIN title_tags tt ON tt.tag_id=ut.id
               WHERE ut.user_id=? GROUP BY ut.id ORDER BY ut.name COLLATE NOCASE""",
            (request.state.user.id,),
        ).fetchall()
    return templates.TemplateResponse(request, "tags.html", {
        "tags": tags, "message": request.query_params.get("message", ""),
    })


@app.post("/tags/create")
def create_tag(request: Request, name: str = Form(...)):
    cleaned = " ".join(name.strip().split())[:40]
    if not cleaned:
        return redirect("/tags", "Enter a tag name before creating it.")
    try:
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO user_tags(user_id,name) VALUES (?,?)",
                (request.state.user.id, cleaned),
            )
    except sqlite3.IntegrityError:
        return redirect(
            "/tags",
            f'The tag "{cleaned}" already exists. Choose a different name or use the existing tag.',
        )
    return redirect("/tags", f'Tag "{cleaned}" created.')


@app.post("/tags/{tag_id}/rename")
def rename_tag(request: Request, tag_id: int, name: str = Form(...)):
    cleaned = " ".join(name.strip().split())[:40]
    if not cleaned:
        return redirect("/tags", "Tag name was not changed because the new name was empty.")
    try:
        with db.connect() as conn:
            result = conn.execute(
                "UPDATE user_tags SET name=? WHERE id=? AND user_id=?",
                (cleaned, tag_id, request.state.user.id),
            )
            if not result.rowcount:
                return redirect("/tags", "That tag could not be found in your account.")
    except sqlite3.IntegrityError:
        return redirect(
            "/tags",
            f'The tag "{cleaned}" already exists. Merge titles into that tag or choose another name.',
        )
    return redirect("/tags", f'Tag renamed to "{cleaned}".')


@app.post("/tags/{tag_id}/delete")
def delete_tag(request: Request, tag_id: int):
    with db.connect() as conn:
        tag = conn.execute(
            "SELECT name FROM user_tags WHERE id=? AND user_id=?",
            (tag_id, request.state.user.id),
        ).fetchone()
        if not tag:
            return redirect("/tags", "That tag could not be found in your account.")
        conn.execute(
            "DELETE FROM user_tags WHERE id=? AND user_id=?",
            (tag_id, request.state.user.id),
        )
    return redirect(
        "/tags",
        f'Tag "{tag["name"]}" deleted. Movies and TV series were not removed.',
    )


@app.get("/library", response_class=HTMLResponse)
def library(
    request: Request, q: str = "", kind: str = "all", letter: str = "",
    genre: str = "", title_type: str = "", root: str = "",
    person: str = "", person_name: str = "", credit_role: str = "",
    match: str = "", gaps: str = "", favorite: str = "", tag: str = "",
    sort: str = "title",
):
    conditions, params = [], []
    root_id = int(root) if root.isdigit() else None
    person_id = person if re.fullmatch(r"nm\d+", person) else ""
    person_name = person_name.strip()
    credit_role = credit_role if credit_role in {"actor", "director", "writer"} else ""
    match_status = match if match in {"matched", "unmatched"} else ""
    gap_status = gaps if kind != "movie" and gaps in {"missing", "complete"} else ""
    favorite_status = "favorites" if favorite == "favorites" else ""
    tag_id = int(tag) if tag.isdigit() else None
    sort_key = sort if sort in {
        "title", "release_new", "release_old", "rating", "personal_rating",
        "date_added", "runtime", "resolution", "bitrate", "file_size",
        "favorites", "random", "custom",
    } else "title"
    if q:
        conditions.append(
            "(t.title LIKE ? OR t.metadata_title LIKE ? OR EXISTS "
            "(SELECT 1 FROM files qf WHERE qf.title_id=t.id AND qf.filename LIKE ?) "
            "OR EXISTS ("
            "SELECT 1 FROM title_tags qtt "
            "JOIN user_tags qut ON qut.id=qtt.tag_id "
            "WHERE qtt.title_id=t.id AND qut.user_id=? AND qut.name LIKE ?"
            "))"
        )
        term = f"%{q}%"
        params.extend([term, term, term, request.state.user.id, term])
    if kind in {"movie", "tv"}:
        conditions.append("t.kind=?")
        params.append(kind)
    if genre:
        conditions.append("INSTR(',' || LOWER(COALESCE(t.genres,'')) || ',', ?) > 0")
        params.append(f",{genre.lower()},")
    if title_type:
        conditions.append("t.imdb_title_type=?")
        params.append(title_type)
    if root_id is not None:
        conditions.append("t.root_id=?")
        params.append(root_id)
    matched_condition = (
        "((t.kind='tv' AND t.tvdb_id IS NOT NULL) OR "
        "(t.kind='movie' AND (t.tvdb_movie_id IS NOT NULL OR "
        "t.tmdb_id IS NOT NULL OR t.imdb_id IS NOT NULL)))"
    )
    if match_status == "matched":
        conditions.append(matched_condition)
    elif match_status == "unmatched":
        conditions.append(f"NOT {matched_condition}")
    if gap_status == "missing":
        conditions.append("t.kind='tv' AND COALESCE(ms.missing_count,0) > 0")
    elif gap_status == "complete":
        conditions.append(
            "t.kind='tv' AND t.tvdb_id IS NOT NULL "
            "AND COALESCE(ms.missing_count,0) = 0"
        )
    if person_id or person_name:
        credit_conditions = ["c.title_id=t.id"]
        if person_id:
            credit_conditions.append("c.imdb_person_id=?")
            params.append(person_id)
        else:
            credit_conditions.append("c.person_name LIKE ?")
            params.append(f"%{person_name}%")
        if credit_role:
            credit_conditions.append("c.role=?")
            params.append(credit_role)
        conditions.append(
            "EXISTS (SELECT 1 FROM title_credits c WHERE "
            + " AND ".join(credit_conditions) + ")"
        )
    if favorite_status:
        conditions.append("COALESCE(uts.favorite,0)=1")
    if tag_id is not None:
        conditions.append(
            "EXISTS (SELECT 1 FROM title_tags filtered_tag "
            "JOIN user_tags filtered_user_tag ON filtered_user_tag.id=filtered_tag.tag_id "
            "WHERE filtered_tag.title_id=t.id AND filtered_tag.tag_id=? "
            "AND filtered_user_tag.user_id=?)"
        )
        params.extend([tag_id, request.state.user.id])
    normalized_letter = letter.upper() if letter else ""
    if normalized_letter == "#":
        conditions.append("COALESCE(NULLIF(t.metadata_title,''),t.title) GLOB '[0-9]*'")
    elif len(normalized_letter) == 1 and normalized_letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        conditions.append(
            "UPPER(SUBSTR(COALESCE(NULLIF(t.metadata_title,''),t.title),1,1))=?"
        )
        params.append(normalized_letter)
    else:
        normalized_letter = ""
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    sort_sql = {
        "title": "COALESCE(t.metadata_title,t.title) COLLATE NOCASE",
        "release_new": "COALESCE(t.metadata_year,t.year,0) DESC, COALESCE(t.metadata_title,t.title) COLLATE NOCASE",
        "release_old": "COALESCE(t.metadata_year,t.year,9999), COALESCE(t.metadata_title,t.title) COLLATE NOCASE",
        "rating": "t.imdb_rating IS NULL, t.imdb_rating DESC, COALESCE(t.metadata_title,t.title) COLLATE NOCASE",
        "personal_rating": "uts.personal_rating IS NULL, uts.personal_rating DESC, COALESCE(t.metadata_title,t.title) COLLATE NOCASE",
        "date_added": "t.discovered_at IS NULL, t.discovered_at DESC, t.id DESC",
        "runtime": "COALESCE(fs.runtime_seconds,0) DESC, COALESCE(t.metadata_title,t.title) COLLATE NOCASE",
        "resolution": "COALESCE(fs.resolution_pixels,0) DESC, COALESCE(t.metadata_title,t.title) COLLATE NOCASE",
        "bitrate": "COALESCE(fs.max_bitrate,0) DESC, COALESCE(t.metadata_title,t.title) COLLATE NOCASE",
        "file_size": "COALESCE(fs.bytes,0) DESC, COALESCE(t.metadata_title,t.title) COLLATE NOCASE",
        "favorites": "COALESCE(uts.favorite,0) DESC, COALESCE(t.metadata_title,t.title) COLLATE NOCASE",
        "random": "RANDOM()",
        "custom": "uts.custom_order IS NULL, uts.custom_order, COALESCE(t.metadata_title,t.title) COLLATE NOCASE",
    }[sort_key]
    with db.connect() as conn:
        option_conditions = ["(genres IS NOT NULL OR imdb_title_type IS NOT NULL)"]
        option_params: list = []
        if kind in {"movie", "tv"}:
            option_conditions.append("kind=?")
            option_params.append(kind)
        if root_id is not None:
            option_conditions.append("root_id=?")
            option_params.append(root_id)
        option_condition = "WHERE " + " AND ".join(option_conditions)
        metadata_options = conn.execute(
            f"SELECT genres, imdb_title_type FROM titles {option_condition}",
            option_params,
        ).fetchall()
        root_options = conn.execute(
            "SELECT id, label, path, kind FROM roots WHERE enabled=1 ORDER BY kind, label, path"
        ).fetchall()
        selected_person = None
        if person_id:
            selected_person = conn.execute(
                """SELECT imdb_person_id, person_name FROM title_credits
                   WHERE imdb_person_id=? ORDER BY person_name LIMIT 1""",
                (person_id,),
            ).fetchone()
        genre_options = sorted({
            value for row in metadata_options for value in (row["genres"] or "").split(",")
            if value
        })
        title_type_options = sorted({
            row["imdb_title_type"] for row in metadata_options if row["imdb_title_type"]
        }, key=display_title_type)
        tag_options = conn.execute(
            """SELECT ut.id,ut.name,ut.color,COUNT(tt.title_id) title_count
               FROM user_tags ut LEFT JOIN title_tags tt ON tt.tag_id=ut.id
               WHERE ut.user_id=? GROUP BY ut.id ORDER BY ut.name COLLATE NOCASE""",
            (request.state.user.id,),
        ).fetchall()
        rows = conn.execute(
            f"""WITH file_stats AS (
                  SELECT title_id, COUNT(*) file_count, COALESCE(SUM(size_bytes),0) bytes,
                    MIN(id) first_file_id,
                    SUM(runtime_seconds) runtime_seconds,
                    MAX(COALESCE(width,0) * COALESCE(height,0)) resolution_pixels,
                    MAX(bitrate) max_bitrate,
                    COALESCE(SUM(CASE WHEN season IS NOT NULL AND episode_start IS NOT NULL
                      THEN COALESCE(episode_end, episode_start) - episode_start + 1
                      ELSE 0 END), 0) episode_count
                  FROM files GROUP BY title_id
                ), missing_stats AS (
                  SELECT e.title_id, COUNT(*) missing_count
                  FROM expected_episodes e
                  WHERE e.season > 0 AND (e.aired IS NULL OR e.aired <= date('now'))
                    AND NOT EXISTS (
                      SELECT 1 FROM files owned
                      WHERE owned.title_id=e.title_id AND owned.season=e.season
                        AND e.episode BETWEEN owned.episode_start
                          AND COALESCE(owned.episode_end, owned.episode_start)
                    )
                  GROUP BY e.title_id
                )
                SELECT t.*, COALESCE(fs.file_count,0) file_count,
                  COALESCE(fs.bytes,0) bytes, fs.first_file_id,
                  fs.runtime_seconds,fs.resolution_pixels,fs.max_bitrate,
                  COALESCE(fs.episode_count,0) episode_count,
                  COALESCE(ms.missing_count,0) missing_count,
                  COALESCE(uts.favorite,0) favorite,
                  uts.personal_rating,uts.custom_order,
                  (SELECT GROUP_CONCAT(ut.name, ', ')
                   FROM title_tags tt JOIN user_tags ut ON ut.id=tt.tag_id
                   WHERE tt.title_id=t.id AND ut.user_id=?) custom_tags
                FROM titles t
                LEFT JOIN file_stats fs ON fs.title_id=t.id
                LEFT JOIN missing_stats ms ON ms.title_id=t.id
                LEFT JOIN user_title_state uts
                  ON uts.title_id=t.id AND uts.user_id=?
                {where}
                ORDER BY {sort_sql} LIMIT 1000""",
            [request.state.user.id, request.state.user.id, *params],
        ).fetchall()
    return templates.TemplateResponse(request, "library.html", {
        "rows": rows, "q": q, "kind": kind, "letter": normalized_letter,
        "genre": genre, "title_type": title_type, "root_id": root_id,
        "match_status": match_status, "gap_status": gap_status,
        "favorite_status": favorite_status, "tag_id": tag_id, "sort_key": sort_key,
        "tag_options": tag_options,
        "root_options": root_options,
        "selected_root": next((item for item in root_options if item["id"] == root_id), None),
        "person_id": person_id, "person_name": (
            selected_person["person_name"] if selected_person else person_name
        ),
        "credit_role": credit_role,
        "genre_options": genre_options, "title_type_options": title_type_options,
        "filter_query": urlencode({
            key: value for key, value in {
                "q": q, "genre": genre, "title_type": title_type, "root": root_id,
                "person": person_id, "person_name": person_name,
                "credit_role": credit_role, "match": match_status,
                "gaps": gap_status,
                "favorite": favorite_status, "tag": tag_id, "sort": sort_key,
            }.items() if value
        }),
        "source_query": urlencode({
            key: value for key, value in {
                "q": q, "genre": genre, "title_type": title_type,
                "root": root_id, "person": person_id,
                "person_name": (
                    selected_person["person_name"] if selected_person else person_name
                ),
                "credit_role": credit_role, "match": match_status,
                "gaps": gap_status,
                "favorite": favorite_status, "tag": tag_id, "sort": sort_key,
            }.items() if value
        }),
        "heading": {"movie": "Movies", "tv": "TV Shows"}.get(kind, "Library"),
        "message": request.query_params.get("message", ""),
    })


@app.get("/movies", response_class=HTMLResponse)
def movies(
    request: Request, q: str = "", letter: str = "",
    genre: str = "", title_type: str = "", root: str = "",
    person: str = "", person_name: str = "", credit_role: str = "",
    match: str = "", gaps: str = "", favorite: str = "", tag: str = "",
    sort: str = "title",
):
    return library(
        request, q, "movie", letter, genre, title_type, root,
        person, person_name, credit_role, match, gaps, favorite, tag, sort,
    )


@app.get("/shows", response_class=HTMLResponse)
def shows(
    request: Request, q: str = "", letter: str = "",
    genre: str = "", title_type: str = "", root: str = "",
    person: str = "", person_name: str = "", credit_role: str = "",
    match: str = "", gaps: str = "", favorite: str = "", tag: str = "",
    sort: str = "title",
):
    return library(
        request, q, "tv", letter, genre, title_type, root,
        person, person_name, credit_role, match, gaps, favorite, tag, sort,
    )


@app.get("/api/people")
def people_search(q: str = "", role: str = "", kind: str = "") -> dict:
    query = q.strip()
    if len(query) < 2:
        return {"people": []}
    role = role if role in {"actor", "director", "writer"} else ""
    conditions = ["c.person_name LIKE ?"]
    params: list = [f"%{query}%"]
    if kind in {"movie", "tv"}:
        conditions.append("t.kind=?")
        params.append(kind)
    if role:
        conditions.append("c.role=?")
        params.append(role)
    params.extend([query, f"{query}%"])
    with db.connect() as conn:
        rows = conn.execute(
            f"""SELECT c.imdb_person_id, c.person_name,
                       GROUP_CONCAT(DISTINCT c.role) roles,
                       COUNT(DISTINCT c.title_id) title_count
                FROM title_credits c JOIN titles t ON t.id=c.title_id
                WHERE {' AND '.join(conditions)}
                GROUP BY c.imdb_person_id, c.person_name
                ORDER BY CASE WHEN c.person_name=? COLLATE NOCASE THEN 0
                              WHEN c.person_name LIKE ? THEN 1 ELSE 2 END,
                         title_count DESC, c.person_name COLLATE NOCASE
                LIMIT 10""",
            params,
        ).fetchall()
    return {"people": [dict(row) for row in rows]}


@app.get("/titles/{title_id}", response_class=HTMLResponse)
def title_detail(request: Request, title_id: int):
    with db.connect() as conn:
        title = conn.execute(
            """SELECT t.*, r.last_scanned_at root_last_scanned_at
               FROM titles t JOIN roots r ON r.id=t.root_id WHERE t.id=?""",
            (title_id,),
        ).fetchone()
        if not title:
            raise HTTPException(404, "Title not found")
        if (title["kind"] == "tv" and title["tvdb_id"]
                and (not title["poster_url"] or not title["imdb_id"]
                     or not title["metadata_title_language"])):
            try:
                series = tvdb.series(title["tvdb_id"])
                poster_url = poster_from(series)
                _tmdb_id, imdb_id = plex_movie_ids(series)
                metadata_title, title_language = localized_tvdb_title(
                    series, title["metadata_title"]
                )
                if poster_url or imdb_id or metadata_title:
                    conn.execute(
                        """UPDATE titles SET
                           poster_url=COALESCE(NULLIF(?, ''), poster_url),
                           imdb_id=COALESCE(NULLIF(?, ''), imdb_id),
                           metadata_title=COALESCE(NULLIF(?, ''), metadata_title),
                           metadata_title_language=?,
                           imdb_checked_at=CURRENT_TIMESTAMP
                           WHERE id=?""",
                        (
                            poster_url, imdb_id, metadata_title,
                            title_language or "preserved", title_id,
                        ),
                    )
                    title = conn.execute(
                        """SELECT t.*, r.last_scanned_at root_last_scanned_at
                           FROM titles t JOIN roots r ON r.id=t.root_id WHERE t.id=?""",
                        (title_id,),
                    ).fetchone()
            except TVDBError:
                # Poster enrichment is optional and should never block the
                # locally cataloged show detail page.
                pass
        file_rows = conn.execute(
            """SELECT f.* FROM files f
               WHERE f.title_id=? ORDER BY f.season, f.episode_start, f.filename""",
            (title_id,),
        ).fetchall()
        episode_names = expected_name_map(conn, title_id)
        expected_rows = conn.execute(
            """SELECT id, season, episode, tvdb_episode_id, imdb_id FROM expected_episodes
               WHERE title_id=? ORDER BY season, episode""",
            (title_id,),
        ).fetchall()
        episode_credit_rows = conn.execute(
            """SELECT e.season, e.episode, c.imdb_person_id, c.person_name,
                      c.role, c.billing_order
               FROM episode_credits c JOIN expected_episodes e ON e.id=c.expected_episode_id
               WHERE e.title_id=? ORDER BY e.season, e.episode, c.role, c.billing_order""",
            (title_id,),
        ).fetchall()
        episode_credit_map: dict[tuple[int, int], list] = {}
        for credit in episode_credit_rows:
            episode_credit_map.setdefault(
                (credit["season"], credit["episode"]), []
            ).append(credit)
        episode_tvdb_ids = {
            (row["season"], row["episode"]): row["tvdb_episode_id"]
            for row in expected_rows
        }
        season_totals: dict[int, int] = {}
        for expected in expected_rows:
            season_totals[expected["season"]] = season_totals.get(expected["season"], 0) + 1
        files = []
        for file_row in file_rows:
            file_view = dict(file_row)
            file_view["episode_name"] = merged_episode_name(
                episode_names, file_row["season"], file_row["episode_start"],
                file_row["episode_end"],
            )
            file_view["episode_tvdb_id"] = episode_tvdb_ids.get(
                (file_row["season"], file_row["episode_start"])
            )
            file_view["season_total"] = season_totals.get(file_row["season"])
            covered_credits = []
            if file_row["season"] is not None and file_row["episode_start"] is not None:
                final_episode = max(
                    file_row["episode_start"],
                    file_row["episode_end"] or file_row["episode_start"],
                )
                seen_credits = set()
                for episode_number in range(file_row["episode_start"], final_episode + 1):
                    for credit in episode_credit_map.get(
                        (file_row["season"], episode_number), []
                    ):
                        key = (credit["imdb_person_id"], credit["role"])
                        if key not in seen_credits:
                            seen_credits.add(key)
                            covered_credits.append(credit)
            file_view["episode_directors"] = [
                credit for credit in covered_credits if credit["role"] == "director"
            ]
            file_view["episode_writers"] = [
                credit for credit in covered_credits if credit["role"] == "writer"
            ]
            files.append(file_view)
        missing = conn.execute(
            """SELECT e.* FROM expected_episodes e WHERE e.title_id=? AND e.season > 0
               AND (e.aired IS NULL OR e.aired <= date('now')) AND NOT EXISTS (
                 SELECT 1 FROM files f WHERE f.title_id=e.title_id AND f.season=e.season
                 AND e.episode BETWEEN f.episode_start AND f.episode_end)
               ORDER BY e.season, e.episode""", (title_id,)
        ).fetchall()
        credit_rows = conn.execute(
            """SELECT imdb_person_id, person_name, role, billing_order
               FROM title_credits WHERE title_id=?
               ORDER BY CASE role WHEN 'director' THEN 1 WHEN 'actor' THEN 2 ELSE 3 END,
                        billing_order, person_name COLLATE NOCASE""",
            (title_id,),
        ).fetchall()
        title_state = conn.execute(
            """SELECT favorite, personal_rating, custom_order
               FROM user_title_state WHERE user_id=? AND title_id=?""",
            (request.state.user.id, title_id),
        ).fetchone() if request.state.user.id > 0 else None
        title_tags = conn.execute(
            """SELECT ut.id, ut.name, ut.color
               FROM user_tags ut JOIN title_tags tt ON tt.tag_id=ut.id
               WHERE ut.user_id=? AND tt.title_id=?
               ORDER BY ut.name COLLATE NOCASE""",
            (request.state.user.id, title_id),
        ).fetchall() if request.state.user.id > 0 else []
    missing_view = []
    show_name = title["metadata_title"] or title["title"]
    for episode in missing:
        query = f'{show_name} S{episode["season"]:02d}E{episode["episode"]:02d}'
        missing_view.append({**dict(episode), "query": query,
                             "search_url": provider_search_url(query)})
    seasons = sorted({row["season"] for row in files if row["season"] is not None})
    genres = [genre for genre in (title["genres"] or "").split(",") if genre]
    directors = [row for row in credit_rows if row["role"] == "director"]
    actors = [row for row in credit_rows if row["role"] == "actor"]
    writers = [row for row in credit_rows if row["role"] == "writer"]
    scan_at = title["last_scanned_at"] or title["root_last_scanned_at"]
    with imdb_genre_lock:
        active_title_ids = imdb_genre_job.get("title_ids")
        credit_update_active = (
            imdb_genre_job.get("status") in {"starting", "running"}
            and (active_title_ids is None or title_id in active_title_ids)
        )
    return templates.TemplateResponse(request, "detail.html", {
        "title": title, "files": files, "missing": missing_view,
        "seasons": seasons, "genres": genres,
        "directors": directors, "actors": actors, "writers": writers,
        "credit_update_active": credit_update_active,
        "scan_at": scan_at, "scan_stale": scan_is_stale(scan_at),
        "series_search_url": series_provider_search_url(title),
        "title_state": title_state, "title_tags": title_tags,
        "tvdb_enabled": bool(getattr(tvdb, "api_key", settings.tvdb_api_key)),
        "message": request.query_params.get("message", ""),
    })


@app.get("/titles/{title_id}/cover", response_class=HTMLResponse)
def title_cover(request: Request, title_id: int):
    with db.connect() as conn:
        title = conn.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
    if not title:
        raise HTTPException(404, "Title not found")
    provider_id = title["tvdb_id"] if title["kind"] == "tv" else title["tvdb_movie_id"]
    candidates: list[dict] = []
    error = ""
    if not provider_id:
        error = (
            "No alternate covers are available because this title is not matched to "
            "TheTVDB. Match the title first, then return here to choose its artwork."
        )
    else:
        try:
            record = (
                tvdb.series(provider_id)
                if title["kind"] == "tv"
                else tvdb.movie(provider_id)
            )
            candidates = poster_candidates(record)
        except TVDBError:
            error = (
                "InfoMancer could not load alternate covers from TheTVDB. Check the "
                "TVDB connection in Settings, then try again."
            )
    return templates.TemplateResponse(request, "cover.html", {
        "title": title, "candidates": candidates, "error": error,
        "message": request.query_params.get("message", ""),
    })


@app.post("/titles/{title_id}/cover")
def update_title_cover(
    title_id: int, poster_url: str = Form(...), return_to: str = Form(""),
):
    with db.connect() as conn:
        title = conn.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
    if not title:
        return redirect("/library", "The cover could not be changed because that title no longer exists.")
    provider_id = title["tvdb_id"] if title["kind"] == "tv" else title["tvdb_movie_id"]
    if not provider_id:
        return redirect(
            f"/titles/{title_id}/cover",
            "The cover could not be changed because this title is not matched to TheTVDB.",
        )
    try:
        record = (
            tvdb.series(provider_id)
            if title["kind"] == "tv"
            else tvdb.movie(provider_id)
        )
        valid_urls = {item["url"] for item in poster_candidates(record)}
    except TVDBError:
        return redirect(
            f"/titles/{title_id}/cover",
            "The cover could not be changed because TheTVDB could not be reached. Check the TVDB connection in Settings and try again.",
        )
    selected = poster_url.strip()
    if selected not in valid_urls:
        return redirect(
            f"/titles/{title_id}/cover",
            "That cover is no longer available from TheTVDB. Refresh the choices and select another cover.",
        )
    with db.connect() as conn:
        conn.execute(
            "UPDATE titles SET poster_url=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (selected, title_id),
        )
    record_event(
        "metadata",
        f"Cover changed for {title['metadata_title'] or title['title']}.",
        context={"title_id": title_id, "provider": "tvdb"},
    )
    return match_success_redirect(title_id, "Cover updated", return_to)


@app.post("/titles/{title_id}/media-info")
def inspect_title_media(title_id: int):
    global media_info_job
    with db.connect() as conn:
        title = conn.execute(
            "SELECT id, metadata_title, title FROM titles WHERE id=?", (title_id,)
        ).fetchone()
        if not title:
            return redirect(
                "/library",
                "Media inspection could not start because that title no longer exists.",
            )
        file_ids = [
            row["id"] for row in conn.execute(
                "SELECT id FROM files WHERE title_id=? ORDER BY id", (title_id,)
            ).fetchall()
        ]
    if not file_ids:
        return redirect(
            f"/titles/{title_id}",
            "Media inspection found no files for this title. Rescan its source, then try again.",
        )
    with media_info_lock:
        if media_info_job.get("status") in {"starting", "running"}:
            return redirect(
                f"/titles/{title_id}",
                "Media inspection is already running. Its progress is available in the task widget.",
            )
        media_info_job = {
            "status": "starting", "current": 0, "total": len(file_ids),
            "message": "Preparing media inspection",
        }
    threading.Thread(target=run_media_inspection, args=(file_ids,), daemon=True).start()
    record_event(
        "media",
        f"Media inspection requested for {title['metadata_title'] or title['title']}.",
        context={"title_id": title_id, "files": len(file_ids)},
    )
    return redirect(
        f"/titles/{title_id}",
        f"Media inspection started for {len(file_ids)} file{'s' if len(file_ids) != 1 else ''}. Progress is shown in the task widget.",
    )


@app.get("/titles/{title_id}/tvdb", response_class=HTMLResponse)
def tvdb_search(request: Request, title_id: int, q: str = ""):
    with db.connect() as conn:
        title = conn.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
    if not title:
        raise HTTPException(404, "Title not found")
    query = q or title["title"]
    try:
        if title["kind"] == "movie":
            raw_results = search_movies_broadly(query)
        elif query.strip().isdigit() or "thetvdb.com" in query.casefold():
            series_id = tvdb_series_id_from_reference(query)
            series = tvdb.series(series_id)
            first_aired = str(series.get("firstAired") or series.get("first_aired") or "")
            raw_results = [{
                **series,
                "tvdb_id": series_id,
                "image_url": poster_from(series),
                "year": first_aired[:4],
                "overview": series.get("overview") or "",
                "_direct_reference": True,
            }]
        else:
            raw_results = search_series_broadly(query)
        results = [
            {**result, "confidence": match_confidence(title["title"], title["year"], result)}
            for result in raw_results
        ]
        results.sort(
            key=lambda result: (
                bool(result.get("_direct_reference")),
                result["confidence"]["score"],
            ),
            reverse=True,
        )
    except TVDBError as exc:
        results = []
        error = str(exc)
    else:
        error = ""
    return templates.TemplateResponse(request, "tvdb.html", {
        "title": title, "q": query, "results": results, "error": error,
        "entity": "movie" if title["kind"] == "movie" else "series",
        "message": request.query_params.get("message", ""),
    })


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


@app.post("/titles/{title_id}/movie/{movie_id}")
def match_movie(
    title_id: int, movie_id: int, return_to: str = Form(""),
    match_origin: str = Form(""),
):
    try:
        provider = store_movie_match(title_id, movie_id)
    except (TVDBError, ValueError) as exc:
        return redirect(f"/titles/{title_id}", str(exc))
    return match_success_redirect(
        title_id, f"Movie matched using {provider}", return_to, match_origin,
    )


@app.get("/bulk-match", response_class=HTMLResponse)
def bulk_match_home(request: Request):
    with db.connect() as conn:
        counts = conn.execute(
            """SELECT
               (SELECT COUNT(*) FROM titles WHERE kind='movie' AND tvdb_movie_id IS NULL) movies,
               (SELECT COUNT(*) FROM titles WHERE kind='tv' AND tvdb_id IS NULL) shows"""
        ).fetchone()
    return templates.TemplateResponse(request, "bulk_match.html", {
        "counts": counts, "message": request.query_params.get("message", ""),
    })


@app.get("/shows/bulk-match", response_class=HTMLResponse)
def bulk_tv_match_review(
    request: Request, review: bool = False, offset: int = 0, selected: bool = False,
):
    with tv_match_lock:
        job = dict(tv_match_job)
    selected_ids = job.get("title_ids", []) if selected and job.get("mode") == "selected" else []
    direct_selection = bool(selected_ids)
    with db.connect() as conn:
        available = conn.execute(
            """SELECT t.*, r.label root_label, r.path root_path
               FROM titles t JOIN roots r ON r.id=t.root_id
               WHERE t.kind='tv' AND t.tvdb_id IS NULL
               ORDER BY COALESCE(t.metadata_title,t.title) COLLATE NOCASE"""
        ).fetchall()
        cached_count = conn.execute(
            """SELECT COUNT(*) FROM tv_match_suggestions s JOIN titles t ON t.id=s.title_id
               WHERE t.kind='tv' AND t.tvdb_id IS NULL"""
        ).fetchone()[0]
        if review and direct_selection:
            placeholders = ",".join("?" for _ in selected_ids)
            suggestion_rows = conn.execute(
                f"""SELECT t.*, r.label root_label, r.path root_path,
                           s.title_id suggestion_id, s.candidate_json,
                           s.confidence_score, s.confidence_label,
                           s.result_count, s.exact, s.error analysis_error
                    FROM titles t JOIN roots r ON r.id=t.root_id
                    LEFT JOIN tv_match_suggestions s ON s.title_id=t.id
                    WHERE t.kind='tv' AND t.tvdb_id IS NULL
                      AND t.id IN ({placeholders})
                    ORDER BY COALESCE(t.metadata_title,t.title) COLLATE NOCASE
                    LIMIT 50 OFFSET ?""",
                [*selected_ids, max(0, offset)],
            ).fetchall()
            cached_count = len(selected_ids)
        else:
            suggestion_rows = conn.execute(
                """SELECT t.*, r.label root_label, r.path root_path,
                          s.title_id suggestion_id, s.candidate_json,
                          s.confidence_score, s.confidence_label,
                          s.result_count, s.exact, s.error analysis_error
                   FROM tv_match_suggestions s JOIN titles t ON t.id=s.title_id
                   JOIN roots r ON r.id=t.root_id WHERE t.kind='tv' AND t.tvdb_id IS NULL
                   ORDER BY s.analyzed_at, COALESCE(t.metadata_title,t.title) COLLATE NOCASE
                   LIMIT 50 OFFSET ?""", (max(0, offset),)
            ).fetchall() if review else []
    suggestions = []
    for row in suggestion_rows:
        suggestions.append({
            "title": row,
            "candidate": json.loads(row["candidate_json"]) if row["candidate_json"] else None,
            "confidence": ({"score": row["confidence_score"], "label": row["confidence_label"]}
                           if row["confidence_score"] is not None else None),
            "exact": bool(row["exact"]), "result_count": row["result_count"],
            "error": row["analysis_error"], "pending": row["suggestion_id"] is None,
        })
    return templates.TemplateResponse(request, "bulk_tv_match.html", {
        "shows": available, "available_count": len(available), "suggestions": suggestions,
        "analyzed": review, "cached_count": cached_count, "offset": max(0, offset),
        "job": job, "direct_selection": direct_selection,
        "message": request.query_params.get("message", ""),
    })


@app.post("/shows/bulk-match/analyze")
def start_bulk_tv_analysis(mode: str = Form("selected"), selected: list[int] = Form(default=[])):
    with tv_match_lock:
        if tv_match_job.get("status") in {"starting", "running"}:
            return redirect("/shows/bulk-match", "TV series analysis is already running")
    selected_ids = list(dict.fromkeys(selected))
    with db.connect() as conn:
        if mode == "all":
            rows = conn.execute(
                """SELECT t.id FROM titles t LEFT JOIN tv_match_suggestions s ON s.title_id=t.id
                   WHERE t.kind='tv' AND t.tvdb_id IS NULL AND s.title_id IS NULL ORDER BY t.title COLLATE NOCASE"""
            ).fetchall()
        elif mode == "next":
            rows = conn.execute(
                """SELECT t.id FROM titles t LEFT JOIN tv_match_suggestions s ON s.title_id=t.id
                   WHERE t.kind='tv' AND t.tvdb_id IS NULL AND s.title_id IS NULL
                   ORDER BY t.title COLLATE NOCASE LIMIT 20"""
            ).fetchall()
        else:
            if not selected_ids:
                return redirect("/shows/bulk-match", "Select at least one TV series")
            placeholders = ",".join("?" for _ in selected_ids)
            rows = conn.execute(
                f"SELECT id FROM titles WHERE kind='tv' AND tvdb_id IS NULL AND id IN ({placeholders}) ORDER BY title COLLATE NOCASE",
                selected_ids,
            ).fetchall()
    title_ids = [row["id"] for row in rows]
    if not title_ids:
        message = "No unmatched selected TV series remain" if mode == "selected" else "No unanalyzed TV series remain"
        return redirect("/shows/bulk-match?review=true", message)
    with tv_match_lock:
        tv_match_job.clear()
        tv_match_job.update({"status": "starting", "total": len(title_ids), "processed": 0, "matched": 0, "errors": 0, "mode": mode, "title_ids": title_ids})
    threading.Thread(target=run_tv_match_analysis, args=(title_ids,), daemon=True).start()
    destination = "/shows/bulk-match?review=true&selected=true" if mode == "selected" else "/shows/bulk-match"
    return redirect(destination, f"Finding matches for {len(title_ids):,} selected TV series" if mode == "selected" else f"Background analysis started for {len(title_ids):,} TV series")


@app.post("/shows/bulk-match")
def bulk_tv_match_apply(
    matches: list[str] = Form(default=[]), selected_scope: str = Form(""),
):
    applied = failed = 0
    for value in matches[:50]:
        try:
            title_id, series_id = (int(part) for part in value.split(":", 1))
            store_tv_match(title_id, series_id)
            with db.connect() as conn:
                conn.execute("DELETE FROM tv_match_suggestions WHERE title_id=?", (title_id,))
            applied += 1
        except (ValueError, TVDBError):
            failed += 1
    message = f"Matched {applied} TV series"
    if failed:
        message += f"; {failed} failed"
    destination = "/shows/bulk-match?review=true&selected=true" if selected_scope else "/shows/bulk-match?review=true"
    return redirect(destination, message)


@app.get("/movies/bulk-match", response_class=HTMLResponse)
def bulk_movie_match_review(
    request: Request, review: bool = False, offset: int = 0, selected: bool = False,
):
    with movie_match_lock:
        job = dict(movie_match_job)
    selected_ids = job.get("title_ids", []) if selected and job.get("mode") == "selected" else []
    direct_selection = bool(selected_ids)
    with db.connect() as conn:
        available = conn.execute(
            """SELECT t.*, r.label root_label, r.path root_path
               FROM titles t JOIN roots r ON r.id=t.root_id
               WHERE t.kind='movie' AND t.tvdb_movie_id IS NULL
               ORDER BY COALESCE(t.metadata_title,t.title) COLLATE NOCASE"""
        ).fetchall()
        cached_count = conn.execute(
            """SELECT COUNT(*) FROM movie_match_suggestions s
               JOIN titles t ON t.id=s.title_id
                   WHERE t.kind='movie'"""
        ).fetchone()[0]
        no_result_count = conn.execute(
            """SELECT COUNT(*) FROM movie_match_suggestions s
               JOIN titles t ON t.id=s.title_id
               WHERE t.kind='movie' AND t.tvdb_movie_id IS NULL
                 AND s.candidate_json IS NULL"""
        ).fetchone()[0]
        suggestion_rows = []
        if review:
            if direct_selection:
                placeholders = ",".join("?" for _ in selected_ids)
                suggestion_rows = conn.execute(
                    f"""SELECT t.*, r.label root_label, r.path root_path,
                               s.title_id suggestion_id, s.candidate_json,
                               s.confidence_score, s.confidence_label,
                               s.result_count, s.exact, s.error analysis_error
                        FROM titles t JOIN roots r ON r.id=t.root_id
                        LEFT JOIN movie_match_suggestions s ON s.title_id=t.id
                        WHERE t.kind='movie' AND t.tvdb_movie_id IS NULL
                          AND t.tmdb_id IS NULL AND t.imdb_id IS NULL
                          AND t.id IN ({placeholders})
                        ORDER BY COALESCE(t.metadata_title,t.title) COLLATE NOCASE
                        LIMIT 50 OFFSET ?""",
                    [*selected_ids, max(0, offset)],
                ).fetchall()
                cached_count = len(selected_ids)
            else:
                suggestion_rows = conn.execute(
                    """SELECT t.*, r.label root_label, r.path root_path,
                              s.title_id suggestion_id, s.candidate_json,
                              s.confidence_score, s.confidence_label,
                              s.result_count, s.exact, s.error analysis_error
                       FROM movie_match_suggestions s
                       JOIN titles t ON t.id=s.title_id
                       JOIN roots r ON r.id=t.root_id
                       WHERE t.kind='movie'
                       ORDER BY s.analyzed_at, COALESCE(t.metadata_title,t.title) COLLATE NOCASE
                       LIMIT 50 OFFSET ?""",
                    (max(0, offset),),
                ).fetchall()
    suggestions = []
    for row in suggestion_rows:
        candidate = json.loads(row["candidate_json"]) if row["candidate_json"] else None
        confidence = None
        if row["confidence_score"] is not None:
            confidence = {"score": row["confidence_score"], "label": row["confidence_label"]}
        suggestions.append({
            "title": row, "candidate": candidate, "confidence": confidence,
            "exact": bool(row["exact"]), "result_count": row["result_count"],
            "error": row["analysis_error"], "pending": row["suggestion_id"] is None,
        })
    return templates.TemplateResponse(request, "bulk_movie_match.html", {
        "movies": available, "available_count": len(available),
        "suggestions": suggestions, "analyzed": review, "error": "",
        "cached_count": cached_count, "no_result_count": no_result_count,
        "offset": max(0, offset), "job": job,
        "direct_selection": direct_selection,
    })


@app.post("/movies/bulk-match/analyze")
def start_bulk_movie_analysis(
    mode: str = Form("selected"), selected: list[int] = Form(default=[]),
):
    with movie_match_lock:
        if movie_match_job.get("status") in {"starting", "running"}:
            return redirect("/movies/bulk-match", "Movie analysis is already running")
    selected_ids = list(dict.fromkeys(selected))
    with db.connect() as conn:
        if mode == "all":
            rows = conn.execute(
                """SELECT t.id FROM titles t
                   LEFT JOIN movie_match_suggestions s ON s.title_id=t.id
                   WHERE t.kind='movie' AND t.tvdb_movie_id IS NULL AND s.title_id IS NULL
                   ORDER BY COALESCE(t.metadata_title,t.title) COLLATE NOCASE"""
            ).fetchall()
        elif mode == "no_results":
            rows = conn.execute(
                """SELECT t.id FROM movie_match_suggestions s
                   JOIN titles t ON t.id=s.title_id
                   WHERE t.kind='movie' AND t.tvdb_movie_id IS NULL
                     AND s.candidate_json IS NULL
                   ORDER BY COALESCE(t.metadata_title,t.title) COLLATE NOCASE"""
            ).fetchall()
        elif mode == "next":
            rows = conn.execute(
                """SELECT t.id FROM titles t
                   LEFT JOIN movie_match_suggestions s ON s.title_id=t.id
                   WHERE t.kind='movie' AND t.tvdb_movie_id IS NULL AND s.title_id IS NULL
                   ORDER BY COALESCE(t.metadata_title,t.title) COLLATE NOCASE LIMIT 20"""
            ).fetchall()
        else:
            if not selected_ids:
                return redirect("/movies/bulk-match", "Select at least one movie")
            placeholders = ",".join("?" for _ in selected_ids)
            rows = conn.execute(
                f"""SELECT id FROM titles WHERE kind='movie'
                      AND tvdb_movie_id IS NULL AND tmdb_id IS NULL AND imdb_id IS NULL
                      AND id IN ({placeholders}) ORDER BY title COLLATE NOCASE""",
                selected_ids,
            ).fetchall()
    title_ids = [row["id"] for row in rows]
    if not title_ids:
        message = "No unmatched selected movies remain" if mode == "selected" else "No unanalyzed movies remain"
        return redirect("/movies/bulk-match?review=true", message)
    with movie_match_lock:
        movie_match_job.clear()
        movie_match_job.update({
            "status": "starting", "total": len(title_ids), "processed": 0,
            "matched": 0, "errors": 0, "mode": mode, "title_ids": title_ids,
        })
    threading.Thread(target=run_movie_match_analysis, args=(title_ids,), daemon=True).start()
    destination = "/movies/bulk-match?review=true&selected=true" if mode == "selected" else "/movies/bulk-match"
    return redirect(destination, f"Finding matches for {len(title_ids):,} selected movies" if mode == "selected" else f"Background analysis started for {len(title_ids):,} movies")


@app.post("/movies/bulk-match")
def bulk_movie_match_apply(
    matches: list[str] = Form(default=[]), selected_scope: str = Form(""),
):
    applied, failed = 0, 0
    for value in matches[:50]:
        try:
            title_id, movie_id = (int(part) for part in value.split(":", 1))
            store_movie_match(title_id, movie_id)
            with db.connect() as conn:
                conn.execute("DELETE FROM movie_match_suggestions WHERE title_id=?", (title_id,))
            applied += 1
        except (ValueError, TVDBError):
            failed += 1
    message = f"Matched {applied} movies"
    if failed:
        message += f"; {failed} failed"
    destination = "/movies/bulk-match?review=true&selected=true" if selected_scope else "/movies/bulk-match?review=true"
    return redirect(destination, message)


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
               poster_url=?, imdb_id=?, imdb_checked_at=CURRENT_TIMESTAMP,
               matched_at=CURRENT_TIMESTAMP,
               updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (series_id, metadata_title, title_language or None, metadata_year,
             metadata_end_year, metadata_continuing, metadata_status,
             poster_from(series), imdb_id, title_id),
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


@app.post("/titles/{title_id}/tvdb/{series_id}")
def match_tvdb(
    title_id: int, series_id: int, return_to: str = Form(""),
    match_origin: str = Form(""),
):
    try:
        episode_count = store_tv_match(title_id, series_id)
    except TVDBError as exc:
        return redirect(f"/titles/{title_id}", str(exc))
    return match_success_redirect(
        title_id, f"Matched to TVDB and loaded {episode_count} episodes",
        return_to, match_origin,
    )


@app.post("/titles/{title_id}/tvdb-manual")
def match_tvdb_manual(
    title_id: int, tvdb_reference: str = Form(""), return_to: str = Form(""),
    match_origin: str = Form(""),
):
    with db.connect() as conn:
        title = conn.execute(
            "SELECT id, kind FROM titles WHERE id=?", (title_id,)
        ).fetchone()
    if not title:
        raise HTTPException(404, "Title not found")
    if title["kind"] != "tv":
        return redirect(f"/titles/{title_id}/tvdb", "Manual TVDB links currently support TV series")
    try:
        series_id = tvdb_series_id_from_reference(tvdb_reference)
        episode_count = store_tv_match(title_id, series_id)
    except (TVDBError, ValueError) as exc:
        return redirect(f"/titles/{title_id}/tvdb", str(exc))
    return match_success_redirect(
        title_id,
        f"Matched to TVDB {series_id} and loaded {episode_count} episodes",
        return_to, match_origin,
    )


@app.post("/titles/{title_id}/unmatch")
def unmatch_title(title_id: int):
    with db.connect() as conn:
        title = conn.execute("SELECT id FROM titles WHERE id=?", (title_id,)).fetchone()
        if not title:
            raise HTTPException(404, "Title not found")
        conn.execute("DELETE FROM expected_episodes WHERE title_id=?", (title_id,))
        conn.execute(
            """UPDATE titles SET tvdb_id=NULL, tvdb_movie_id=NULL, tmdb_id=NULL,
               imdb_id=NULL, imdb_checked_at=NULL, genres=NULL,
               imdb_title_type=NULL, imdb_rating=NULL, imdb_votes=NULL,
               poster_url=NULL, metadata_title=NULL, metadata_year=NULL,
               metadata_title_language=NULL,
               metadata_end_year=NULL, metadata_continuing=NULL,
               metadata_status=NULL, matched_at=NULL,
               updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (title_id,),
        )
    return redirect(f"/titles/{title_id}", "Match metadata removed; media files were unchanged")


@app.get("/titles/{title_id}/rename-folder", response_class=HTMLResponse)
def rename_folder_preview(request: Request, title_id: int):
    with db.connect() as conn:
        title = conn.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
    if not title or not title["tvdb_id"]:
        return redirect(f"/titles/{title_id}", "Match this show to TVDB first")
    source = Path(title["folder_path"])
    continuing = title["metadata_continuing"] if title["metadata_continuing"] is not None else title["continuing"]
    new_name = plex_show_folder(
        title["metadata_title"] or title["title"], title["metadata_year"] or title["year"],
        title["tvdb_id"], title["metadata_end_year"] or title["end_year"], continuing,
    )
    destination = source.with_name(new_name)
    return templates.TemplateResponse(request, "rename.html", {
        "title": title, "source": source, "destination": destination,
        "action": f"/titles/{title_id}/rename-folder", "kind": "folder",
    })


@app.post("/titles/{title_id}/rename-folder")
def rename_folder(title_id: int, confirm: str = Form("")):
    if confirm != "RENAME":
        return redirect(f"/titles/{title_id}", "Rename cancelled: confirmation did not match")
    with db.connect() as conn:
        title = conn.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
        if not title or not title["tvdb_id"]:
            raise HTTPException(400, "TVDB match required")
        source = Path(title["folder_path"])
        continuing = title["metadata_continuing"] if title["metadata_continuing"] is not None else title["continuing"]
        new_name = plex_show_folder(
            title["metadata_title"] or title["title"], title["metadata_year"] or title["year"],
            title["tvdb_id"], title["metadata_end_year"] or title["end_year"], continuing,
        )
        destination = contained_destination(source, new_name)
        if destination == source:
            return redirect(f"/titles/{title_id}", "Folder already follows the Plex format")
        if destination.exists():
            return redirect(f"/titles/{title_id}", f"Destination already exists: {destination}")
        try:
            source.rename(destination)
        except OSError as exc:
            record_event(
                "filesystem", f"Show folder could not be renamed: {source.name}.",
                level="error", detail=str(exc),
                context={"title_id": title_id, "source": str(source), "destination": str(destination)},
            )
            return redirect(
                f"/titles/{title_id}",
                "The show folder could not be renamed. Check that the folder still exists and InfoMancer has permission to change it, then try again. No catalog paths were changed.",
            )
        old_prefix, new_prefix = str(source), str(destination)
        conn.execute("UPDATE titles SET folder_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (new_prefix, title_id))
        rows = conn.execute("SELECT id, path FROM files WHERE title_id=?", (title_id,)).fetchall()
        for row in rows:
            new_path = new_prefix + row["path"][len(old_prefix):]
            conn.execute("UPDATE files SET path=? WHERE id=?", (new_path, row["id"]))
    record_event(
        "filesystem", f"Show folder renamed from {source.name} to {destination.name}.",
        context={"title_id": title_id, "source": str(source), "destination": str(destination)},
    )
    return redirect(f"/titles/{title_id}", "Show folder renamed")


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


@app.get("/titles/{title_id}/rename-episodes", response_class=HTMLResponse)
def bulk_rename_preview(request: Request, title_id: int):
    with db.connect() as conn:
        title, proposals = episode_rename_proposals(conn, title_id)
    if not title:
        raise HTTPException(404, "Title not found")
    if not title["tvdb_id"]:
        return redirect(f"/titles/{title_id}", "Match this show to TVDB first")
    return templates.TemplateResponse(request, "bulk_rename.html", {
        "title": title, "proposals": proposals,
        "ready": sum(item["status"] == "ready" for item in proposals),
        "conflicts": sum(item["status"] in {"conflict", "missing"} for item in proposals),
    })


@app.post("/titles/{title_id}/rename-episodes")
def bulk_rename_apply(
    title_id: int, selected_file_ids: list[int] = Form(default=[]),
):
    selected = set(selected_file_ids)
    if not selected:
        return redirect(
            f"/titles/{title_id}/rename-episodes",
            "Select at least one episode file to rename",
        )
    renamed = 0
    skipped = 0
    with db.connect() as conn:
        title, proposals = episode_rename_proposals(conn, title_id)
        if not title:
            raise HTTPException(404, "Title not found")
        for proposal in proposals:
            if proposal["file_id"] not in selected:
                continue
            if proposal["status"] != "ready":
                skipped += 1
                continue
            source, destination = proposal["source"], proposal["destination"]
            try:
                if destination.exists():
                    skipped += 1
                    continue
                source.rename(destination)
                conn.execute(
                    "UPDATE files SET path=?, filename=? WHERE id=?",
                    (str(destination), destination.name, proposal["file_id"]),
                )
                renamed += 1
            except OSError as exc:
                skipped += 1
                record_event(
                    "filesystem", f"Episode file could not be renamed: {source.name}.",
                    level="error", detail=str(exc),
                    context={"title_id": title_id, "source": str(source), "destination": str(destination)},
                )
    message = f"Renamed {renamed} selected episode files"
    if skipped:
        message += f"; skipped {skipped} conflicts or missing files"
    record_event(
        "filesystem", message + ".",
        level="warning" if skipped else "info",
        context={"title_id": title_id, "renamed": renamed, "skipped": skipped},
    )
    return redirect(f"/titles/{title_id}", message)


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


@app.get("/titles/{title_id}/restore-filenames", response_class=HTMLResponse)
def restore_filenames_preview(request: Request, title_id: int):
    with db.connect() as conn:
        title, proposals = restore_filename_proposals(conn, title_id)
    if not title:
        raise HTTPException(404, "Title not found")
    return templates.TemplateResponse(request, "restore_filenames.html", {
        "title": title, "proposals": proposals,
        "ready": sum(item["status"] == "ready" for item in proposals),
        "conflicts": sum(item["status"] in {"conflict", "missing"} for item in proposals),
    })


@app.post("/titles/{title_id}/restore-filenames")
def restore_filenames_apply(title_id: int):
    restored = 0
    skipped = 0
    with db.connect() as conn:
        title, proposals = restore_filename_proposals(conn, title_id)
        if not title:
            raise HTTPException(404, "Title not found")
        for proposal in proposals:
            if proposal["status"] != "ready":
                skipped += proposal["status"] != "unchanged"
                continue
            try:
                proposal["source"].rename(proposal["destination"])
                conn.execute(
                    "UPDATE files SET path=?, filename=? WHERE id=?",
                    (str(proposal["destination"]), proposal["destination"].name,
                     proposal["file_id"]),
                )
                restored += 1
            except OSError as exc:
                skipped += 1
                record_event(
                    "filesystem",
                    f"Original filename could not be restored for {proposal['source'].name}.",
                    level="error", detail=str(exc),
                    context={"title_id": title_id, "source": str(proposal["source"])},
                )
    message = f"Restored {restored} original filenames"
    if skipped:
        message += f"; skipped {skipped} conflicts or missing files"
    record_event(
        "filesystem", message + ".",
        level="warning" if skipped else "info",
        context={"title_id": title_id, "restored": restored, "skipped": skipped},
    )
    return redirect(f"/titles/{title_id}", message)


@app.get("/files/{file_id}/rename", response_class=HTMLResponse)
def rename_file_preview(request: Request, file_id: int):
    with db.connect() as conn:
        row = conn.execute(
            """SELECT f.*, t.title, t.metadata_title, t.year, t.metadata_year,
               t.id matched_title_id FROM files f JOIN titles t ON t.id=f.title_id
               WHERE f.id=?""", (file_id,)
        ).fetchone()
        episode_name = merged_episode_name(
            expected_name_map(conn, row["matched_title_id"]), row["season"],
            row["episode_start"], row["episode_end"],
        ) if row else ""
    if not row or row["season"] is None or row["episode_start"] is None:
        raise HTTPException(400, "This file has no parsed SxxExx identifier")
    new_name = plex_episode_filename(
        row["metadata_title"] or row["title"], row["metadata_year"] or row["year"],
        row["season"], row["episode_start"], episode_name, row["extension"],
        row["episode_end"],
    )
    source = Path(row["path"])
    return templates.TemplateResponse(request, "rename.html", {
        "title": row, "source": source, "destination": source.with_name(new_name),
        "action": f"/files/{file_id}/rename", "kind": "file",
    })


@app.post("/files/{file_id}/rename")
def rename_file(file_id: int):
    with db.connect() as conn:
        row = conn.execute(
            """SELECT f.*, t.title, t.metadata_title, t.year, t.metadata_year,
               t.id matched_title_id FROM files f JOIN titles t ON t.id=f.title_id
               WHERE f.id=?""", (file_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "File not found")
        episode_name = merged_episode_name(
            expected_name_map(conn, row["matched_title_id"]), row["season"],
            row["episode_start"], row["episode_end"],
        )
        new_name = plex_episode_filename(
            row["metadata_title"] or row["title"], row["metadata_year"] or row["year"],
            row["season"], row["episode_start"], episode_name, row["extension"],
            row["episode_end"],
        )
        source = Path(row["path"])
        destination = contained_destination(source, new_name)
        if destination.exists() and destination != source:
            return redirect(f"/titles/{row['title_id']}", f"Destination already exists: {destination}")
        if destination != source:
            try:
                source.rename(destination)
            except OSError as exc:
                record_event(
                    "filesystem", f"Episode file could not be renamed: {source.name}.",
                    level="error", detail=str(exc),
                    context={"file_id": file_id, "source": str(source), "destination": str(destination)},
                )
                return redirect(
                    f"/titles/{row['title_id']}",
                    "The episode could not be renamed. Check that the file still exists and InfoMancer has permission to change it, then try again. The catalog was not changed.",
                )
            conn.execute("UPDATE files SET path=?, filename=? WHERE id=?", (str(destination), destination.name, file_id))
            record_event(
                "filesystem", f"Episode file renamed to {destination.name}.",
                context={"file_id": file_id, "source": str(source), "destination": str(destination)},
            )
    return redirect(f"/titles/{row['title_id']}", "Episode renamed")


@app.get("/files/{file_id}/rename-movie", response_class=HTMLResponse)
def rename_movie_preview(request: Request, file_id: int):
    with db.connect() as conn:
        row = conn.execute(
            """SELECT f.*, t.title, t.metadata_title, t.year, t.metadata_year,
               t.tmdb_id, t.imdb_id, t.kind title_kind, t.folder_path
               FROM files f JOIN titles t ON t.id=f.title_id WHERE f.id=?""",
            (file_id,),
        ).fetchone()
    if not row or row["title_kind"] != "movie" or not (row["tmdb_id"] or row["imdb_id"]):
        raise HTTPException(400, "Match this movie before renaming it")
    new_name = plex_movie_filename(
        row["metadata_title"] or row["title"], row["metadata_year"] or row["year"],
        row["extension"], row["tmdb_id"] or "", row["imdb_id"] or "",
    )
    source = Path(row["path"])
    return templates.TemplateResponse(request, "rename.html", {
        "title": row, "source": source, "destination": source.with_name(new_name),
        "action": f"/files/{file_id}/rename-movie", "kind": "movie-file",
    })


@app.post("/files/{file_id}/rename-movie")
def rename_movie(file_id: int):
    with db.connect() as conn:
        row = conn.execute(
            """SELECT f.*, t.title, t.metadata_title, t.year, t.metadata_year,
               t.tmdb_id, t.imdb_id, t.kind title_kind, t.folder_path
               FROM files f JOIN titles t ON t.id=f.title_id WHERE f.id=?""",
            (file_id,),
        ).fetchone()
        if not row or row["title_kind"] != "movie":
            raise HTTPException(404, "Movie file not found")
        new_name = plex_movie_filename(
            row["metadata_title"] or row["title"], row["metadata_year"] or row["year"],
            row["extension"], row["tmdb_id"] or "", row["imdb_id"] or "",
        )
        source = Path(row["path"])
        destination = contained_destination(source, new_name)
        if destination.exists() and destination != source:
            return redirect(f"/titles/{row['title_id']}", f"Destination already exists: {destination}")
        if destination != source:
            try:
                source.rename(destination)
            except OSError as exc:
                record_event(
                    "filesystem", f"Movie file could not be renamed: {source.name}.",
                    level="error", detail=str(exc),
                    context={"file_id": file_id, "source": str(source), "destination": str(destination)},
                )
                return redirect(
                    f"/titles/{row['title_id']}",
                    "The movie could not be renamed. Check that the file still exists and InfoMancer has permission to change it, then try again. The catalog was not changed.",
                )
            conn.execute(
                "UPDATE files SET path=?, filename=? WHERE id=?",
                (str(destination), destination.name, file_id),
            )
            if row["folder_path"] == str(source):
                conn.execute(
                    "UPDATE titles SET folder_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (str(destination), row["title_id"]),
                )
            record_event(
                "filesystem", f"Movie file renamed to {destination.name}.",
                context={"file_id": file_id, "source": str(source), "destination": str(destination)},
            )
    return redirect(f"/titles/{row['title_id']}", "Movie file renamed")

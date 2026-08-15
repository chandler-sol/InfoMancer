from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)


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
        sandbox=os.getenv("INFOMANCER_SANDBOX", "").strip().casefold()
        in {"1", "true", "yes", "on"},
        bootstrap_token=os.getenv("INFOMANCER_BOOTSTRAP_TOKEN", "").strip(),
    )

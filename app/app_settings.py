from __future__ import annotations

from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .db import Database


class AppSettingError(ValueError):
    pass


class AppSettings:
    EDITABLE_KEYS = {
        "installation_name",
        "timezone",
        "default_library_view",
        "default_cover_size",
        "search_provider_name",
        "search_url_template",
        "log_level",
    }

    def __init__(self, database: Database, environment_search_url: str) -> None:
        self.database = database
        parsed = urlparse(environment_search_url)
        provider = (parsed.hostname or "External search").removeprefix("www.")
        self.defaults = {
            "installation_name": "InfoMancer",
            "timezone": "UTC",
            "default_library_view": "list",
            "default_cover_size": "180",
            "search_provider_name": provider or "External search",
            "search_url_template": environment_search_url,
            "log_level": "info",
        }

    def get(self, key: str) -> str:
        if key not in self.defaults:
            raise KeyError(key)
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row else self.defaults[key]

    def values(self) -> dict[str, str]:
        values = dict(self.defaults)
        with self.database.connect() as conn:
            rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        values.update({row["key"]: row["value"] for row in rows if row["key"] in values})
        return values

    def validate_general(
        self, installation_name: str, timezone_name: str,
        default_library_view: str, default_cover_size: str,
    ) -> dict[str, str]:
        name = " ".join(installation_name.strip().split())
        if not 1 <= len(name) <= 50:
            raise AppSettingError("Installation name must contain between 1 and 50 characters.")
        timezone_name = timezone_name.strip()
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise AppSettingError(
                f'"{timezone_name or "blank"}" is not a recognized IANA time zone. '
                'Use a value such as "America/New_York" or "Europe/London".'
            )
        view = default_library_view.strip().casefold()
        if view not in {"list", "covers"}:
            raise AppSettingError("Default library view must be List or Covers.")
        try:
            cover_size = int(default_cover_size)
        except ValueError as exc:
            raise AppSettingError("Default cover size must be a number from 120 to 300 pixels.") from exc
        if not 120 <= cover_size <= 300:
            raise AppSettingError("Default cover size must be between 120 and 300 pixels.")
        return {
            "installation_name": name,
            "timezone": timezone_name,
            "default_library_view": view,
            "default_cover_size": str(cover_size),
        }

    def validate_external_search(
        self, provider_name: str, url_template: str,
    ) -> dict[str, str]:
        name = " ".join(provider_name.strip().split())
        if not 1 <= len(name) <= 50:
            raise AppSettingError("Search provider name must contain between 1 and 50 characters.")
        template = url_template.strip()
        if template.count("{query}") != 1:
            raise AppSettingError(
                'Search URL must contain exactly one "{query}" placeholder so InfoMancer knows where to insert the title or episode.'
            )
        parsed = urlparse(template.replace("{query}", "test"))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise AppSettingError(
                "Search URL must be a complete HTTP or HTTPS address, including the website name."
            )
        return {
            "search_provider_name": name,
            "search_url_template": template,
        }

    def validate_logging(self, log_level: str) -> dict[str, str]:
        level = log_level.strip().casefold()
        if level not in {"info", "verbose", "debug"}:
            raise AppSettingError("Choose Standard, Verbose, or Debug logging.")
        return {"log_level": level}

    def update(self, values: dict[str, str], changed_by: int | None) -> int:
        unknown = set(values) - self.EDITABLE_KEYS
        if unknown:
            raise AppSettingError("InfoMancer received an unsupported setting and made no changes.")
        actor = changed_by if changed_by and changed_by > 0 else None
        changed = 0
        with self.database.connect() as conn:
            existing = {
                row["key"]: row["value"]
                for row in conn.execute(
                    f"SELECT key, value FROM app_settings WHERE key IN ({','.join('?' for _ in values)})",
                    tuple(values),
                )
            } if values else {}
            for key, new_value in values.items():
                old_value = existing.get(key, self.defaults[key])
                if old_value == new_value:
                    continue
                conn.execute(
                    """INSERT INTO app_settings(key,value,updated_by,updated_at)
                       VALUES (?,?,?,CURRENT_TIMESTAMP)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                         updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",
                    (key, new_value, actor),
                )
                conn.execute(
                    """INSERT INTO app_setting_changes
                       (key,old_value,new_value,changed_by) VALUES (?,?,?,?)""",
                    (key, old_value, new_value, actor),
                )
                changed += 1
        return changed

    def history(self, limit: int = 20):
        with self.database.connect() as conn:
            return conn.execute(
                """SELECT c.*, COALESCE(u.display_name, 'System') changed_by_name
                   FROM app_setting_changes c
                   LEFT JOIN users u ON u.id=c.changed_by
                   ORDER BY c.id DESC LIMIT ?""",
                (max(1, min(limit, 100)),),
            ).fetchall()

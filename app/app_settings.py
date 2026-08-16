from __future__ import annotations

from datetime import datetime
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
        "default_season_display",
        "search_provider_name",
        "search_url_template",
        "log_level",
        "trash_retention_days",
        "lockdown_mode",
        "read_only_mode",
        "hash_mode",
        "hash_immediate_limit",
        "hash_schedule_frequency",
        "hash_schedule_day",
        "hash_schedule_time",
        "hash_io_intensity",
        "hash_pause_for_activity",
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
            "default_season_display": "collapsed",
            "search_provider_name": provider or "External search",
            "search_url_template": environment_search_url,
            "log_level": "info",
            "trash_retention_days": "30",
            "lockdown_mode": "0",
            "read_only_mode": "0",
            "hash_mode": "automatic",
            "hash_immediate_limit": "200",
            "hash_schedule_frequency": "weekly",
            "hash_schedule_day": "6",
            "hash_schedule_time": "03:00",
            "hash_io_intensity": "low",
            "hash_pause_for_activity": "1",
            "hash_last_scheduled_at": "",
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

    def validate_season_display(self, value: str) -> dict[str, str]:
        display = value.strip().casefold()
        if display not in {"collapsed", "expanded"}:
            raise AppSettingError("Choose Collapsed or Expanded for the default TV season display.")
        return {"default_season_display": display}

    def file_protection_mode(self) -> str:
        if self.get("read_only_mode") == "1":
            return "readonly"
        if self.get("lockdown_mode") == "1":
            return "lockdown"
        return "standard"

    def validate_safety(self, protection_mode: str) -> dict[str, str]:
        mode = protection_mode.strip().casefold().replace("-", "_")
        aliases = {"0": "standard", "1": "lockdown", "read_only": "readonly"}
        mode = aliases.get(mode, mode)
        if mode not in {"readonly", "standard", "lockdown"}:
            raise AppSettingError(
                "Choose Read-Only Mode, Standard Mode, or Lockdown Mode."
            )
        return {
            "read_only_mode": "1" if mode == "readonly" else "0",
            "lockdown_mode": "1" if mode == "lockdown" else "0",
        }

    def validate_hashing(
        self, mode: str, immediate_limit: str, frequency: str,
        schedule_day: str, schedule_time: str, intensity: str,
        pause_for_activity: str,
    ) -> dict[str, str]:
        mode = mode.strip().casefold()
        frequency = frequency.strip().casefold()
        intensity = intensity.strip().casefold()
        if mode not in {"automatic", "scheduled", "on_demand", "off"}:
            raise AppSettingError("Choose Automatic, Scheduled, On demand, or Off hashing.")
        try:
            limit = int(immediate_limit)
            day = int(schedule_day)
        except ValueError as exc:
            raise AppSettingError("The hashing limit and schedule day must be numbers.") from exc
        if not 1 <= limit <= 10_000:
            raise AppSettingError("The immediate hashing limit must be between 1 and 10,000 files.")
        if frequency not in {"daily", "weekly", "monthly"}:
            raise AppSettingError("Choose a daily, weekly, or monthly hashing schedule.")
        if frequency == "weekly" and not 0 <= day <= 6:
            raise AppSettingError("Choose a valid day of the week for hashing.")
        if frequency == "monthly" and not 1 <= day <= 28:
            raise AppSettingError("Choose a monthly hashing day from 1 through 28.")
        try:
            datetime.strptime(schedule_time.strip(), "%H:%M")
        except ValueError as exc:
            raise AppSettingError("Choose a valid hashing time.") from exc
        if intensity not in {"low", "balanced", "high"}:
            raise AppSettingError("Choose Low, Balanced, or High hashing intensity.")
        return {
            "hash_mode": mode,
            "hash_immediate_limit": str(limit),
            "hash_schedule_frequency": frequency,
            "hash_schedule_day": str(day),
            "hash_schedule_time": schedule_time.strip(),
            "hash_io_intensity": intensity,
            "hash_pause_for_activity": "1" if pause_for_activity == "1" else "0",
        }

    def validate_import(self, values: object) -> dict[str, str]:
        if not isinstance(values, dict):
            raise AppSettingError(
                "The settings file must contain a JSON object named settings."
            )
        unknown = set(values) - self.EDITABLE_KEYS
        if unknown:
            raise AppSettingError(
                "The settings file contains options this version does not support: "
                + ", ".join(sorted(unknown))
                + ". No settings were changed."
            )
        text_values = {
            key: value for key, value in values.items() if isinstance(value, str)
        }
        if len(text_values) != len(values):
            raise AppSettingError(
                "Every imported setting must contain text. No settings were changed."
            )
        validated: dict[str, str] = {}
        general = {
            key: text_values[key] for key in (
                "installation_name", "timezone", "default_library_view",
                "default_cover_size",
            ) if key in text_values
        }
        if general:
            current = self.values()
            validated.update(self.validate_general(
                general.get("installation_name", current["installation_name"]),
                general.get("timezone", current["timezone"]),
                general.get("default_library_view", current["default_library_view"]),
                general.get("default_cover_size", current["default_cover_size"]),
            ))
        external = {
            key: text_values[key] for key in (
                "search_provider_name", "search_url_template",
            ) if key in text_values
        }
        if external:
            current = self.values()
            validated.update(self.validate_external_search(
                external.get("search_provider_name", current["search_provider_name"]),
                external.get("search_url_template", current["search_url_template"]),
            ))
        if "trash_retention_days" in text_values:
            retention = text_values["trash_retention_days"].strip().casefold()
            if retention not in {"never", "7", "30", "90", "365"}:
                raise AppSettingError(
                    "Trash retention must be Never, 7 days, 30 days, 90 days, or 1 year."
                )
            validated["trash_retention_days"] = retention
        if "log_level" in text_values:
            validated.update(self.validate_logging(text_values["log_level"]))
        if "default_season_display" in text_values:
            validated.update(
                self.validate_season_display(text_values["default_season_display"])
            )
        safety_keys = {"lockdown_mode", "read_only_mode"}.intersection(text_values)
        safety_imported = bool(safety_keys)
        if safety_imported:
            current = self.values()
            supplied_read_only = text_values.get("read_only_mode")
            supplied_lockdown = text_values.get("lockdown_mode")
            for value in (supplied_read_only, supplied_lockdown):
                if value is not None and value.strip() not in {"0", "1"}:
                    raise AppSettingError(
                        "Imported file-protection flags must be 0 or 1. No settings were changed."
                    )
            if supplied_read_only is not None and supplied_lockdown is not None:
                read_only = supplied_read_only.strip()
                lockdown = supplied_lockdown.strip()
                if read_only == "1" and lockdown == "1":
                    raise AppSettingError(
                        "Read-Only Mode and Lockdown Mode cannot both be enabled. No settings were changed."
                    )
                mode = "readonly" if read_only == "1" else "lockdown" if lockdown == "1" else "standard"
            elif supplied_read_only is not None:
                mode = (
                    "readonly" if supplied_read_only.strip() == "1"
                    else "lockdown" if current["lockdown_mode"] == "1" else "standard"
                )
            else:
                mode = (
                    "lockdown" if supplied_lockdown.strip() == "1"
                    else "readonly" if current["read_only_mode"] == "1" else "standard"
                )
            validated.update(self.validate_safety(mode))
        hashing_keys = {
            "hash_mode", "hash_immediate_limit", "hash_schedule_frequency",
            "hash_schedule_day", "hash_schedule_time", "hash_io_intensity",
            "hash_pause_for_activity",
        }
        if hashing_keys.intersection(text_values):
            current = self.values()
            validated.update(self.validate_hashing(*(
                text_values.get(key, current[key]) for key in (
                    "hash_mode", "hash_immediate_limit", "hash_schedule_frequency",
                    "hash_schedule_day", "hash_schedule_time", "hash_io_intensity",
                    "hash_pause_for_activity",
                )
            )))
        result = {key: validated[key] for key in text_values}
        if safety_imported:
            # A partial import still returns both flags so the persisted state
            # cannot represent Read-Only and Lockdown simultaneously.
            result["read_only_mode"] = validated["read_only_mode"]
            result["lockdown_mode"] = validated["lockdown_mode"]
        return result

    def set_internal(self, key: str, value: str) -> None:
        if key not in self.defaults:
            raise KeyError(key)
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO app_settings(key,value,updated_at)
                   VALUES (?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                     updated_at=CURRENT_TIMESTAMP""", (key, value),
            )

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

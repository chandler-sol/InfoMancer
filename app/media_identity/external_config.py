from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..path_mapping import ExternalPathMapper, PathMapping, PathMappingError


SUPPORTED_EXTERNAL_SOURCES = frozenset({"plex", "jellyfin"})


class ExternalSourceConfigError(ValueError):
    """Raised when external integration configuration is invalid or unsafe."""


@dataclass(frozen=True)
class ExternalSourceConfig:
    source_key: str
    enabled: bool
    server_url: str
    metadata_root: str
    config: dict[str, Any]


@dataclass(frozen=True)
class ExternalConnectionResult:
    source_key: str
    ok: bool
    server_name: str = ""
    version: str = ""
    detail: str = ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def normalize_server_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ExternalSourceConfigError(
            "Server URL must be a complete HTTP or HTTPS address."
        )
    if parsed.username or parsed.password:
        raise ExternalSourceConfigError(
            "Do not put credentials in the server URL. Save the access token separately."
        )
    if parsed.query or parsed.fragment:
        raise ExternalSourceConfigError(
            "Server URL cannot contain a query string or fragment."
        )
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunsplit(
        (parsed.scheme.casefold(), parsed.netloc, path, "", "")
    )


class ExternalSourceConfigService:
    def __init__(self, database) -> None:
        self.database = database

    @staticmethod
    def _source_key(value: str) -> str:
        key = str(value or "").strip().casefold()
        if key not in SUPPORTED_EXTERNAL_SOURCES:
            raise ExternalSourceConfigError(
                "Choose Plex or Jellyfin as the external analysis source."
            )
        return key

    def source(self, source_key: str) -> ExternalSourceConfig:
        key = self._source_key(source_key)
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT source_key,enabled,server_url,metadata_root,config_json
                   FROM external_analysis_sources WHERE source_key=?""",
                (key,),
            ).fetchone()
        if row is None:
            raise ExternalSourceConfigError(
                f"External analysis source '{key}' is not configured."
            )
        try:
            config = json.loads(row["config_json"] or "{}")
        except json.JSONDecodeError:
            config = {}
        if not isinstance(config, dict):
            config = {}
        return ExternalSourceConfig(
            source_key=key,
            enabled=bool(row["enabled"]),
            server_url=str(row["server_url"] or ""),
            metadata_root=str(row["metadata_root"] or ""),
            config=config,
        )

    def sources(self) -> tuple[ExternalSourceConfig, ...]:
        return tuple(self.source(key) for key in sorted(SUPPORTED_EXTERNAL_SOURCES))

    def save_source(
        self,
        source_key: str,
        *,
        enabled: bool,
        server_url: str,
        metadata_root: str = "",
        config: dict[str, Any] | None = None,
    ) -> ExternalSourceConfig:
        key = self._source_key(source_key)
        url = normalize_server_url(server_url)
        root = str(metadata_root or "").strip()
        if enabled and not url:
            raise ExternalSourceConfigError(
                "Enter the media server URL before enabling this integration."
            )
        payload = json.dumps(config or {}, sort_keys=True, separators=(",", ":"))
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO external_analysis_sources(
                     source_key,enabled,server_url,metadata_root,config_json,updated_at
                   ) VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(source_key) DO UPDATE SET
                     enabled=excluded.enabled,
                     server_url=excluded.server_url,
                     metadata_root=excluded.metadata_root,
                     config_json=excluded.config_json,
                     updated_at=CURRENT_TIMESTAMP""",
                (key, 1 if enabled else 0, url, root, payload),
            )
        return self.source(key)

    def mappings(self, source_key: str) -> tuple[dict[str, Any], ...]:
        key = self._source_key(source_key)
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT id,source_key,external_root,local_root,priority,enabled
                   FROM external_path_mappings
                   WHERE source_key=? ORDER BY priority,id""",
                (key,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def mapper(self, source_key: str) -> ExternalPathMapper:
        return ExternalPathMapper(
            PathMapping(
                row["source_key"],
                row["external_root"],
                row["local_root"],
                priority=int(row["priority"]),
                enabled=bool(row["enabled"]),
            )
            for row in self.mappings(source_key)
        )

    def add_mapping(
        self,
        source_key: str,
        external_root: str,
        local_root: str,
        *,
        priority: int = 100,
        enabled: bool = True,
    ) -> int:
        key = self._source_key(source_key)
        try:
            mapping = PathMapping(
                key,
                external_root,
                local_root,
                priority=priority,
                enabled=enabled,
            )
        except (PathMappingError, ValueError) as exc:
            raise ExternalSourceConfigError(str(exc)) from exc
        if not 0 <= mapping.priority <= 10000:
            raise ExternalSourceConfigError(
                "Mapping priority must be between 0 and 10,000."
            )
        try:
            with self.database.connect() as conn:
                cursor = conn.execute(
                    """INSERT INTO external_path_mappings(
                         source_key,external_root,local_root,priority,enabled
                       ) VALUES (?,?,?,?,?)""",
                    (
                        mapping.source_key,
                        mapping.external_root,
                        mapping.local_root,
                        mapping.priority,
                        1 if mapping.enabled else 0,
                    ),
                )
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise ExternalSourceConfigError(
                "That external-to-local path mapping already exists."
            ) from exc

    def delete_mapping(self, source_key: str, mapping_id: int) -> bool:
        key = self._source_key(source_key)
        with self.database.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM external_path_mappings WHERE id=? AND source_key=?",
                (int(mapping_id), key),
            )
            return cursor.rowcount > 0

    def test_mapping(self, source_key: str, external_path: str) -> dict[str, Any]:
        key = self._source_key(source_key)
        try:
            translated = self.mapper(key).translate(key, external_path)
        except PathMappingError as exc:
            raise ExternalSourceConfigError(str(exc)) from exc
        if translated is None:
            return {
                "matched": False,
                "external_path": str(external_path or "").strip(),
                "local_path": "",
                "exists": False,
                "catalog_match": None,
            }
        local_path = translated.local_path
        with self.database.connect() as conn:
            if os.name == "nt":
                row = conn.execute(
                    """SELECT f.id,f.filename,t.id title_id,
                              COALESCE(t.metadata_title,t.title) title_name
                       FROM files f JOIN titles t ON t.id=f.title_id
                       WHERE f.path=? COLLATE NOCASE LIMIT 1""",
                    (local_path,),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT f.id,f.filename,t.id title_id,
                              COALESCE(t.metadata_title,t.title) title_name
                       FROM files f JOIN titles t ON t.id=f.title_id
                       WHERE f.path=? LIMIT 1""",
                    (local_path,),
                ).fetchone()
        try:
            exists = Path(local_path).is_file()
        except OSError:
            exists = False
        return {
            "matched": True,
            "external_path": str(external_path or "").strip(),
            "local_path": local_path,
            "exists": exists,
            "mapping_id": int(translated.mapping.priority) if False else None,
            "catalog_match": dict(row) if row is not None else None,
        }


def test_external_connection(
    source_key: str,
    server_url: str,
    token: str,
    *,
    timeout: float = 5.0,
) -> ExternalConnectionResult:
    key = str(source_key or "").strip().casefold()
    if key not in SUPPORTED_EXTERNAL_SOURCES:
        raise ExternalSourceConfigError("Choose Plex or Jellyfin.")
    base = normalize_server_url(server_url)
    credential = str(token or "").strip()
    if not base:
        raise ExternalSourceConfigError("Enter the media server URL first.")
    if not credential:
        raise ExternalSourceConfigError(
            f"Enter a {key.title()} access token before testing the connection."
        )

    if key == "plex":
        url = base + "/"
        headers = {
            "Accept": "application/json",
            "X-Plex-Token": credential,
            "X-Plex-Product": "InfoMancer",
            "X-Plex-Client-Identifier": "infomancer-episode-identity",
        }
    else:
        url = base + "/System/Info"
        headers = {
            "Accept": "application/json",
            "X-Emby-Token": credential,
        }

    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=max(1.0, min(float(timeout), 15.0))) as response:
            if response.status != 200:
                raise ExternalSourceConfigError(
                    f"{key.title()} returned HTTP {response.status}."
                )
            payload = response.read(1024 * 1024)
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            detail = "The server rejected the access token."
        elif 300 <= exc.code < 400:
            detail = "The server redirected the test request. Save its final local server URL instead."
        else:
            detail = f"The server returned HTTP {exc.code}."
        return ExternalConnectionResult(key, False, detail=detail)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return ExternalConnectionResult(
            key,
            False,
            detail=f"InfoMancer could not reach the server: {reason}",
        )

    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ExternalConnectionResult(
            key,
            False,
            detail="The server responded, but InfoMancer could not read its JSON status response.",
        )

    if key == "plex":
        container = data.get("MediaContainer") if isinstance(data, dict) else None
        if not isinstance(container, dict):
            return ExternalConnectionResult(
                key, False, detail="The response did not look like a Plex Media Server."
            )
        return ExternalConnectionResult(
            key,
            True,
            server_name=str(container.get("friendlyName") or ""),
            version=str(container.get("version") or ""),
            detail="Authenticated Plex connection succeeded.",
        )

    if not isinstance(data, dict) or not (
        data.get("ServerName") or data.get("ProductName") or data.get("Id")
    ):
        return ExternalConnectionResult(
            key, False, detail="The response did not look like a Jellyfin server."
        )
    return ExternalConnectionResult(
        key,
        True,
        server_name=str(data.get("ServerName") or data.get("ProductName") or ""),
        version=str(data.get("Version") or ""),
        detail="Authenticated Jellyfin connection succeeded.",
    )

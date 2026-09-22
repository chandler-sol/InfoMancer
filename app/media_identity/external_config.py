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
from .external import ExternalSourceRegistry, ExternalSourceStatus
from .sources.jellyfin import JellyfinTrickplaySource
from .sources.plex import PlexBifError, PlexBifSource, normalize_plex_metadata_root


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
    credential_generation: str = ""
    config_revision: int = 0
    last_test_revision: int | None = None
    last_test_status: str = ""
    last_test_detail: str = ""
    last_test_server_name: str = ""
    last_test_version: str = ""
    last_test_at: str | None = None


@dataclass(frozen=True)
class ValidatedExternalSourceSettings:
    source_key: str
    enabled: bool
    server_url: str
    metadata_root: str
    config: dict[str, Any]
    config_json: str


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
    raw = str(value or "")
    if not raw:
        return ""
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in raw
    ):
        raise ExternalSourceConfigError(
            "Server URL cannot contain whitespace or control characters."
        )
    if any(ord(character) > 127 for character in raw):
        raise ExternalSourceConfigError(
            "Server URL cannot contain non-ASCII characters. Use the server's ASCII URL."
        )
    try:
        parsed = urllib.parse.urlsplit(raw)
        scheme = parsed.scheme.casefold()
        hostname = parsed.hostname
        username = parsed.username
        password = parsed.password
        # Accessing port validates malformed or out-of-range port syntax even
        # though urlunsplit() preserves the original normalized netloc.
        _port = parsed.port
    except ValueError as exc:
        raise ExternalSourceConfigError(
            "Server URL is not valid. Enter a complete HTTP or HTTPS address."
        ) from exc
    if scheme not in {"http", "https"} or not hostname:
        raise ExternalSourceConfigError(
            "Server URL must be a complete HTTP or HTTPS address."
        )
    if username or password:
        raise ExternalSourceConfigError(
            "Do not put credentials in the server URL. Save the access token separately."
        )
    if parsed.query or parsed.fragment:
        raise ExternalSourceConfigError(
            "Server URL cannot contain a query string or fragment."
        )
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunsplit(
        (scheme, parsed.netloc, path, "", "")
    )


def external_token_is_bound(
    source: ExternalSourceConfig,
    secrets: dict[str, str],
) -> bool:
    token_key = f"{source.source_key}_token"
    endpoint_key = f"{source.source_key}_token_endpoint"
    generation_key = f"{source.source_key}_token_generation"
    return (
        bool(secrets.get(token_key, ""))
        and secrets.get(endpoint_key, "") == source.server_url
        and bool(source.credential_generation)
        and secrets.get(generation_key, "") == source.credential_generation
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
                """SELECT source_key,enabled,server_url,metadata_root,config_json,
                          credential_generation,config_revision,last_test_revision,
                          last_test_status,last_test_detail,last_test_server_name,
                          last_test_version,last_test_at
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
        config_revision = int(row["config_revision"] or 0)
        last_test_revision = (
            int(row["last_test_revision"])
            if row["last_test_revision"] is not None
            else None
        )
        test_is_current = last_test_revision == config_revision
        return ExternalSourceConfig(
            source_key=key,
            enabled=bool(row["enabled"]),
            server_url=str(row["server_url"] or ""),
            metadata_root=str(row["metadata_root"] or ""),
            config=config,
            credential_generation=str(row["credential_generation"] or ""),
            config_revision=config_revision,
            last_test_revision=last_test_revision if test_is_current else None,
            last_test_status=str(row["last_test_status"] or "") if test_is_current else "",
            last_test_detail=str(row["last_test_detail"] or "") if test_is_current else "",
            last_test_server_name=(
                str(row["last_test_server_name"] or "") if test_is_current else ""
            ),
            last_test_version=str(row["last_test_version"] or "") if test_is_current else "",
            last_test_at=row["last_test_at"] if test_is_current else None,
        )

    def sources(self) -> tuple[ExternalSourceConfig, ...]:
        return tuple(self.source(key) for key in sorted(SUPPORTED_EXTERNAL_SOURCES))

    def validate_source_settings(
        self,
        source_key: str,
        *,
        enabled: bool,
        server_url: str,
        metadata_root: str = "",
        config: dict[str, Any] | None = None,
    ) -> ValidatedExternalSourceSettings:
        """Validate and normalize every non-secret setting before credentials change."""
        key = self._source_key(source_key)
        url = normalize_server_url(server_url)
        root = str(metadata_root or "").strip()
        if key == "plex" and root:
            try:
                root = str(normalize_plex_metadata_root(root))
            except PlexBifError as exc:
                raise ExternalSourceConfigError(str(exc)) from exc
        elif key != "plex":
            root = ""
        if enabled and not url:
            raise ExternalSourceConfigError(
                "Enter the media server URL before enabling this integration."
            )
        normalized_config = dict(config or {})
        try:
            payload = json.dumps(
                normalized_config, sort_keys=True, separators=(",", ":")
            )
        except (TypeError, ValueError) as exc:
            raise ExternalSourceConfigError(
                "External integration settings contain an unsupported value."
            ) from exc
        return ValidatedExternalSourceSettings(
            source_key=key,
            enabled=bool(enabled),
            server_url=url,
            metadata_root=root,
            config=normalized_config,
            config_json=payload,
        )

    def save_source(
        self,
        source_key: str,
        *,
        enabled: bool,
        server_url: str,
        metadata_root: str = "",
        config: dict[str, Any] | None = None,
        credential_generation: str | None = None,
    ) -> ExternalSourceConfig:
        validated = self.validate_source_settings(
            source_key,
            enabled=enabled,
            server_url=server_url,
            metadata_root=metadata_root,
            config=config,
        )
        key = validated.source_key
        url = validated.server_url
        root = validated.metadata_root
        payload = validated.config_json
        apply_credential_generation = credential_generation is not None
        generation = str(credential_generation or "").strip()
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO external_analysis_sources(
                     source_key,enabled,server_url,metadata_root,config_json,
                     credential_generation,config_revision,updated_at
                   ) VALUES (?,?,?,?,?,?,0,CURRENT_TIMESTAMP)
                   ON CONFLICT(source_key) DO UPDATE SET
                     enabled=excluded.enabled,
                     server_url=excluded.server_url,
                     metadata_root=excluded.metadata_root,
                     config_json=excluded.config_json,
                     config_revision=external_analysis_sources.config_revision +
                       CASE
                         WHEN external_analysis_sources.server_url != excluded.server_url
                              OR external_analysis_sources.metadata_root != excluded.metadata_root
                              OR external_analysis_sources.config_json != excluded.config_json
                              OR (
                                ? AND external_analysis_sources.credential_generation
                                  != excluded.credential_generation
                              )
                         THEN 1
                         ELSE 0
                       END,
                     credential_generation=
                       CASE
                         WHEN ? THEN excluded.credential_generation
                         ELSE external_analysis_sources.credential_generation
                       END,
                     updated_at=CURRENT_TIMESTAMP""",
                (
                    key,
                    1 if enabled else 0,
                    url,
                    root,
                    payload,
                    generation,
                    1 if apply_credential_generation else 0,
                    1 if apply_credential_generation else 0,
                ),
            )
        return self.source(key)

    def clear_connection_result(self, source_key: str) -> None:
        key = self._source_key(source_key)
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE external_analysis_sources
                   SET last_test_revision=NULL,
                       last_test_status='',
                       last_test_detail='',
                       last_test_server_name='',
                       last_test_version='',
                       last_test_at=NULL,
                       updated_at=CURRENT_TIMESTAMP
                   WHERE source_key=?""",
                (key,),
            )

    def record_connection_result(
        self,
        result: ExternalConnectionResult,
        *,
        tested_server_url: str,
        tested_revision: int,
    ) -> bool:
        key = self._source_key(result.source_key)
        endpoint = normalize_server_url(tested_server_url)
        revision = int(tested_revision)
        with self.database.connect() as conn:
            cursor = conn.execute(
                """UPDATE external_analysis_sources
                   SET last_test_revision=config_revision,
                       last_test_status=?,
                       last_test_detail=?,
                       last_test_server_name=?,
                       last_test_version=?,
                       last_test_at=CURRENT_TIMESTAMP,
                       updated_at=CURRENT_TIMESTAMP
                   WHERE source_key=? AND server_url=? AND config_revision=?""",
                (
                    "ok" if result.ok else "error",
                    result.detail,
                    result.server_name,
                    result.version,
                    key,
                    endpoint,
                    revision,
                ),
            )
            return cursor.rowcount == 1

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
            "catalog_match": dict(row) if row is not None else None,
        }


class ConfiguredExternalSource:
    """Capability-safe shell until a source-specific evidence adapter is installed."""

    version = "0.9-pr-e"

    def __init__(self, config: ExternalSourceConfig, *, token_configured: bool) -> None:
        self.source_key = config.source_key
        self.config = config
        self.token_configured = bool(token_configured)

    def status(self) -> ExternalSourceStatus:
        if not self.config.enabled:
            return ExternalSourceStatus(
                source_key=self.source_key,
                available=False,
                detail="Disabled in Settings.",
            )
        if not self.config.server_url:
            return ExternalSourceStatus(
                source_key=self.source_key,
                available=False,
                detail="Server URL is not configured.",
            )
        if not self.token_configured:
            return ExternalSourceStatus(
                source_key=self.source_key,
                available=False,
                detail="Access token is not configured.",
            )
        if self.config.last_test_status == "ok":
            return ExternalSourceStatus(
                source_key=self.source_key,
                available=True,
                capabilities=frozenset(),
                detail="Configured; the last explicit connection test succeeded.",
            )
        if self.config.last_test_status == "error":
            return ExternalSourceStatus(
                source_key=self.source_key,
                available=False,
                capabilities=frozenset(),
                detail="Configured; the last explicit connection test failed.",
            )
        return ExternalSourceStatus(
            source_key=self.source_key,
            available=True,
            capabilities=frozenset(),
            detail="Configured and ready for a source-specific analysis adapter.",
        )

    def resolve_media(self, context):
        return None

    def preview_frames(self, media):
        return ()

    def read_preview(self, frame):
        raise ExternalSourceConfigError(
            f"{self.source_key.title()} preview ingestion is not installed yet."
        )

    def subtitles(self, media):
        return ()

    def read_subtitle(self, subtitle):
        raise ExternalSourceConfigError(
            f"{self.source_key.title()} subtitle ingestion is not installed yet."
        )

    def media_metadata(self, media):
        return {}

    def fingerprints(self, media):
        return ()

    def known_identity(self, media):
        return None


def build_configured_source_registry(
    service: ExternalSourceConfigService,
    secrets: dict[str, str],
) -> ExternalSourceRegistry:
    configured = []
    for source in service.sources():
        token_is_bound = external_token_is_bound(source, secrets)
        if source.source_key == "jellyfin":
            configured.append(
                JellyfinTrickplaySource(
                    source.server_url,
                    secrets.get("jellyfin_token", "") if token_is_bound else "",
                    service.mapper("jellyfin"),
                    enabled=source.enabled,
                    last_test_status=source.last_test_status,
                    allow_insecure_http=bool(
                        source.config.get("allow_insecure_http", False)
                    ),
                    advertise_preview_frames=True,
                )
            )
        elif source.source_key == "plex":
            configured.append(
                PlexBifSource(
                    source.server_url,
                    secrets.get("plex_token", "") if token_is_bound else "",
                    service.mapper("plex"),
                    metadata_root=source.metadata_root,
                    enabled=source.enabled,
                    last_test_status=source.last_test_status,
                    allow_insecure_http=bool(
                        source.config.get("allow_insecure_http", False)
                    ),
                    advertise_preview_frames=True,
                )
            )
        else:
            configured.append(
                ConfiguredExternalSource(
                    source,
                    token_configured=token_is_bound,
                )
            )
    return ExternalSourceRegistry(tuple(configured))


def test_external_connection(
    source_key: str,
    server_url: str,
    token: str,
    *,
    timeout: float = 5.0,
    allow_insecure_http: bool = False,
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

    if (
        urllib.parse.urlsplit(base).scheme.casefold() == "http"
        and not allow_insecure_http
    ):
        raise ExternalSourceConfigError(
            f"{key.title()} credentials will not be sent over plain HTTP. "
            "Use HTTPS or explicitly allow insecure HTTP for this integration."
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
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
    )
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

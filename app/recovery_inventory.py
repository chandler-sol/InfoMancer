from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .maintenance import MaintenanceError, validate_database_backup
from .recovery_package import RecoveryPackageError, RecoveryPackageService
from .update_channels import CHANNEL_RANK, channel_allows, normalize_channel, version_key


RECOVERY_SEARCH_PATHS_ENV = "INFOMANCER_RECOVERY_SEARCH_PATHS"
MAX_INVENTORY_PACKAGES = 50


def recovery_search_directories(database_path: Path, configured: str | None = None) -> list[Path]:
    """Return the explicit directories InfoMancer is allowed to inspect for recovery packages.

    The built-in recovery-packages directory is always included. Additional locations
    must be explicitly configured through INFOMANCER_RECOVERY_SEARCH_PATHS. The value
    uses the host OS path separator and is never interpreted as a recursive filesystem
    search request.
    """
    database_path = Path(database_path)
    default = database_path.parent / "recovery-packages"
    raw = os.getenv(RECOVERY_SEARCH_PATHS_ENV, "") if configured is None else configured
    directories = [default]
    if raw:
        for value in raw.split(os.pathsep):
            value = value.strip()
            if not value:
                continue
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                candidate = database_path.parent / candidate
            directories.append(candidate)

    unique: list[Path] = []
    seen: set[str] = set()
    for directory in directories:
        try:
            resolved = directory.resolve(strict=False)
        except OSError:
            resolved = directory.absolute()
        key = str(resolved).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(resolved)
    return unique


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def schema_contract_from_database(database_path: Path) -> dict:
    """Derive the compatibility contract recorded inside a packaged database.

    Old portable packages predate the recovery inventory feature, so the schema
    contract is derived from the database itself instead of trusting optional archive
    metadata. If the compatibility ledger is absent or incomplete, downgrade advice
    fails closed while equal/newer targets can still be recommended safely.
    """
    connection = None
    try:
        connection = sqlite3.connect(f"file:{Path(database_path)}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        tables = _table_names(connection)
        if "schema_migrations" not in tables:
            return {
                "current": 0,
                "minimum_reader_schema": None,
                "minimum_writer_schema": None,
                "downgrade_policy": "restore_required",
                "ledger_status": "missing",
            }

        applied_versions = sorted(
            int(row[0]) for row in connection.execute("SELECT version FROM schema_migrations")
        )
        current = applied_versions[-1] if applied_versions else 0
        if current < 1 or "schema_compatibility" not in tables:
            return {
                "current": current,
                "minimum_reader_schema": current if current else None,
                "minimum_writer_schema": current if current else None,
                "downgrade_policy": "restore_required",
                "ledger_status": "missing",
            }

        rows = connection.execute(
            """SELECT migration_version,minimum_reader_schema,minimum_writer_schema,
                      downgrade_policy
               FROM schema_compatibility
               WHERE migration_version<=?
               ORDER BY migration_version""",
            (current,),
        ).fetchall()
        recorded = {int(row["migration_version"]) for row in rows}
        if any(version not in recorded for version in applied_versions):
            return {
                "current": current,
                "minimum_reader_schema": current,
                "minimum_writer_schema": current,
                "downgrade_policy": "restore_required",
                "ledger_status": "incomplete",
            }

        minimum_reader = max(int(row["minimum_reader_schema"]) for row in rows)
        writer_values = [row["minimum_writer_schema"] for row in rows]
        minimum_writer = (
            None
            if any(value is None for value in writer_values)
            else max(int(value) for value in writer_values)
        )
        policies = {str(row["downgrade_policy"]) for row in rows}
        if "restore_required" in policies:
            policy = "restore_required"
        elif "read_only" in policies:
            policy = "read_only"
        else:
            policy = "compatible"
        return {
            "current": current,
            "minimum_reader_schema": minimum_reader,
            "minimum_writer_schema": minimum_writer,
            "downgrade_policy": policy,
            "ledger_status": "complete",
        }
    except sqlite3.Error as exc:
        raise RecoveryPackageError(
            "InfoMancer could not read the packaged database schema history."
        ) from exc
    finally:
        if connection is not None:
            connection.close()


def inspect_recovery_package(
    service: RecoveryPackageService,
    package_path: Path,
) -> dict:
    """Fully verify a portable package and derive its database schema contract."""
    package_path = Path(package_path)
    summary = service.verify(package_path)
    temporary_database: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=service.database_path.parent,
            prefix="inventory-recovery-",
            suffix=".db",
            delete=False,
        ) as handle:
            temporary_database = Path(handle.name)
            with zipfile.ZipFile(package_path, "r") as archive:
                with archive.open("database/infomancer.db", "r") as source:
                    shutil.copyfileobj(source, handle, length=1024 * 1024)
        validate_database_backup(temporary_database)
        database_schema = schema_contract_from_database(temporary_database)
    except (KeyError, OSError, zipfile.BadZipFile, MaintenanceError) as exc:
        if isinstance(exc, MaintenanceError):
            raise RecoveryPackageError(str(exc)) from exc
        raise RecoveryPackageError(
            "InfoMancer could not inspect the database inside this recovery package."
        ) from exc
    finally:
        if temporary_database is not None:
            temporary_database.unlink(missing_ok=True)

    stat = package_path.stat()
    return {
        **summary,
        "name": package_path.name,
        "package_size": int(stat.st_size),
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "database_schema": database_schema,
        "integrity_status": "verified",
    }


def _safe_package_files(directory: Path) -> Iterable[Path]:
    try:
        root = directory.resolve(strict=True)
    except OSError:
        return ()
    if not root.is_dir():
        return ()

    files: list[Path] = []
    try:
        candidates = root.glob("*.infomancer-backup")
        for candidate in candidates:
            try:
                if candidate.is_symlink():
                    continue
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(root)
                if resolved.is_file():
                    files.append(resolved)
            except (OSError, ValueError):
                continue
    except OSError:
        return ()
    return files


def scan_recovery_packages(
    service: RecoveryPackageService,
    directories: Iterable[Path],
    limit: int = MAX_INVENTORY_PACKAGES,
) -> list[dict]:
    """Inspect up to ``limit`` newest packages from explicitly allowed directories."""
    if limit < 1:
        return []
    candidates: dict[str, tuple[Path, Path, float]] = {}
    for directory in directories:
        for candidate in _safe_package_files(Path(directory)):
            try:
                modified = candidate.stat().st_mtime
            except OSError:
                continue
            key = str(candidate).casefold()
            previous = candidates.get(key)
            if previous is None or modified > previous[2]:
                candidates[key] = (candidate, Path(directory), modified)

    ordered = sorted(candidates.values(), key=lambda item: item[2], reverse=True)[:limit]
    results: list[dict] = []
    for candidate, directory, _modified in ordered:
        try:
            item = inspect_recovery_package(service, candidate)
            item.update({
                "valid": True,
                "location": str(directory),
                "error": "",
            })
        except (RecoveryPackageError, OSError) as exc:
            item = {
                "name": candidate.name,
                "valid": False,
                "location": str(directory),
                "error": str(exc),
                "integrity_status": "failed",
            }
        results.append(item)
    return results


def recovery_target_compatibility(backup_schema: dict, target_schema: dict) -> dict:
    """Assess whether a qualified build may safely open a restored backup read/write."""
    backup_current = int(backup_schema.get("current") or 0)
    target_current = int(target_schema.get("current") or 0)
    if backup_current < 1 or target_current < 1:
        return {"status": "unknown", "reason": "schema_unknown"}
    if target_current >= backup_current:
        return {
            "status": "compatible",
            "reason": "equal_or_upgrade",
            "backup_schema": backup_current,
            "target_schema": target_current,
        }

    minimum_writer = backup_schema.get("minimum_writer_schema")
    minimum_reader = backup_schema.get("minimum_reader_schema")
    policy = str(backup_schema.get("downgrade_policy") or "restore_required")
    if (
        policy == "compatible"
        and isinstance(minimum_writer, int)
        and target_current >= minimum_writer
    ):
        return {
            "status": "compatible",
            "reason": "ledger_safe_downgrade",
            "backup_schema": backup_current,
            "target_schema": target_current,
        }
    if (
        isinstance(minimum_reader, int)
        and target_current >= minimum_reader
        and policy != "restore_required"
    ):
        return {
            "status": "read_only",
            "reason": "writer_incompatible",
            "backup_schema": backup_current,
            "target_schema": target_current,
        }
    return {
        "status": "restore_required",
        "reason": "reader_incompatible",
        "backup_schema": backup_current,
        "target_schema": target_current,
    }


def recommend_recovery_build(
    backup: dict,
    manifests: Iterable[dict],
    preferred_channel: str,
) -> dict | None:
    """Choose the best qualified build for a verified backup.

    Preference order is an exact creator-version match inside the selected update
    channel, then the newest compatible build allowed by that channel. If the selected
    channel has no compatible build, the most stable compatible cross-channel build is
    returned with ``requires_channel_change`` set so the UI can warn instead of silently
    changing channel policy.
    """
    if not backup.get("valid"):
        return None
    selected = normalize_channel(preferred_channel)
    backup_schema = backup.get("database_schema")
    if not isinstance(backup_schema, dict):
        return None

    creator = str(backup.get("app_version") or "").strip().lstrip("v")
    candidates: list[dict] = []
    for raw in manifests:
        if not isinstance(raw, dict):
            continue
        target_schema = raw.get("database_schema")
        if not isinstance(target_schema, dict):
            continue
        compatibility = recovery_target_compatibility(backup_schema, target_schema)
        if compatibility["status"] != "compatible":
            continue
        item = dict(raw)
        item["recovery_compatibility"] = compatibility
        candidates.append(item)
    if not candidates:
        return None

    allowed = [
        item for item in candidates
        if channel_allows(selected, str(item.get("channel") or ""))
    ]
    exact_allowed = [
        item for item in allowed
        if str(item.get("version") or "").lstrip("v") == creator
    ]
    if exact_allowed:
        chosen = max(exact_allowed, key=lambda item: version_key(str(item.get("version") or "")))
        reason = "exact_creator"
        requires_channel_change = False
    elif allowed:
        chosen = max(allowed, key=lambda item: version_key(str(item.get("version") or "")))
        reason = "newest_compatible"
        requires_channel_change = False
    else:
        chosen = min(
            candidates,
            key=lambda item: (
                CHANNEL_RANK[normalize_channel(str(item.get("channel") or ""))],
                tuple(-value if isinstance(value, int) else value for value in version_key(str(item.get("version") or ""))[:3]),
            ),
        )
        reason = "cross_channel_fallback"
        requires_channel_change = True

    artifacts = []
    raw_artifacts = chosen.get("artifacts")
    if isinstance(raw_artifacts, dict):
        for platform, artifact in sorted(raw_artifacts.items()):
            if not isinstance(artifact, dict):
                continue
            artifacts.append({
                "platform": str(platform),
                "kind": str(artifact.get("kind") or "download"),
                "url": str(artifact.get("url") or ""),
                "sha256": str(artifact.get("sha256") or ""),
            })

    return {
        "version": str(chosen.get("version") or ""),
        "channel": normalize_channel(str(chosen.get("channel") or "")),
        "build_id": str(chosen.get("build_id") or ""),
        "commit_sha": str(chosen.get("commit_sha") or ""),
        "qualified_at": str(chosen.get("qualified_at") or ""),
        "reason": reason,
        "requires_channel_change": requires_channel_change,
        "compatibility": chosen["recovery_compatibility"],
        "artifacts": artifacts,
    }

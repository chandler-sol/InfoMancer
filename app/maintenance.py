from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class MaintenanceError(RuntimeError):
    pass


REQUIRED_TABLES = {"titles", "files", "roots", "users", "app_settings"}
SAFE_BACKUP_NAME = re.compile(
    r"^infomancer-backup-\d{8}-\d{6}(?:-[a-z-]+)?(?:-\d+)?\.db$"
)


def backup_directory(database_path: Path) -> Path:
    path = database_path.parent / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_database_backup(database_path: Path, suffix: str = "") -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    cleaned_suffix = re.sub(r"[^a-z]+", "-", suffix.casefold()).strip("-")
    name = f"infomancer-backup-{stamp}"
    if cleaned_suffix:
        name += f"-{cleaned_suffix}"
    directory = backup_directory(database_path)
    destination = directory / f"{name}.db"
    counter = 2
    while destination.exists():
        destination = directory / f"{name}-{counter}.db"
        counter += 1
    source = None
    target = None
    try:
        source = sqlite3.connect(database_path)
        target = sqlite3.connect(destination)
        with target:
            source.backup(target)
        source.close()
        source = None
        target.close()
        target = None
        validate_database_backup(destination)
    except (sqlite3.Error, OSError, MaintenanceError) as exc:
        destination.unlink(missing_ok=True)
        raise MaintenanceError(
            "InfoMancer could not create a readable database backup. "
            "The live database was not changed."
        ) from exc
    finally:
        if source is not None:
            source.close()
        if target is not None:
            target.close()
    return destination


def validate_database_backup(path: Path) -> None:
    connection = None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    except sqlite3.Error as exc:
        raise MaintenanceError(
            "The selected file is not a readable SQLite database."
        ) from exc
    finally:
        if connection is not None:
            connection.close()
    if not integrity or integrity[0] != "ok":
        raise MaintenanceError(
            "The selected database did not pass SQLite's integrity check."
        )
    missing = REQUIRED_TABLES - tables
    if missing:
        raise MaintenanceError(
            "The selected database is not an InfoMancer backup. "
            f"It is missing required data tables: {', '.join(sorted(missing))}."
        )


def list_database_backups(database_path: Path) -> list[dict]:
    rows = []
    for path in backup_directory(database_path).glob("infomancer-backup-*.db"):
        if not SAFE_BACKUP_NAME.fullmatch(path.name):
            continue
        stat = path.stat()
        rows.append({
            "name": path.name,
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(
                stat.st_mtime, timezone.utc
            ).isoformat(),
        })
    return sorted(rows, key=lambda item: item["modified_at"], reverse=True)


def resolve_backup(database_path: Path, name: str) -> Path:
    if not SAFE_BACKUP_NAME.fullmatch(name):
        raise MaintenanceError("That backup name is not valid.")
    path = next((
        candidate for candidate in backup_directory(database_path).iterdir()
        if candidate.is_file() and candidate.name == name
    ), None)
    if path is None:
        raise MaintenanceError("That database backup no longer exists.")
    return path


def install_database_backup(database_path: Path, candidate: Path) -> Path:
    validate_database_backup(candidate)
    safety_backup = create_database_backup(database_path, "before-restore")
    staged = database_path.with_suffix(".restore.db")
    source = None
    target = None
    try:
        source = sqlite3.connect(candidate)
        target = sqlite3.connect(staged)
        with target:
            source.backup(target)
        source.close()
        source = None
        target.close()
        target = None
        validate_database_backup(staged)
        for suffix in ("-wal", "-shm"):
            Path(f"{database_path}{suffix}").unlink(missing_ok=True)
        os.replace(staged, database_path)
    except (sqlite3.Error, OSError, MaintenanceError) as exc:
        staged.unlink(missing_ok=True)
        raise MaintenanceError(
            "The restore could not be completed. A safety backup of the "
            "current database was retained and the uploaded file was not used."
        ) from exc
    finally:
        if source is not None:
            source.close()
        if target is not None:
            target.close()
    return safety_backup


def update_request_path(database_path: Path) -> Path:
    return database_path.parent / "update-request.json"


def update_status_path(database_path: Path) -> Path:
    return database_path.parent / "update-status.json"


def read_update_status(database_path: Path) -> dict:
    path = update_status_path(database_path)
    if not path.exists():
        return {"status": "idle"}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"status": "idle"}
    except (OSError, json.JSONDecodeError):
        return {
            "status": "error",
            "message": "The updater status file could not be read.",
        }


def write_update_status(database_path: Path, value: dict) -> Path:
    path = update_status_path(database_path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return path


def write_update_request(database_path: Path, tag: str, requested_by: str) -> Path:
    candidate = tag[1:] if tag.startswith("v") else tag
    core = candidate
    suffix = ""
    has_suffix_marker = False
    for marker in ("-", "+"):
        if marker in core:
            core, suffix = core.split(marker, 1)
            has_suffix_marker = True
            break
    version_parts = core.split(".")
    valid_suffix = (not has_suffix_marker or bool(suffix)) and all(
        character.isascii() and (character.isalnum() or character in ".-")
        for character in suffix
    )
    if (
        not tag or len(tag) > 100 or len(version_parts) != 3
        or not all(part.isascii() and part.isdigit() and part for part in version_parts)
        or not valid_suffix
    ):
        raise MaintenanceError("The selected release tag is not valid.")
    request_path = update_request_path(database_path)
    temporary = request_path.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "tag": tag,
        "requested_by": requested_by,
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2), encoding="utf-8")
    os.replace(temporary, request_path)
    return request_path

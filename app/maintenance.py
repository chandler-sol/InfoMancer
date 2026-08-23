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
SAFE_ARTWORK_NAME = re.compile(r"^[0-9a-f]{40}\.(?:jpg|png|webp)$")


def backup_directory(database_path: Path) -> Path:
    path = database_path.parent / "backups"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MaintenanceError(
            "InfoMancer could not access its database backup folder. Check application-data permissions and available disk space."
        ) from exc
    return path


def _safe_backup_file(directory: Path, candidate: Path) -> Path | None:
    """Return a contained regular backup file without following directory symlinks."""
    try:
        directory_resolved = directory.resolve(strict=True)
        if candidate.is_symlink():
            return None
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(directory_resolved)
        if not resolved.is_file():
            return None
        return resolved
    except (OSError, ValueError):
        return None


def create_database_backup(database_path: Path, suffix: str = "") -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    cleaned_suffix = re.sub(r"[^a-z]+", "-", suffix.casefold()).strip("-")
    name = f"infomancer-backup-{stamp}"
    if cleaned_suffix:
        name += f"-{cleaned_suffix}"
    directory = backup_directory(database_path)
    destination = directory / f"{name}.db"
    counter = 2
    while destination.exists() or destination.is_symlink():
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
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
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
        foreign_key_error = connection.execute("PRAGMA foreign_key_check").fetchone()
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
    if foreign_key_error:
        raise MaintenanceError(
            "The selected database contains broken catalog relationships."
        )
    missing = REQUIRED_TABLES - tables
    if missing:
        raise MaintenanceError(
            "The selected database is not an InfoMancer backup. "
            f"It is missing required data tables: {', '.join(sorted(missing))}."
        )


def _resolved(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError as exc:
        raise MaintenanceError(
            "InfoMancer could not verify a filesystem path because its storage location is unavailable or unreadable. Reconnect the storage and try again."
        ) from exc


def _inside(path: Path, parent: Path) -> bool:
    try:
        _resolved(path).relative_to(_resolved(parent))
        return True
    except ValueError:
        return False


def _database_roots(database_path: Path) -> tuple[Path, ...]:
    connection = None
    try:
        connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
        return tuple(
            Path(row[0]) for row in connection.execute("SELECT path FROM roots")
            if row[0]
        )
    except sqlite3.Error:
        return ()
    finally:
        if connection is not None:
            connection.close()


def validate_database_paths(
    path: Path, media_browse_roots: tuple[Path, ...],
    existing_roots: tuple[Path, ...] = (),
) -> None:
    """Reject restored catalog paths that escape already-trusted storage."""
    allowed_parents = tuple(_resolved(root) for root in media_browse_roots)
    grandfathered = {_resolved(root) for root in existing_roots}
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        roots: dict[int, Path] = {}
        for row in connection.execute("SELECT id,path FROM roots"):
            root = Path(row["path"] or "")
            resolved = _resolved(root)
            if not root.is_absolute() or (
                resolved not in grandfathered
                and not any(_inside(resolved, parent) for parent in allowed_parents)
            ):
                raise MaintenanceError(
                    "The selected backup contains a media root outside the storage locations this installation trusts."
                )
            roots[int(row["id"])] = root

        for row in connection.execute("SELECT root_id,folder_path FROM titles"):
            root = roots.get(int(row["root_id"]))
            candidate = Path(row["folder_path"] or "")
            if root is None or not candidate.is_absolute() or not _inside(candidate, root):
                raise MaintenanceError(
                    "The selected backup contains a title path outside its configured media root."
                )

        for row in connection.execute(
            """SELECT f.path,t.root_id FROM files f JOIN titles t ON t.id=f.title_id"""
        ):
            root = roots.get(int(row["root_id"]))
            candidate = Path(row["path"] or "")
            if root is None or not candidate.is_absolute() or not _inside(candidate, root):
                raise MaintenanceError(
                    "The selected backup contains a media-file path outside its configured root."
                )

        if "duplicate_trash" in tables:
            for row in connection.execute(
                "SELECT root_id,original_path,trash_path FROM duplicate_trash"
            ):
                root = roots.get(int(row["root_id"])) if row["root_id"] is not None else None
                original = Path(row["original_path"] or "")
                trash = Path(row["trash_path"] or "")
                if (
                    root is None or not original.is_absolute() or not trash.is_absolute()
                    or not _inside(original, root)
                    or not _inside(trash, root / ".infomancer-trash")
                ):
                    raise MaintenanceError(
                        "The selected backup contains managed-trash paths outside a configured media root."
                    )

        if "collections" in tables:
            for row in connection.execute(
                "SELECT artwork_filename FROM collections WHERE artwork_filename IS NOT NULL AND artwork_filename<>''"
            ):
                if not SAFE_ARTWORK_NAME.fullmatch(str(row["artwork_filename"])):
                    raise MaintenanceError(
                        "The selected backup contains an invalid collection artwork filename."
                    )
    except sqlite3.Error as exc:
        raise MaintenanceError(
            "The selected database could not be checked for safe filesystem paths."
        ) from exc
    finally:
        connection.close()


def list_database_backups(database_path: Path) -> list[dict]:
    rows = []
    directory = backup_directory(database_path)
    for path in directory.glob("infomancer-backup-*.db"):
        if not SAFE_BACKUP_NAME.fullmatch(path.name):
            continue
        safe_path = _safe_backup_file(directory, path)
        if safe_path is None:
            continue
        try:
            stat = safe_path.stat()
        except OSError:
            continue
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
    directory = backup_directory(database_path)
    candidate = directory / name
    path = _safe_backup_file(directory, candidate)
    if path is None:
        raise MaintenanceError("That database backup no longer exists or is not safe to use.")
    return path


def install_database_backup(
    database_path: Path, candidate: Path,
    media_browse_roots: tuple[Path, ...] | None = None,
) -> Path:
    validate_database_backup(candidate)
    existing_roots: tuple[Path, ...] = ()
    if media_browse_roots is not None:
        existing_roots = _database_roots(database_path)
        validate_database_paths(candidate, media_browse_roots, existing_roots)
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
        if media_browse_roots is not None:
            validate_database_paths(staged, media_browse_roots, existing_roots)
        for suffix in ("-wal", "-shm"):
            Path(f"{database_path}{suffix}").unlink(missing_ok=True)
        os.replace(staged, database_path)
    except (sqlite3.Error, OSError, MaintenanceError) as exc:
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            pass
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


def _write_json_atomically(path: Path, value: dict, error_message: str) -> Path:
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise MaintenanceError(error_message) from exc
    return path


def write_update_status(database_path: Path, value: dict) -> Path:
    return _write_json_atomically(
        update_status_path(database_path),
        value,
        "InfoMancer could not write the updater status file. Check application-data permissions and free disk space.",
    )


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
    return _write_json_atomically(
        update_request_path(database_path),
        {
            "tag": tag,
            "requested_by": requested_by,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        },
        "InfoMancer could not write the updater request. Check application-data permissions and free disk space.",
    )
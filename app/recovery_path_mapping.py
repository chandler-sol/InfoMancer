from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable

from .maintenance import MaintenanceError, validate_database_backup, validate_database_paths
from .recovery_package import RecoveryPackageError, RecoveryPackageService


_WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def _path_parts(value: str) -> tuple[str, tuple[str, ...], bool]:
    """Return root marker, relative components, and Windows-style semantics."""
    raw = str(value or "").strip()
    if _WINDOWS_ABSOLUTE.match(raw):
        path = PureWindowsPath(raw)
        anchor = path.anchor
        if not anchor:
            raise RecoveryPackageError("A recovery media path is not absolute.")
        return anchor, tuple(path.parts[1:]), True
    path = PurePosixPath(raw)
    if not path.is_absolute():
        raise RecoveryPackageError("A recovery media path is not absolute.")
    return path.anchor, tuple(path.parts[1:]), False


def _relative_parts(value: str, root: str) -> tuple[str, ...]:
    value_anchor, value_parts, value_windows = _path_parts(value)
    root_anchor, root_parts, root_windows = _path_parts(root)
    if value_windows != root_windows:
        raise RecoveryPackageError("A catalog path uses a different path style than its media root.")

    def comparable(part: str) -> str:
        return part.casefold() if value_windows else part

    if comparable(value_anchor) != comparable(root_anchor):
        raise RecoveryPackageError("A catalog path is outside its media root.")
    if len(value_parts) < len(root_parts):
        raise RecoveryPackageError("A catalog path is outside its media root.")
    for actual, expected in zip(value_parts, root_parts):
        if comparable(actual) != comparable(expected):
            raise RecoveryPackageError("A catalog path is outside its media root.")
    return value_parts[len(root_parts):]


def _rewritten_path(value: str, old_root: str, new_root: Path) -> str:
    relative = _relative_parts(value, old_root)
    return str(new_root.joinpath(*relative))


def _inside(candidate: Path, parent: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _validated_destination(value: str, trusted_roots: Iterable[Path]) -> Path:
    candidate = Path(value).expanduser()
    try:
        if not candidate.is_absolute():
            raise RecoveryPackageError("Recovery path mappings must use an absolute destination.")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_dir():
            raise RecoveryPackageError("A recovery path mapping destination is not a directory.")
    except OSError as exc:
        raise RecoveryPackageError(
            f"Recovery path mapping destination is unavailable: {candidate}"
        ) from exc
    trusted = [Path(root).expanduser() for root in trusted_roots]
    if not any(_inside(resolved, root) for root in trusted):
        raise RecoveryPackageError(
            "Recovery path mappings must stay inside a storage location trusted by this installation."
        )
    return resolved


def _extract_packaged_database(
    service: RecoveryPackageService, package_path: Path
) -> Path:
    service.verify(package_path)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=service.database_path.parent,
            prefix="recovery-paths-",
            suffix=".db",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            with zipfile.ZipFile(package_path, "r") as archive:
                with archive.open("database/infomancer.db", "r") as source:
                    shutil.copyfileobj(source, handle, length=1024 * 1024)
        validate_database_backup(temporary)
        return temporary
    except (KeyError, OSError, zipfile.BadZipFile, MaintenanceError) as exc:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if isinstance(exc, MaintenanceError):
            raise RecoveryPackageError(str(exc)) from exc
        raise RecoveryPackageError(
            "InfoMancer could not inspect the media paths inside this recovery package."
        ) from exc


def inspect_recovery_roots(
    service: RecoveryPackageService,
    package_path: Path,
    trusted_roots: Iterable[Path],
) -> list[dict]:
    """Return packaged media roots with conservative same-host suggestions."""
    temporary = _extract_packaged_database(service, Path(package_path))
    trusted = [Path(root).expanduser() for root in trusted_roots]
    rows: list[dict] = []
    try:
        connection = sqlite3.connect(f"file:{temporary}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            source_rows = connection.execute(
                "SELECT id,path,kind,enabled FROM roots ORDER BY id"
            ).fetchall()
        finally:
            connection.close()

        for row in source_rows:
            source = str(row["path"] or "")
            accessible = False
            trusted_here = False
            try:
                current = Path(source).expanduser()
                accessible = current.is_absolute() and current.is_dir()
                trusted_here = accessible and any(_inside(current, root) for root in trusted)
            except OSError:
                pass

            basename = ""
            try:
                _anchor, parts, _windows = _path_parts(source)
                basename = parts[-1] if parts else ""
            except RecoveryPackageError:
                pass

            matches: list[str] = []
            if basename:
                for parent in trusted:
                    candidate = parent / basename
                    try:
                        if candidate.is_dir():
                            matches.append(str(candidate.resolve(strict=True)))
                    except OSError:
                        continue
            suggestion = source if trusted_here else (matches[0] if len(matches) == 1 else "")
            rows.append({
                "id": int(row["id"]),
                "path": source,
                "kind": str(row["kind"] or "media"),
                "enabled": bool(row["enabled"]),
                "accessible": accessible,
                "trusted_here": trusted_here,
                "suggested_path": suggestion,
                "suggestion_is_match": bool(suggestion and suggestion != source),
            })
        return rows
    except sqlite3.Error as exc:
        raise RecoveryPackageError(
            "InfoMancer could not read media roots from the recovery package."
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def apply_recovery_root_mappings(
    database_path: Path,
    mappings: dict[int, str],
    trusted_roots: Iterable[Path],
) -> dict:
    """Rewrite path-bearing catalog rows from packaged roots to current-host roots.

    Mappings are explicit and root-scoped. Windows paths are compared using Windows
    case-insensitive semantics even when the restore runs on Linux or macOS. The
    destination must exist and remain under a trusted browse root before any row is
    changed. A path that cannot be proven to belong to its old root fails closed.
    """
    if not mappings:
        return {"mapped_roots": 0, "rewritten_paths": 0}

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    rewritten = 0
    try:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        roots = {
            int(row["id"]): str(row["path"] or "")
            for row in connection.execute("SELECT id,path FROM roots")
        }
        unknown = sorted(set(mappings) - set(roots))
        if unknown:
            raise RecoveryPackageError("A recovery path mapping refers to an unknown media root.")

        destinations: dict[int, Path] = {
            root_id: _validated_destination(value, trusted_roots)
            for root_id, value in mappings.items()
            if str(value or "").strip()
        }
        if not destinations:
            return {"mapped_roots": 0, "rewritten_paths": 0}

        with connection:
            for root_id, destination in destinations.items():
                old_root = roots[root_id]
                connection.execute(
                    "UPDATE roots SET path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (str(destination), root_id),
                )
                rewritten += 1

                title_rows = connection.execute(
                    "SELECT id,folder_path FROM titles WHERE root_id=?", (root_id,)
                ).fetchall()
                for row in title_rows:
                    new_value = _rewritten_path(str(row["folder_path"] or ""), old_root, destination)
                    connection.execute(
                        "UPDATE titles SET folder_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (new_value, row["id"]),
                    )
                    rewritten += 1

                file_rows = connection.execute(
                    """SELECT f.id,f.path FROM files f
                       JOIN titles t ON t.id=f.title_id WHERE t.root_id=?""",
                    (root_id,),
                ).fetchall()
                for row in file_rows:
                    new_value = _rewritten_path(str(row["path"] or ""), old_root, destination)
                    connection.execute(
                        "UPDATE files SET path=? WHERE id=?", (new_value, row["id"])
                    )
                    rewritten += 1

                if "duplicate_trash" in tables:
                    trash_rows = connection.execute(
                        "SELECT id,original_path,trash_path FROM duplicate_trash WHERE root_id=?",
                        (root_id,),
                    ).fetchall()
                    for row in trash_rows:
                        original = _rewritten_path(
                            str(row["original_path"] or ""), old_root, destination
                        )
                        trash = _rewritten_path(
                            str(row["trash_path"] or ""), old_root, destination
                        )
                        connection.execute(
                            "UPDATE duplicate_trash SET original_path=?,trash_path=? WHERE id=?",
                            (original, trash, row["id"]),
                        )
                        rewritten += 2

                if "rename_proposals" in tables:
                    proposals = connection.execute(
                        "SELECT id,source_path,destination_path FROM rename_proposals WHERE root_id=?",
                        (root_id,),
                    ).fetchall()
                    for row in proposals:
                        source = _rewritten_path(
                            str(row["source_path"] or ""), old_root, destination
                        )
                        target = _rewritten_path(
                            str(row["destination_path"] or ""), old_root, destination
                        )
                        connection.execute(
                            "UPDATE rename_proposals SET source_path=?,destination_path=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (source, target, row["id"]),
                        )
                        rewritten += 2
    except sqlite3.Error as exc:
        raise RecoveryPackageError(
            "InfoMancer could not reconcile media paths in the staged recovery database."
        ) from exc
    finally:
        connection.close()

    try:
        validate_database_paths(Path(database_path), tuple(Path(root) for root in trusted_roots))
    except MaintenanceError as exc:
        raise RecoveryPackageError(str(exc)) from exc
    return {"mapped_roots": len(destinations), "rewritten_paths": rewritten}


class MappedRecoveryPackageService(RecoveryPackageService):
    """Recovery service that rewrites staged paths before normal safety validation."""

    def __init__(
        self,
        database_path: Path,
        app_version: str,
        mappings: dict[int, str],
        trusted_roots: Iterable[Path],
    ) -> None:
        super().__init__(database_path, app_version)
        self._recovery_mappings = dict(mappings)
        self._trusted_recovery_roots = tuple(Path(root) for root in trusted_roots)
        self.mapping_result = {"mapped_roots": 0, "rewritten_paths": 0}

    def _extract_for_restore(self, package_path: Path, staging: Path):
        summary, database, artwork = super()._extract_for_restore(package_path, staging)
        self.mapping_result = apply_recovery_root_mappings(
            database,
            self._recovery_mappings,
            self._trusted_recovery_roots,
        )
        summary = {**summary, "path_reconciliation": dict(self.mapping_result)}
        return summary, database, artwork

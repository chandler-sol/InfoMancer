from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from .maintenance import MaintenanceError, validate_database_backup


class RecoveryPackageError(ValueError):
    pass


class RecoveryPackageService:
    FORMAT = "infomancer-recovery"
    FORMAT_VERSION = 1
    MAX_PACKAGE_BYTES = 4 * 1024 * 1024 * 1024
    MAX_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
    MAX_ENTRIES = 10_000
    MAX_MANIFEST_BYTES = 1024 * 1024

    def __init__(self, database_path: Path, app_version: str) -> None:
        self.database_path = Path(database_path)
        self.app_version = app_version
        self.artwork_dir = self.database_path.parent / "collection-art"
        self.output_dir = self.database_path.parent / "recovery-packages"

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _sha256_stream(handle: BinaryIO) -> str:
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _safe_member(name: str) -> bool:
        if not name or "\\" in name or "\x00" in name:
            return False
        path = PurePosixPath(name)
        return not path.is_absolute() and ".." not in path.parts

    def _database_snapshot(self, destination: Path) -> None:
        try:
            source = sqlite3.connect(self.database_path, timeout=30)
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
            validate_database_backup(destination)
        except (sqlite3.Error, OSError, MaintenanceError) as exc:
            destination.unlink(missing_ok=True)
            raise RecoveryPackageError(
                "InfoMancer could not create a verified database snapshot for the recovery package."
            ) from exc

    def _artwork_files(self) -> list[Path]:
        if not self.artwork_dir.is_dir():
            return []
        files: list[Path] = []
        for candidate in self.artwork_dir.rglob("*"):
            if candidate.is_symlink():
                continue
            if candidate.is_file():
                try:
                    candidate.resolve(strict=True).relative_to(self.artwork_dir.resolve(strict=True))
                except (OSError, ValueError):
                    continue
                files.append(candidate)
        return sorted(files, key=lambda item: item.as_posix().casefold())

    def create(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        final = self.output_dir / f"infomancer-recovery-{timestamp}.infomancer-backup"
        temp_package: Path | None = None
        temp_database: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.database_path.parent, prefix="recovery-db-", suffix=".db", delete=False
            ) as handle:
                temp_database = Path(handle.name)
            self._database_snapshot(temp_database)
            with tempfile.NamedTemporaryFile(
                dir=self.output_dir, prefix="recovery-package-", suffix=".tmp", delete=False
            ) as handle:
                temp_package = Path(handle.name)

            manifest_files: list[dict] = []
            with zipfile.ZipFile(
                temp_package, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6,
            ) as archive:
                db_member = "database/infomancer.db"
                archive.write(temp_database, db_member)
                manifest_files.append({
                    "path": db_member,
                    "role": "database",
                    "size": temp_database.stat().st_size,
                    "sha256": self._sha256_file(temp_database),
                })
                for artwork in self._artwork_files():
                    relative = artwork.relative_to(self.artwork_dir).as_posix()
                    member = f"collection-art/{relative}"
                    archive.write(artwork, member)
                    manifest_files.append({
                        "path": member,
                        "role": "collection-artwork",
                        "size": artwork.stat().st_size,
                        "sha256": self._sha256_file(artwork),
                    })
                manifest = {
                    "format": self.FORMAT,
                    "format_version": self.FORMAT_VERSION,
                    "app_version": self.app_version,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "files": manifest_files,
                    "contains_media": False,
                    "excluded": [
                        "movie and TV media files",
                        "provider credentials and provider-secret encryption keys",
                        "deployment environment files",
                        "application binaries and caches",
                    ],
                    "notes": (
                        "The database contains the catalog, accounts, source definitions, settings, "
                        "collections, favorites, tags, ratings, operation history, and other persisted state. "
                        "Provider credentials must be entered again after recovery."
                    ),
                }
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
                )
            if temp_package.stat().st_size > self.MAX_PACKAGE_BYTES:
                raise RecoveryPackageError("The recovery package exceeded the 4 GB safety limit.")
            self.verify(temp_package)
            os.replace(temp_package, final)
            temp_package = None
            return final
        except (OSError, zipfile.BadZipFile, RecoveryPackageError) as exc:
            if isinstance(exc, RecoveryPackageError):
                raise
            raise RecoveryPackageError(
                "InfoMancer could not finish the recovery package. Check free disk space and application-data permissions."
            ) from exc
        finally:
            if temp_database:
                temp_database.unlink(missing_ok=True)
            if temp_package:
                temp_package.unlink(missing_ok=True)

    def verify(self, package_path: Path) -> dict:
        package_path = Path(package_path)
        if not package_path.is_file():
            raise RecoveryPackageError("The recovery package could not be found.")
        if package_path.stat().st_size > self.MAX_PACKAGE_BYTES:
            raise RecoveryPackageError("The recovery package is larger than the 4 GB safety limit.")
        temp_database: Path | None = None
        try:
            with zipfile.ZipFile(package_path, "r") as archive:
                infos = archive.infolist()
                if not infos or len(infos) > self.MAX_ENTRIES:
                    raise RecoveryPackageError("The recovery package has an invalid number of files.")
                names = [item.filename for item in infos]
                if len(names) != len(set(names)):
                    raise RecoveryPackageError("The recovery package contains duplicate archive paths.")
                if any(not self._safe_member(name) for name in names):
                    raise RecoveryPackageError("The recovery package contains an unsafe archive path.")
                total = sum(int(item.file_size) for item in infos)
                if total > self.MAX_UNCOMPRESSED_BYTES:
                    raise RecoveryPackageError("The recovery package expands beyond the 4 GB safety limit.")
                try:
                    manifest_info = archive.getinfo("manifest.json")
                except KeyError as exc:
                    raise RecoveryPackageError("The recovery package is missing its manifest.") from exc
                if manifest_info.file_size > self.MAX_MANIFEST_BYTES:
                    raise RecoveryPackageError("The recovery package manifest is too large.")
                try:
                    manifest = json.loads(archive.read(manifest_info))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise RecoveryPackageError("The recovery package manifest is unreadable.") from exc
                if not isinstance(manifest, dict) or manifest.get("format") != self.FORMAT:
                    raise RecoveryPackageError("This is not an InfoMancer recovery package.")
                if manifest.get("format_version") != self.FORMAT_VERSION:
                    raise RecoveryPackageError(
                        "This recovery package uses a format this version of InfoMancer does not support."
                    )
                file_records = manifest.get("files")
                if not isinstance(file_records, list) or not file_records:
                    raise RecoveryPackageError("The recovery package manifest has no files to restore.")
                expected_names = {"manifest.json"}
                database_record = None
                artwork_count = 0
                for record in file_records:
                    if not isinstance(record, dict):
                        raise RecoveryPackageError("The recovery package manifest contains an invalid file record.")
                    name = str(record.get("path") or "")
                    if not self._safe_member(name) or name == "manifest.json":
                        raise RecoveryPackageError("The recovery package manifest contains an unsafe file path.")
                    if name in expected_names:
                        raise RecoveryPackageError("The recovery package manifest repeats a file path.")
                    expected_names.add(name)
                    try:
                        info = archive.getinfo(name)
                    except KeyError as exc:
                        raise RecoveryPackageError(f"The recovery package is missing {name}.") from exc
                    if int(record.get("size", -1)) != int(info.file_size):
                        raise RecoveryPackageError(f"The recovery package size check failed for {name}.")
                    expected_hash = str(record.get("sha256") or "").casefold()
                    if len(expected_hash) != 64:
                        raise RecoveryPackageError(f"The recovery package checksum is invalid for {name}.")
                    with archive.open(info, "r") as stream:
                        actual_hash = self._sha256_stream(stream)
                    if actual_hash != expected_hash:
                        raise RecoveryPackageError(f"The recovery package checksum failed for {name}.")
                    role = record.get("role")
                    if role == "database":
                        if database_record is not None or name != "database/infomancer.db":
                            raise RecoveryPackageError("The recovery package has an invalid database entry.")
                        database_record = record
                    elif role == "collection-artwork":
                        if not name.startswith("collection-art/"):
                            raise RecoveryPackageError("The recovery package has an invalid artwork entry.")
                        artwork_count += 1
                    else:
                        raise RecoveryPackageError("The recovery package contains an unsupported file role.")
                if set(names) != expected_names:
                    raise RecoveryPackageError("The recovery package contains files not declared in its manifest.")
                if database_record is None:
                    raise RecoveryPackageError("The recovery package does not contain an InfoMancer database.")
                with tempfile.NamedTemporaryFile(
                    dir=self.database_path.parent, prefix="verify-recovery-", suffix=".db", delete=False
                ) as handle:
                    temp_database = Path(handle.name)
                    with archive.open("database/infomancer.db", "r") as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            handle.write(chunk)
            try:
                validate_database_backup(temp_database)
            except MaintenanceError as exc:
                raise RecoveryPackageError(str(exc)) from exc
            return {
                "app_version": str(manifest.get("app_version") or "unknown"),
                "created_at": str(manifest.get("created_at") or ""),
                "files": len(file_records),
                "artwork_files": artwork_count,
                "database_size": int(database_record["size"]),
            }
        except zipfile.BadZipFile as exc:
            raise RecoveryPackageError("The selected file is not a readable recovery package.") from exc
        finally:
            if temp_database:
                temp_database.unlink(missing_ok=True)

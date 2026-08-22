from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable

from .maintenance import (
    MaintenanceError,
    validate_database_backup,
    validate_database_paths,
)


class RecoveryPackageError(ValueError):
    pass


class RecoveryPackageService:
    FORMAT = "infomancer-recovery"
    FORMAT_VERSION = 1
    MAX_PACKAGE_BYTES = 4 * 1024 * 1024 * 1024
    MAX_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
    MAX_ENTRIES = 10_000
    MAX_MANIFEST_BYTES = 1024 * 1024
    MAX_COMPRESSION_RATIO = 250
    MAX_MEMBER_PATH_LENGTH = 1024
    MAX_MEMBER_DEPTH = 32
    MAX_MEMBER_COMPONENT_LENGTH = 255
    WINDOWS_RESERVED = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
    WINDOWS_INVALID = set('<>:"|?*')

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

    @classmethod
    def _safe_member(cls, name: str) -> bool:
        if (
            not name
            or len(name) > cls.MAX_MEMBER_PATH_LENGTH
            or "\\" in name
            or "\x00" in name
        ):
            return False
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != name
            or len(path.parts) > cls.MAX_MEMBER_DEPTH
        ):
            return False
        for part in path.parts:
            if (
                not part
                or len(part) > cls.MAX_MEMBER_COMPONENT_LENGTH
                or part.rstrip(" .") != part
                or any(ord(character) < 32 for character in part)
                or any(character in cls.WINDOWS_INVALID for character in part)
                or part.split(".", 1)[0].upper() in cls.WINDOWS_RESERVED
            ):
                return False
        return True

    @staticmethod
    def _portable_member_key(name: str) -> str:
        return "/".join(
            unicodedata.normalize("NFC", part).casefold()
            for part in PurePosixPath(name).parts
        )

    @classmethod
    def _validate_zip_info(cls, info: zipfile.ZipInfo) -> None:
        if info.is_dir() or not cls._safe_member(info.filename):
            raise RecoveryPackageError("The recovery package contains an unsafe archive path.")
        if info.flag_bits & 0x1:
            raise RecoveryPackageError("Encrypted recovery package entries are not supported.")
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise RecoveryPackageError("The recovery package uses an unsupported compression method.")
        if info.file_size > 0:
            if info.compress_size <= 0:
                raise RecoveryPackageError("The recovery package contains an invalid compressed file.")
            if info.file_size > info.compress_size * cls.MAX_COMPRESSION_RATIO:
                raise RecoveryPackageError(
                    "The recovery package contains a file with an unsafe compression ratio."
                )

    @staticmethod
    def _manifest_size(record: dict, name: str) -> int:
        value = record.get("size")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RecoveryPackageError(
                f"The recovery package has an invalid size value for {name}."
            )
        return value

    @staticmethod
    def _manifest_hash(record: dict, name: str) -> str:
        value = record.get("sha256")
        if not isinstance(value, str):
            raise RecoveryPackageError(
                f"The recovery package checksum is invalid for {name}."
            )
        value = value.casefold()
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise RecoveryPackageError(
                f"The recovery package checksum is invalid for {name}."
            )
        return value

    @staticmethod
    def _replace(source: Path, destination: Path) -> None:
        """Small seam used by restore fault-injection tests."""
        os.replace(source, destination)

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
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
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

    def _read_manifest(self, archive: zipfile.ZipFile) -> dict:
        try:
            manifest_info = archive.getinfo("manifest.json")
        except KeyError as exc:
            raise RecoveryPackageError("The recovery package is missing its manifest.") from exc
        if manifest_info.file_size > self.MAX_MANIFEST_BYTES:
            raise RecoveryPackageError("The recovery package manifest is too large.")
        try:
            manifest = json.loads(archive.read(manifest_info))
        except (json.JSONDecodeError, UnicodeDecodeError, RuntimeError, NotImplementedError) as exc:
            raise RecoveryPackageError("The recovery package manifest is unreadable.") from exc
        if not isinstance(manifest, dict) or manifest.get("format") != self.FORMAT:
            raise RecoveryPackageError("This is not an InfoMancer recovery package.")
        if manifest.get("format_version") != self.FORMAT_VERSION:
            raise RecoveryPackageError(
                "This recovery package uses a format this version of InfoMancer does not support."
            )
        return manifest

    def verify(self, package_path: Path) -> dict:
        package_path = Path(package_path)
        if not package_path.is_file():
            raise RecoveryPackageError("The recovery package could not be found.")
        if package_path.stat().st_size > self.MAX_PACKAGE_BYTES:
            raise RecoveryPackageError("The recovery package is larger than the 4 GB safety limit.")
        temp_database: Path | None = None
        manifest: dict = {}
        file_records: list[dict] = []
        database_record: dict | None = None
        artwork_count = 0
        try:
            with zipfile.ZipFile(package_path, "r") as archive:
                infos = archive.infolist()
                if not infos or len(infos) > self.MAX_ENTRIES:
                    raise RecoveryPackageError("The recovery package has an invalid number of files.")
                for info in infos:
                    self._validate_zip_info(info)
                names = [item.filename for item in infos]
                if len(names) != len(set(names)):
                    raise RecoveryPackageError("The recovery package contains duplicate archive paths.")
                portable_names = [self._portable_member_key(name) for name in names]
                if len(portable_names) != len(set(portable_names)):
                    raise RecoveryPackageError(
                        "The recovery package contains archive paths that collide on another supported platform."
                    )
                total = sum(int(item.file_size) for item in infos)
                if total > self.MAX_UNCOMPRESSED_BYTES:
                    raise RecoveryPackageError("The recovery package expands beyond the 4 GB safety limit.")
                manifest = self._read_manifest(archive)
                records = manifest.get("files")
                if not isinstance(records, list) or not records:
                    raise RecoveryPackageError("The recovery package manifest has no files to restore.")
                file_records = records
                expected_names = {"manifest.json"}
                for record in file_records:
                    if not isinstance(record, dict):
                        raise RecoveryPackageError("The recovery package manifest contains an invalid file record.")
                    raw_name = record.get("path")
                    if not isinstance(raw_name, str):
                        raise RecoveryPackageError("The recovery package manifest contains an unsafe file path.")
                    name = raw_name
                    if not self._safe_member(name) or name == "manifest.json":
                        raise RecoveryPackageError("The recovery package manifest contains an unsafe file path.")
                    if name in expected_names:
                        raise RecoveryPackageError("The recovery package manifest repeats a file path.")
                    expected_names.add(name)
                    try:
                        info = archive.getinfo(name)
                    except KeyError as exc:
                        raise RecoveryPackageError(f"The recovery package is missing {name}.") from exc
                    expected_size = self._manifest_size(record, name)
                    if expected_size != int(info.file_size):
                        raise RecoveryPackageError(f"The recovery package size check failed for {name}.")
                    expected_hash = self._manifest_hash(record, name)
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
                "database_size": self._manifest_size(database_record, "database/infomancer.db"),
                "contains_media": bool(manifest.get("contains_media", False)),
                "excluded": list(manifest.get("excluded") or []),
                "notes": str(manifest.get("notes") or ""),
            }
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as exc:
            raise RecoveryPackageError("The selected file is not a readable recovery package.") from exc
        finally:
            if temp_database:
                temp_database.unlink(missing_ok=True)

    def _extract_for_restore(self, package_path: Path, staging: Path) -> tuple[dict, Path, Path]:
        """Verify first, then re-check every extracted payload before it can be used."""
        summary = self.verify(package_path)
        staging = staging.resolve(strict=False)
        database = staging / "database" / "infomancer.db"
        artwork = staging / "collection-art"
        database.parent.mkdir(parents=True, exist_ok=True)
        artwork.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(package_path, "r") as archive:
                manifest = self._read_manifest(archive)
                records = manifest.get("files")
                if not isinstance(records, list) or not records:
                    raise RecoveryPackageError("The recovery package manifest has no files to restore.")
                for record in records:
                    if not isinstance(record, dict):
                        raise RecoveryPackageError("The recovery package manifest contains an invalid file record.")
                    raw_name = record.get("path")
                    if not isinstance(raw_name, str) or not self._safe_member(raw_name):
                        raise RecoveryPackageError("The recovery package manifest contains an unsafe file path.")
                    name = raw_name
                    role = record.get("role")
                    try:
                        info = archive.getinfo(name)
                    except KeyError as exc:
                        raise RecoveryPackageError(f"The recovery package is missing {name}.") from exc
                    self._validate_zip_info(info)
                    expected_size = self._manifest_size(record, name)
                    expected_hash = self._manifest_hash(record, name)
                    if expected_size != int(info.file_size):
                        raise RecoveryPackageError(f"The recovery package size check failed for {name}.")
                    if role == "database":
                        if name != "database/infomancer.db":
                            raise RecoveryPackageError("The recovery package has an invalid database entry.")
                        destination = database
                    elif role == "collection-artwork":
                        if not name.startswith("collection-art/"):
                            raise RecoveryPackageError("The recovery package has an invalid artwork entry.")
                        relative = PurePosixPath(name).relative_to("collection-art")
                        destination = artwork.joinpath(*relative.parts)
                    else:
                        raise RecoveryPackageError("The recovery package contains an unsupported file role.")
                    try:
                        destination.resolve(strict=False).relative_to(staging)
                    except ValueError as exc:
                        raise RecoveryPackageError(
                            "The recovery package contains an unsafe extraction destination."
                        ) from exc
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info, "r") as source, destination.open("wb") as target:
                        digest = hashlib.sha256()
                        written = 0
                        while chunk := source.read(1024 * 1024):
                            written += len(chunk)
                            if written > expected_size:
                                raise RecoveryPackageError(
                                    f"The staged restore size check failed for {name}."
                                )
                            target.write(chunk)
                            digest.update(chunk)
                    if written != expected_size:
                        raise RecoveryPackageError(f"The staged restore size check failed for {name}.")
                    if digest.hexdigest() != expected_hash:
                        raise RecoveryPackageError(f"The staged restore checksum failed for {name}.")
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as exc:
            raise RecoveryPackageError("The selected file is not a readable recovery package.") from exc
        try:
            validate_database_backup(database)
        except MaintenanceError as exc:
            raise RecoveryPackageError(str(exc)) from exc
        return summary, database, artwork

    def restore(self, package_path: Path, media_browse_roots: Iterable[Path]) -> dict:
        """Restore database + collection artwork as one rollback-protected operation.

        The incoming archive is verified and fully staged before any live file is
        touched. A fresh portable recovery package of the current installation is
        also created before commit. Provider-secret storage is intentionally outside
        this operation and is never read, replaced, or removed.
        """
        package_path = Path(package_path)
        staging_root = Path(tempfile.mkdtemp(prefix="recovery-restore-", dir=self.database_path.parent))
        rollback_art = self.database_path.parent / f".collection-art-rollback-{staging_root.name}"
        rollback_database = staging_root / "rollback-live.db"
        old_art_moved = False
        incoming_art_installed = False
        database_replaced = False
        safety_package: Path | None = None
        try:
            summary, staged_database, staged_artwork = self._extract_for_restore(
                package_path, staging_root
            )
            try:
                validate_database_paths(staged_database, media_browse_roots)
            except MaintenanceError as exc:
                raise RecoveryPackageError(str(exc)) from exc

            # A complete, self-verified package of the current installation is a
            # hard precondition. If this fails, restore stops before touching live state.
            safety_package = self.create()
            self._database_snapshot(rollback_database)

            if rollback_art.exists():
                shutil.rmtree(rollback_art)
            if self.artwork_dir.exists():
                self._replace(self.artwork_dir, rollback_art)
                old_art_moved = True
            self._replace(staged_artwork, self.artwork_dir)
            incoming_art_installed = True

            for suffix in ("-wal", "-shm"):
                Path(str(self.database_path) + suffix).unlink(missing_ok=True)
            self._replace(staged_database, self.database_path)
            database_replaced = True
            validate_database_backup(self.database_path)

            if rollback_art.exists():
                shutil.rmtree(rollback_art)
            rollback_database.unlink(missing_ok=True)
            return {
                **summary,
                "safety_package": safety_package.name,
                "restored_database": self.database_path.name,
                "restored_artwork_files": summary["artwork_files"],
            }
        except (OSError, zipfile.BadZipFile, MaintenanceError, RecoveryPackageError) as exc:
            rollback_failures: list[str] = []
            if database_replaced:
                try:
                    for suffix in ("-wal", "-shm"):
                        Path(str(self.database_path) + suffix).unlink(missing_ok=True)
                    if rollback_database.exists():
                        self._replace(rollback_database, self.database_path)
                        validate_database_backup(self.database_path)
                except Exception as rollback_exc:  # pragma: no cover - emergency path
                    rollback_failures.append(f"database: {rollback_exc}")
            if incoming_art_installed or old_art_moved:
                try:
                    if incoming_art_installed and self.artwork_dir.exists():
                        if self.artwork_dir.is_dir():
                            shutil.rmtree(self.artwork_dir)
                        else:
                            self.artwork_dir.unlink()
                    if old_art_moved and rollback_art.exists():
                        self._replace(rollback_art, self.artwork_dir)
                except Exception as rollback_exc:  # pragma: no cover - emergency path
                    rollback_failures.append(f"collection artwork: {rollback_exc}")
            if rollback_failures:
                safety = safety_package.name if safety_package else "unavailable"
                raise RecoveryPackageError(
                    "Portable recovery failed and automatic rollback was incomplete. "
                    f"Safety package: {safety}. Rollback errors: {'; '.join(rollback_failures)}"
                ) from exc
            if isinstance(exc, RecoveryPackageError):
                raise RecoveryPackageError(
                    f"{exc} The live installation was left unchanged or rolled back safely."
                ) from exc
            raise RecoveryPackageError(
                "Portable recovery could not be completed. The live installation was left unchanged or rolled back safely."
            ) from exc
        finally:
            if rollback_art.exists() and not old_art_moved:
                shutil.rmtree(rollback_art, ignore_errors=True)
            shutil.rmtree(staging_root, ignore_errors=True)

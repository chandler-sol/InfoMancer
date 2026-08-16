from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def add_service() -> None:
    path = ROOT / "app" / "recovery_package.py"
    if path.exists():
        raise RuntimeError("app/recovery_package.py already exists")
    path.write_text(r'''from __future__ import annotations

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
''', encoding="utf-8")


def patch_settings_route() -> None:
    path = "app/routes/settings.py"
    text = read(path)
    text = replace_once(
        text,
        'from ..access import require_librarian\n',
        'from ..access import require_librarian\nfrom ..recovery_package import RecoveryPackageError, RecoveryPackageService\n',
        "recovery package import",
    )
    service_anchor = '    db = ctx.live("db")\n'
    text = replace_once(
        text, service_anchor,
        service_anchor + '    recovery_packages = RecoveryPackageService(db.path, APP_VERSION)\n',
        "recovery package service",
    )
    route_anchor = '''    @librarian_post("/maintenance/backups")
'''
    routes = '''    @librarian_post("/maintenance/recovery-package")
    def create_recovery_package(request: Request):
        try:
            package = recovery_packages.create()
        except RecoveryPackageError as exc:
            record_event(
                "backup", "Portable recovery package could not be created.",
                level="error", detail=str(exc), user_id=request.state.user.id,
            )
            return redirect("/settings/system", str(exc))
        record_event(
            "backup", "Portable recovery package created and verified.",
            context={"name": package.name}, user_id=request.state.user.id,
        )
        return FileResponse(
            package,
            media_type="application/octet-stream",
            filename=package.name,
        )

    @librarian_post("/maintenance/recovery-package/verify")
    async def verify_recovery_package(
        request: Request, recovery_file: UploadFile = File(...),
    ):
        candidate_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=db.path.parent, prefix="verify-recovery-upload-",
                suffix=".infomancer-backup", delete=False,
            ) as candidate:
                candidate_path = Path(candidate.name)
                total = 0
                while chunk := await recovery_file.read(1024 * 1024):
                    total += len(chunk)
                    if total > recovery_packages.MAX_PACKAGE_BYTES:
                        raise RecoveryPackageError(
                            "The uploaded recovery package is larger than the 4 GB verification limit."
                        )
                    candidate.write(chunk)
            result = recovery_packages.verify(candidate_path)
        except (RecoveryPackageError, OSError) as exc:
            record_event(
                "backup", "Uploaded recovery package verification failed.",
                level="error", detail=str(exc), user_id=request.state.user.id,
            )
            return redirect(
                "/settings/system",
                str(exc) if isinstance(exc, RecoveryPackageError)
                else "InfoMancer could not save that package for verification. Check free disk space and permissions.",
            )
        finally:
            if candidate_path:
                candidate_path.unlink(missing_ok=True)
        message = (
            f"Recovery package verified successfully. Database: {result['database_size'] / (1024 * 1024):.1f} MB; "
            f"collection artwork files: {result['artwork_files']}; created by InfoMancer {result['app_version']}."
        )
        record_event(
            "backup", "Uploaded recovery package verified successfully.",
            context={key: value for key, value in result.items() if key != "created_at"},
            user_id=request.state.user.id,
        )
        return redirect("/settings/system", message)

'''
    text = replace_once(text, route_anchor, routes + route_anchor, "recovery package routes")
    return_anchor = '        "create_backup_from_ui": create_backup_from_ui,\n'
    text = replace_once(
        text, return_anchor,
        '        "create_recovery_package": create_recovery_package,\n        "verify_recovery_package": verify_recovery_package,\n' + return_anchor,
        "recovery route aliases",
    )
    write(path, text)


def patch_settings_template() -> None:
    path = "app/templates/settings.html"
    text = read(path)
    anchor = '''    <form class="settings-restore-upload backup-upload-row" method="post" action="/maintenance/restore/upload" enctype="multipart/form-data" onsubmit="return confirm('Restore this uploaded database? Current accounts, catalog data, settings, and organization will be replaced. A safety backup will be created first.');"><label>Restore an uploaded InfoMancer database<input type="file" name="database_file" accept=".db,application/vnd.sqlite3,application/x-sqlite3" required></label><input type="hidden" name="confirm" value="RESTORE"><button class="button danger">Validate and restore</button></form>
  </section>
'''
    replacement = '''    <form class="settings-restore-upload backup-upload-row" method="post" action="/maintenance/restore/upload" enctype="multipart/form-data" onsubmit="return confirm('Restore this uploaded database? Current accounts, catalog data, settings, and organization will be replaced. A safety backup will be created first.');"><label>Restore an uploaded InfoMancer database<input type="file" name="database_file" accept=".db,application/vnd.sqlite3,application/x-sqlite3" required></label><input type="hidden" name="confirm" value="RESTORE"><button class="button danger">Validate and restore</button></form>
    <div class="recovery-package-block">
      <div class="recovery-package-copy"><p class="eyebrow">PORTABLE RECOVERY</p><h3>One-file recovery package</h3><p class="muted">Create one verified package containing the InfoMancer database plus collection artwork. It includes accounts, catalog data, source definitions, settings, organization, and operation history. Media files, provider credentials, encryption keys, deployment files, and caches are never included.</p></div>
      <div class="recovery-package-actions"><form method="post" action="/maintenance/recovery-package"><button class="button primary">Create &amp; download recovery package</button></form><form method="post" action="/maintenance/recovery-package/verify" enctype="multipart/form-data" class="recovery-verify-form"><label>Verify an existing package<input type="file" name="recovery_file" accept=".infomancer-backup,application/octet-stream" required></label><button class="button">Verify package</button></form></div>
    </div>
  </section>
'''
    text = replace_once(text, anchor, replacement, "recovery package settings UI")
    write(path, text)


def patch_css() -> None:
    path = "app/static/workspace.css"
    text = read(path)
    marker = "/* Portable recovery package */"
    if marker in text:
        raise RuntimeError("recovery CSS already exists")
    text += '''

/* Portable recovery package */
.recovery-package-block {align-items:start;border-top:1px solid var(--line);display:grid;gap:20px;grid-template-columns:minmax(0,1fr) minmax(330px,.72fr);margin-top:18px;padding-top:18px}
.recovery-package-copy h3 {font-size:16px;margin:3px 0 7px}
.recovery-package-copy p:last-child {line-height:1.5;margin-bottom:0}
.recovery-package-actions {display:grid;gap:10px}
.recovery-package-actions form {margin:0}
.recovery-verify-form {align-items:end;display:grid;gap:8px;grid-template-columns:minmax(0,1fr) auto}
.recovery-verify-form label {color:var(--muted);display:grid;font-size:11px;gap:5px}
.recovery-verify-form input[type=file] {background:var(--bg);border:1px solid var(--line);border-radius:5px;color:var(--text);font:inherit;padding:7px}
@media (max-width:900px) {.recovery-package-block {grid-template-columns:1fr}.recovery-verify-form {grid-template-columns:1fr}}
'''
    write(path, text)


def add_tests() -> None:
    path = ROOT / "tests" / "test_recovery_package.py"
    if path.exists():
        raise RuntimeError("recovery package tests already exist")
    path.write_text(r'''import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.db import Database
from app.recovery_package import RecoveryPackageError, RecoveryPackageService


class RecoveryPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name)
        self.database = Database(self.data / "infomancer.db")
        self.database.initialize()
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO users(username,display_name,role,password_hash)
                   VALUES ('librarian','Librarian','librarian','test')"""
            )
        artwork = self.data / "collection-art"
        artwork.mkdir()
        (artwork / "collection-1.webp").write_bytes(b"fake artwork")
        self.service = RecoveryPackageService(self.database.path, "0.8-test")

    def tearDown(self):
        self.temporary.cleanup()

    def test_create_is_self_verified_and_contains_database_artwork_and_manifest(self):
        package = self.service.create()
        self.assertEqual(package.suffix, ".infomancer-backup")
        result = self.service.verify(package)
        self.assertEqual(result["app_version"], "0.8-test")
        self.assertEqual(result["artwork_files"], 1)
        with zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
            self.assertEqual(
                names,
                {"manifest.json", "database/infomancer.db", "collection-art/collection-1.webp"},
            )
            manifest = json.loads(archive.read("manifest.json"))
        self.assertFalse(manifest["contains_media"])
        self.assertTrue(any("provider credentials" in item for item in manifest["excluded"]))

    def test_verify_rejects_traversal_even_when_manifest_names_it(self):
        package = self.data / "evil.infomancer-backup"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("../escape.txt", b"bad")
            archive.writestr("manifest.json", json.dumps({
                "format": "infomancer-recovery", "format_version": 1,
                "files": [{"path": "../escape.txt", "role": "collection-artwork", "size": 3, "sha256": "0" * 64}],
            }))
        with self.assertRaisesRegex(RecoveryPackageError, "unsafe archive path"):
            self.service.verify(package)

    def test_verify_rejects_checksum_tampering(self):
        package = self.service.create()
        rebuilt = self.data / "tampered.infomancer-backup"
        with zipfile.ZipFile(package, "r") as source, zipfile.ZipFile(rebuilt, "w") as target:
            for item in source.infolist():
                payload = source.read(item)
                if item.filename == "collection-art/collection-1.webp":
                    payload = b"evil artwork"
                target.writestr(item.filename, payload)
        with self.assertRaisesRegex(RecoveryPackageError, "checksum failed"):
            self.service.verify(rebuilt)


class RecoveryPackageUiContractTests(unittest.TestCase):
    def test_system_settings_explain_portable_recovery_scope(self):
        root = Path(__file__).resolve().parents[1]
        settings = (root / "app/templates/settings.html").read_text(encoding="utf-8")
        routes = (root / "app/routes/settings.py").read_text(encoding="utf-8")
        self.assertIn("Create &amp; download recovery package", settings)
        self.assertIn("provider credentials", settings)
        self.assertIn('action="/maintenance/recovery-package/verify"', settings)
        self.assertIn('recovery_packages.verify(candidate_path)', routes)


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")


def patch_docs() -> None:
    path = "docs/PACKAGING.md"
    text = read(path)
    anchor = '''A complete uninstall removes application binaries, databases, configuration,
provider-secret/encryption-key files, artwork, caches, logs, updater data,
'''
    section = '''The recovery choice uses InfoMancer's portable `.infomancer-backup` format. The
package contains a verified SQLite snapshot plus collection artwork and a signed-by-
content manifest of SHA-256 checksums. The database carries accounts, catalog data,
source definitions, settings, collections, favorites, tags, and operation history.
Media files, provider credentials, local encryption keys, deployment environment
files, binaries, and caches are intentionally excluded. Provider credentials must be
entered again after recovery. The same package creator/verifier is exposed in App
Settings so users can test the format before a native installer exists.

'''
    text = replace_once(text, anchor, section + anchor, "packaging recovery format docs")
    write(path, text)

    path = "docs/WORKSPACE.md"
    text = read(path)
    marker = "## Persisted Global Rename Review\n"
    section = '''## Portable Recovery Package

System Settings can create a single `.infomancer-backup` file for disaster recovery
and future native-uninstall handoff. The package contains a consistent SQLite backup,
collection artwork, a versioned manifest, file sizes, and SHA-256 checksums. Creation
verifies the package before download, and an existing package can be uploaded for
verification without changing the live installation. Archive traversal, undeclared
entries, duplicate paths, checksum mismatches, oversized packages, invalid databases,
and unsupported format versions are rejected.

The recovery format never contains movie or TV media, provider credentials or their
local encryption keys, deployment environment files, application binaries, or caches.
This keeps the package portable without weakening provider-secret encryption.

'''
    text = replace_once(text, marker, section + marker, "workspace recovery docs")
    write(path, text)


def main() -> None:
    add_service()
    patch_settings_route()
    patch_settings_template()
    patch_css()
    add_tests()
    patch_docs()


if __name__ == "__main__":
    main()

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.db import Database
from app.maintenance import MaintenanceError, create_database_backup, validate_database_backup
from app.recovery_package import RecoveryPackageError, RecoveryPackageService


class DataDurabilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.path = self.base / "infomancer.db"
        self.database = Database(self.path)
        self.database.initialize()
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO app_settings(key,value) VALUES (?,?)",
                ("durability_sentinel", "before"),
            )
        self.recovery = RecoveryPackageService(self.path, "0.8-test")

    def tearDown(self):
        self.temporary.cleanup()

    def integrity(self) -> str:
        connection = sqlite3.connect(self.path)
        try:
            return str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            connection.close()

    def setting(self) -> str:
        connection = sqlite3.connect(self.path)
        try:
            return str(connection.execute(
                "SELECT value FROM app_settings WHERE key='durability_sentinel'"
            ).fetchone()[0])
        finally:
            connection.close()

    def test_committed_wal_survives_abrupt_process_exit(self):
        script = r'''
import os, sqlite3, sys
path = sys.argv[1]
conn = sqlite3.connect(path)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA wal_autocheckpoint=0")
conn.execute("UPDATE app_settings SET value='committed-before-crash' WHERE key='durability_sentinel'")
conn.commit()
os._exit(23)
'''
        result = subprocess.run(
            [sys.executable, "-c", script, str(self.path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )
        self.assertEqual(result.returncode, 23, result.stderr.decode(errors="replace"))
        self.assertEqual(self.setting(), "committed-before-crash")
        self.assertEqual(self.integrity(), "ok")
        validate_database_backup(self.path)

    def test_truncated_database_copy_is_rejected_without_touching_live_database(self):
        backup = create_database_backup(self.path, "durability")
        corrupt = self.base / "corrupt.db"
        payload = backup.read_bytes()
        corrupt.write_bytes(payload[: max(1, len(payload) // 5)])
        with self.assertRaises(MaintenanceError):
            validate_database_backup(corrupt)
        self.assertEqual(self.setting(), "before")
        self.assertEqual(self.integrity(), "ok")

    def test_interrupted_recovery_package_creation_leaves_no_advertised_partial_archive(self):
        original_write = __import__("zipfile").ZipFile.write
        calls = {"count": 0}

        def fail_after_first_write(archive, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] > 1:
                raise OSError(28, "simulated disk full")
            return original_write(archive, *args, **kwargs)

        artwork = self.base / "collection-art"
        artwork.mkdir(exist_ok=True)
        (artwork / "one.webp").write_bytes(b"one")
        with mock.patch("zipfile.ZipFile.write", new=fail_after_first_write):
            with self.assertRaisesRegex(RecoveryPackageError, "could not finish"):
                self.recovery.create()

        output = self.base / "recovery-packages"
        if output.exists():
            self.assertEqual(list(output.glob("*.tmp")), [])
            self.assertEqual(list(output.glob("*.infomancer-backup")), [])
        self.assertEqual(self.setting(), "before")
        self.assertEqual(self.integrity(), "ok")

    def test_recovery_rejects_tampered_database_before_live_mutation(self):
        package = self.recovery.create()
        tampered = self.base / "tampered.infomancer-backup"
        import zipfile
        with zipfile.ZipFile(package, "r") as source, zipfile.ZipFile(tampered, "w") as target:
            for item in source.infolist():
                data = source.read(item)
                if item.filename == "database/infomancer.db":
                    data = data[:-1] + bytes([data[-1] ^ 1])
                target.writestr(item, data)
        before = self.setting()
        with self.assertRaisesRegex(RecoveryPackageError, "checksum failed"):
            self.recovery.restore(tampered, (self.base,))
        self.assertEqual(self.setting(), before)
        self.assertEqual(self.integrity(), "ok")

    def test_interrupted_fingerprint_state_is_requeued_on_service_restart(self):
        with self.database.connect() as conn:
            root_id = conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES (?,?,?)",
                (str(self.base), "movie", "Movies"),
            ).lastrowid
            title_id = conn.execute(
                "INSERT INTO titles(root_id,kind,title,folder_path) VALUES (?,?,?,?)",
                (root_id, "movie", "Durability Film", str(self.base)),
            ).lastrowid
            media = self.base / "film.mkv"
            media.write_bytes(b"media")
            file_id = conn.execute(
                """INSERT INTO files(title_id,path,filename,extension,size_bytes,modified_at,seen_scan)
                   VALUES (?,?,?,?,?,?,?)""",
                (title_id, str(media), media.name, ".mkv", media.stat().st_size, media.stat().st_mtime, "test"),
            ).lastrowid
            conn.execute(
                """INSERT INTO media_file_hashes(file_id,size_bytes,modified_at,status,updated_at)
                   VALUES (?,?,?,'running',CURRENT_TIMESTAMP)""",
                (file_id, media.stat().st_size, media.stat().st_mtime),
            )
        from app.file_hashes import MediaHashService
        MediaHashService(self.database)
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT status,error FROM media_file_hashes WHERE file_id=?", (file_id,)
            ).fetchone()
        self.assertEqual(row["status"], "queued")
        self.assertIn("interrupted", row["error"].lower())


if __name__ == "__main__":
    unittest.main()

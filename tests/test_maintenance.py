from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.maintenance import (
    MaintenanceError,
    create_database_backup,
    install_database_backup,
    list_database_backups,
    read_update_status,
    resolve_backup,
    validate_database_backup,
    write_update_request,
    write_update_status,
)


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.path = self.base / "infomancer.db"
        self.database = Database(self.path)
        self.database.initialize()
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO app_settings(key,value) VALUES (?,?)",
                ("installation_name", "Before"),
            )

    def tearDown(self):
        self.temporary.cleanup()

    def setting(self) -> str:
        connection = sqlite3.connect(self.path)
        try:
            return connection.execute(
                "SELECT value FROM app_settings WHERE key='installation_name'"
            ).fetchone()[0]
        finally:
            connection.close()

    def test_backup_is_valid_listed_and_download_name_is_restricted(self):
        first = create_database_backup(self.path)
        second = create_database_backup(self.path)
        validate_database_backup(first)
        self.assertNotEqual(first.name, second.name)
        self.assertEqual(len(list_database_backups(self.path)), 2)
        self.assertEqual(resolve_backup(self.path, first.name), first)
        with self.assertRaisesRegex(MaintenanceError, "not valid"):
            resolve_backup(self.path, "../infomancer.db")

    def test_restore_replaces_database_and_retains_safety_backup(self):
        backup = create_database_backup(self.path)
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE app_settings SET value='After' WHERE key='installation_name'"
            )
        safety = install_database_backup(self.path, backup)
        self.assertEqual(self.setting(), "Before")
        self.assertTrue(safety.is_file())
        validate_database_backup(safety)

    def test_non_infomancer_database_is_rejected(self):
        invalid = self.base / "invalid.db"
        connection = sqlite3.connect(invalid)
        try:
            connection.execute("CREATE TABLE unrelated(value TEXT)")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(MaintenanceError, "not an InfoMancer backup"):
            validate_database_backup(invalid)

    def test_update_request_is_validated_and_status_failure_is_plain(self):
        request = write_update_request(self.path, "v1.2.3", "Librarian")
        self.assertEqual(json.loads(request.read_text())["tag"], "v1.2.3")
        with self.assertRaisesRegex(MaintenanceError, "not valid"):
            write_update_request(self.path, "main; rm -rf", "Librarian")
        for invalid in ("v1.2.3-", "v1.2", "v1.2.3/../../main", "v１.2.3"):
            with self.subTest(tag=invalid), self.assertRaisesRegex(
                MaintenanceError, "not valid"
            ):
                write_update_request(self.path, invalid, "Librarian")
        (self.base / "update-status.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(read_update_status(self.path)["status"], "error")
        write_update_status(self.path, {"status": "requested"})
        self.assertEqual(read_update_status(self.path)["status"], "requested")


if __name__ == "__main__":
    unittest.main()

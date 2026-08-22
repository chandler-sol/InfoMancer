from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.maintenance import (
    MaintenanceError,
    backup_directory,
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
        self.assertEqual(resolve_backup(self.path, first.name), first.resolve())
        with self.assertRaisesRegex(MaintenanceError, "not valid"):
            resolve_backup(self.path, "../infomancer.db")

    def test_backup_listing_and_resolver_reject_symlinked_database(self):
        outside = self.base / "outside.db"
        outside.write_bytes(self.path.read_bytes())
        directory = backup_directory(self.path)
        linked = directory / "infomancer-backup-20260821-120000.db"
        try:
            linked.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks are unavailable in this test environment: {exc}")
        self.assertNotIn(linked.name, {item["name"] for item in list_database_backups(self.path)})
        with self.assertRaisesRegex(MaintenanceError, "not safe"):
            resolve_backup(self.path, linked.name)

    def test_backup_creation_does_not_follow_dangling_symlink_collision(self):
        directory = backup_directory(self.path)
        real_exists = Path.exists
        real_is_symlink = Path.is_symlink
        collision_seen = {"value": False}

        def fake_exists(candidate: Path) -> bool:
            if candidate.parent == directory and candidate.name.startswith("infomancer-backup-"):
                return False
            return real_exists(candidate)

        def fake_is_symlink(candidate: Path) -> bool:
            if (
                candidate.parent == directory
                and candidate.name.startswith("infomancer-backup-")
                and not candidate.name.endswith("-2.db")
            ):
                collision_seen["value"] = True
                return True
            return real_is_symlink(candidate)

        from unittest.mock import patch
        with patch.object(Path, "exists", fake_exists), patch.object(Path, "is_symlink", fake_is_symlink):
            backup = create_database_backup(self.path)
        self.assertTrue(collision_seen["value"])
        self.assertTrue(backup.name.endswith("-2.db"))
        validate_database_backup(backup)

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

    def test_restore_rejects_catalog_paths_outside_trusted_storage(self):
        media = self.base / "media"
        root = media / "Movies"
        title_folder = root / "Example"
        root.mkdir(parents=True)
        with self.database.connect() as connection:
            root_id = connection.execute(
                "INSERT INTO roots(path,kind,label) VALUES (?,'movie','Movies')",
                (str(root),),
            ).lastrowid
            title_id = connection.execute(
                """INSERT INTO titles(root_id,kind,title,folder_path)
                   VALUES (?,'movie','Example',?)""",
                (root_id, str(title_folder)),
            ).lastrowid
            connection.execute(
                """INSERT INTO files(title_id,path,filename,extension,seen_scan)
                   VALUES (?,?,?,?,?)""",
                (title_id, str(title_folder / "movie.mkv"), "movie.mkv", ".mkv", "scan"),
            )
        backup = create_database_backup(self.path)
        connection = sqlite3.connect(backup)
        try:
            connection.execute(
                "UPDATE files SET path='/outside/trusted/storage/movie.mkv'"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(MaintenanceError, "media-file path"):
            install_database_backup(self.path, backup, (media,))

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

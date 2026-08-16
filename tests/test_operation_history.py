from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.duplicate_trash import DuplicateTrashService
from app.operation_history import OperationHistoryError, OperationHistoryService


class OperationHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "media"
        self.root.mkdir()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            self.user_id = int(conn.execute(
                """INSERT INTO users(username,display_name,role,password_hash)
                   VALUES ('librarian','Librarian','librarian','test')"""
            ).lastrowid)
            self.root_id = int(conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES (?,?,?)",
                (str(self.root), "tv", "TV"),
            ).lastrowid)
            self.show_folder = self.root / "Example Show"
            self.show_folder.mkdir()
            self.title_id = int(conn.execute(
                """INSERT INTO titles(root_id,kind,title,folder_path)
                   VALUES (?,?,?,?)""",
                (self.root_id, "tv", "Example Show", str(self.show_folder)),
            ).lastrowid)
            self.file_path = self.show_folder / "old.mkv"
            self.file_path.write_bytes(b"media")
            self.file_id = int(conn.execute(
                """INSERT INTO files(title_id,path,filename,extension,seen_scan)
                   VALUES (?,?,?,?,?)""",
                (self.title_id, str(self.file_path), self.file_path.name, ".mkv", "scan"),
            ).lastrowid)
        self.history = OperationHistoryService(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def test_synthetic_auth_disabled_actor_is_recorded_as_system(self):
        operation_id = self.history.record(
            "rename_file", "Synthetic actor test", actor_user_id=999999,
        )
        with self.database.connect() as conn:
            actor = conn.execute(
                "SELECT actor_user_id FROM operation_history WHERE id=?", (operation_id,)
            ).fetchone()["actor_user_id"]
        self.assertIsNone(actor)

    def test_file_rename_undo_revalidates_and_restores_catalog(self):
        destination = self.show_folder / "new.mkv"
        self.file_path.rename(destination)
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE files SET path=?,filename=? WHERE id=?",
                (str(destination), destination.name, self.file_id),
            )
        operation_id = self.history.record_file_rename(
            self.file_id, self.file_path, destination, self.user_id,
        )
        message = self.history.undo(operation_id, self.user_id)
        self.assertIn("Restored", message)
        self.assertTrue(self.file_path.is_file())
        self.assertFalse(destination.exists())
        with self.database.connect() as conn:
            row = conn.execute("SELECT path,filename FROM files WHERE id=?", (self.file_id,)).fetchone()
            operation = conn.execute("SELECT status,undone_by FROM operation_history WHERE id=?", (operation_id,)).fetchone()
        self.assertEqual(row["path"], str(self.file_path))
        self.assertEqual(row["filename"], "old.mkv")
        self.assertEqual(operation["status"], "undone")
        self.assertEqual(operation["undone_by"], self.user_id)

    def test_file_undo_refuses_collision_and_keeps_operation_available(self):
        destination = self.show_folder / "new.mkv"
        self.file_path.rename(destination)
        with self.database.connect() as conn:
            conn.execute("UPDATE files SET path=?,filename=? WHERE id=?", (str(destination), destination.name, self.file_id))
        operation_id = self.history.record_file_rename(self.file_id, self.file_path, destination, self.user_id)
        self.file_path.write_bytes(b"different")
        with self.assertRaisesRegex(OperationHistoryError, "already exists"):
            self.history.undo(operation_id, self.user_id)
        self.assertTrue(destination.is_file())
        with self.database.connect() as conn:
            operation = conn.execute("SELECT status,undo_error FROM operation_history WHERE id=?", (operation_id,)).fetchone()
        self.assertEqual(operation["status"], "completed")
        self.assertIn("already exists", operation["undo_error"])

    def test_folder_rename_undo_restores_all_catalog_paths(self):
        destination = self.root / "Example Show (2020) {tvdb-1}"
        self.show_folder.rename(destination)
        destination_file = destination / self.file_path.name
        with self.database.connect() as conn:
            conn.execute("UPDATE titles SET folder_path=? WHERE id=?", (str(destination), self.title_id))
            conn.execute("UPDATE files SET path=? WHERE id=?", (str(destination_file), self.file_id))
        operation_id = self.history.record_folder_rename(
            self.title_id, self.show_folder, destination, self.user_id,
        )
        self.history.undo(operation_id, self.user_id)
        self.assertTrue(self.show_folder.is_dir())
        with self.database.connect() as conn:
            title = conn.execute("SELECT folder_path FROM titles WHERE id=?", (self.title_id,)).fetchone()
            file_row = conn.execute("SELECT path FROM files WHERE id=?", (self.file_id,)).fetchone()
        self.assertEqual(title["folder_path"], str(self.show_folder))
        self.assertEqual(file_row["path"], str(self.file_path))

    def test_managed_trash_move_can_be_undone_through_existing_restore_guards(self):
        trash = DuplicateTrashService(self.database)
        trash_id = trash.move(self.file_id, 30, self.user_id)
        operation_id = self.history.record_trash_move(trash_id, self.user_id)
        self.assertFalse(self.file_path.exists())
        self.history.undo(operation_id, self.user_id, duplicate_trash=trash)
        self.assertTrue(self.file_path.is_file())
        with self.database.connect() as conn:
            status = conn.execute("SELECT status FROM operation_history WHERE id=?", (operation_id,)).fetchone()["status"]
            trash_status = conn.execute("SELECT status FROM duplicate_trash WHERE id=?", (trash_id,)).fetchone()["status"]
        self.assertEqual(status, "undone")
        self.assertEqual(trash_status, "restored")


class OperationHistoryUiContractTests(unittest.TestCase):
    def test_activity_links_to_librarian_operation_history_and_undo_explains_revalidation(self):
        root = Path(__file__).resolve().parents[1]
        activity = (root / "app/templates/activity.html").read_text(encoding="utf-8")
        operations = (root / "app/templates/operations.html").read_text(encoding="utf-8")
        self.assertIn('href="/operations"', activity)
        self.assertIn("revalidate", operations.lower())
        self.assertIn('action="/operations/{{ operation.id }}/undo"', operations)
        self.assertIn("nothing will be changed", operations.lower())


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.recovery_package import RecoveryPackageError, RecoveryPackageService
from app.recovery_path_mapping import (
    apply_recovery_root_mappings,
    inspect_recovery_roots,
)


class RecoveryPathMappingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name)
        self.database = Database(self.data / "infomancer.db")
        self.database.initialize()
        with self.database.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES (?,?,?)",
                (r"D:\Movies", "movie", "Movies"),
            )
            self.root_id = int(cursor.lastrowid)
            cursor = conn.execute(
                """INSERT INTO titles(root_id,kind,title,year,folder_path)
                   VALUES (?,?,?,?,?)""",
                (
                    self.root_id,
                    "movie",
                    "Alien",
                    1979,
                    r"d:\movies\Alien (1979)",
                ),
            )
            self.title_id = int(cursor.lastrowid)
            cursor = conn.execute(
                """INSERT INTO files(title_id,path,filename,extension,seen_scan)
                   VALUES (?,?,?,?,?)""",
                (
                    self.title_id,
                    r"D:\Movies\Alien (1979)\Alien.mkv",
                    "Alien.mkv",
                    ".mkv",
                    "test-scan",
                ),
            )
            self.file_id = int(cursor.lastrowid)
            conn.execute(
                """INSERT INTO operation_history(
                     operation_type,status,summary,title_id,file_id,root_id,undo_kind,undo_payload,detail
                   ) VALUES ('rename_file','completed','rename',?,?,?,?,?,?)""",
                (
                    self.title_id,
                    self.file_id,
                    self.root_id,
                    "rename_file",
                    json.dumps({
                        "file_id": self.file_id,
                        "source": r"D:\Movies\Alien (1979)\Alien-old.mkv",
                        "destination": r"D:\Movies\Alien (1979)\Alien.mkv",
                    }),
                    r"D:\Movies\Alien (1979)\Alien-old.mkv → D:\Movies\Alien (1979)\Alien.mkv",
                ),
            )
        self.trusted = self.data / "media"
        self.destination = self.trusted / "Movies"
        self.destination.mkdir(parents=True)

    def tearDown(self):
        self.temporary.cleanup()

    def test_windows_backup_paths_map_to_native_destination_and_undo_history(self):
        result = apply_recovery_root_mappings(
            self.database.path,
            {self.root_id: str(self.destination)},
            (self.trusted,),
        )
        self.assertEqual(result["mapped_roots"], 1)
        self.assertEqual(result["rewritten_paths"], 5)
        with self.database.connect() as conn:
            root = conn.execute(
                "SELECT path FROM roots WHERE id=?", (self.root_id,)
            ).fetchone()[0]
            folder = conn.execute(
                "SELECT folder_path FROM titles WHERE id=?", (self.title_id,)
            ).fetchone()[0]
            media_file = conn.execute(
                "SELECT path FROM files WHERE title_id=?", (self.title_id,)
            ).fetchone()[0]
            history = conn.execute(
                "SELECT undo_payload,detail FROM operation_history WHERE root_id=?",
                (self.root_id,),
            ).fetchone()
        self.assertEqual(root, str(self.destination.resolve()))
        self.assertEqual(folder, str(self.destination.resolve() / "Alien (1979)"))
        self.assertEqual(
            media_file,
            str(self.destination.resolve() / "Alien (1979)" / "Alien.mkv"),
        )
        payload = json.loads(history["undo_payload"])
        self.assertEqual(
            payload["source"],
            str(self.destination.resolve() / "Alien (1979)" / "Alien-old.mkv"),
        )
        self.assertEqual(
            payload["destination"],
            str(self.destination.resolve() / "Alien (1979)" / "Alien.mkv"),
        )
        self.assertIn(str(self.destination.resolve()), history["detail"])

    def test_mapping_destination_must_be_inside_trusted_storage(self):
        outside = self.data / "outside" / "Movies"
        outside.mkdir(parents=True)
        with self.assertRaisesRegex(RecoveryPackageError, "trusted"):
            apply_recovery_root_mappings(
                self.database.path,
                {self.root_id: str(outside)},
                (self.trusted,),
            )

    def test_unknown_root_fails_closed(self):
        with self.assertRaisesRegex(RecoveryPackageError, "unknown media root"):
            apply_recovery_root_mappings(
                self.database.path,
                {99999: str(self.destination)},
                (self.trusted,),
            )

    def test_inventory_suggests_unique_same_name_directory(self):
        service = RecoveryPackageService(self.database.path, "0.9-test")
        package = service.create()
        roots = inspect_recovery_roots(service, package, (self.trusted,))
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0]["path"], r"D:\Movies")
        self.assertEqual(roots[0]["suggested_path"], str(self.destination.resolve()))
        self.assertTrue(roots[0]["suggestion_is_match"])


if __name__ == "__main__":
    unittest.main()

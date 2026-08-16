import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.rename_proposals import RenameProposalError, RenameProposalService


class RenameProposalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "media"
        self.root.mkdir()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            self.root_id = int(conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES (?,?,?)",
                (str(self.root), "movie", "Movies"),
            ).lastrowid)
            self.title_id = int(conn.execute(
                """INSERT INTO titles(root_id,kind,title,metadata_title,metadata_year,tmdb_id,folder_path)
                   VALUES (?,?,?,?,?,?,?)""",
                (self.root_id, "movie", "Example", "Example", 2020, "123", str(self.root)),
            ).lastrowid)
            self.source = self.root / "example-old.mkv"
            self.source.write_bytes(b"movie")
            self.file_id = int(conn.execute(
                """INSERT INTO files(title_id,path,filename,extension,seen_scan)
                   VALUES (?,?,?,?,?)""",
                (self.title_id, str(self.source), self.source.name, ".mkv", "scan"),
            ).lastrowid)
        self.service = RenameProposalService(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def test_refresh_persists_snapshot_without_review_time_filesystem_work(self):
        result = self.service.refresh_all()
        self.assertEqual(result["active"], 1)
        rows = self.service.list_for_review("active")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["source_name"], "example-old.mkv")
        self.assertEqual(row["destination_name"], "Example (2020) {tmdb-123}.mkv")
        self.assertGreater(row["source_mtime_ns"], 0)

    def test_apply_fails_closed_if_source_changes_after_snapshot(self):
        self.service.refresh_all()
        proposal = self.service.list_for_review("active")[0]
        self.source.write_bytes(b"changed movie contents")
        with self.assertRaisesRegex(RenameProposalError, "changed after"):
            self.service.apply(proposal["id"])
        self.assertTrue(self.source.is_file())
        stale = self.service.get(proposal["id"])
        self.assertEqual(stale["status"], "stale")

    def test_apply_renames_and_updates_catalog_without_overwrite(self):
        self.service.refresh_all()
        proposal = self.service.list_for_review("active")[0]
        applied = self.service.apply(proposal["id"])
        destination = Path(applied["destination_path"])
        self.assertTrue(destination.is_file())
        self.assertFalse(self.source.exists())
        with self.database.connect() as conn:
            file_row = conn.execute("SELECT path,filename FROM files WHERE id=?", (self.file_id,)).fetchone()
        self.assertEqual(file_row["path"], str(destination))
        self.assertEqual(file_row["filename"], destination.name)
        self.assertEqual(self.service.get(proposal["id"])["status"], "applied")

    def test_dismissed_snapshot_stays_dismissed_until_file_or_destination_changes(self):
        self.service.refresh_all()
        proposal = self.service.list_for_review("active")[0]
        self.assertTrue(self.service.dismiss(proposal["id"]))
        self.service.refresh_all()
        self.assertEqual(self.service.get(proposal["id"])["status"], "dismissed")
        self.source.write_bytes(b"changed")
        self.service.refresh_all()
        self.assertEqual(self.service.get(proposal["id"])["status"], "active")


class RenameReviewContractTests(unittest.TestCase):
    def test_review_uses_persisted_rename_bucket_and_background_refresh(self):
        root = Path(__file__).resolve().parents[1]
        queue = (root / "app/review_queue.py").read_text(encoding="utf-8")
        routes = (root / "app/routes/review.py").read_text(encoding="utf-8")
        drawer = (root / "app/templates/_review_drawer.html").read_text(encoding="utf-8")
        self.assertIn('"renames": "Renames"', queue)
        self.assertIn("rename_proposals.list_for_review", queue)
        self.assertIn('target=run_rename_refresh', routes)
        self.assertIn('file_protection.require_media_write("apply media rename proposals")', routes)
        self.assertIn("Apply rename", drawer)


if __name__ == "__main__":
    unittest.main()

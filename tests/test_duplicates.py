from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.duplicates import DuplicateService
from app.duplicate_trash import DuplicateTrashError, DuplicateTrashService


class DuplicateServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.database = Database(self.base / "catalog.db")
        self.database.initialize()
        self.service = DuplicateService(self.database)
        self.trash = DuplicateTrashService(self.database)
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO roots(id,path,kind,label) VALUES (1,?,'movie','Movies')",
                (str(self.base),),
            )
            conn.execute(
                """INSERT INTO titles(id,root_id,kind,title,folder_path,tmdb_id)
                   VALUES (1,1,'movie','Example Movie',?, '100')""",
                (str(self.base / "Example Movie"),),
            )
            conn.executemany(
                """INSERT INTO files(
                     id,title_id,path,filename,extension,size_bytes,modified_at,
                     runtime_seconds,width,height,video_codec,bitrate,dynamic_range,
                     media_info_at,seen_scan
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (1, 1, str(self.base / "first.mkv"), "first.mkv", ".mkv", 5,
                     1.0, 7200, 1920, 1080, "h264", 4_000_000, "SDR", "now", "one"),
                    (2, 1, str(self.base / "second.mkv"), "second.mkv", ".mkv", 5,
                     1.0, 7201, 3840, 2160, "hevc", 12_000_000, "HDR10", "now", "one"),
                ],
            )

    def tearDown(self):
        self.temporary.cleanup()

    def test_movie_files_are_compared_and_stronger_copy_is_recommended(self):
        candidates = self.service.candidates()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["classification"], "likely")
        self.assertEqual(candidates[0]["preferred_id"], 2)
        self.assertIn("stronger technical profile", candidates[0]["recommendation"])
        self.assertEqual(candidates[0]["recommended_keep"], "second.mkv")
        self.assertFalse(candidates[0]["safe_to_remove"])
        self.assertTrue(any(
            "Higher stored resolution" in reason
            for reason in candidates[0]["recovery_reasons"]
        ))
        opportunity = self.service.recovery_opportunity(candidates)
        self.assertEqual(opportunity["files"], 1)
        self.assertEqual(opportunity["bytes"], 5)
        self.assertEqual(opportunity["likely_files"], 1)

    def test_ignore_returns_only_after_a_file_changes(self):
        self.assertTrue(self.service.decide(1, 2, "ignored", None))
        self.assertEqual(self.service.candidates(), [])
        self.assertEqual(len(self.service.candidates(status="ignored")), 1)
        with self.database.connect() as conn:
            conn.execute("UPDATE files SET modified_at=2.0 WHERE id=2")
        self.assertEqual(len(self.service.candidates()), 1)

    def test_not_duplicate_is_persistent_and_restorable(self):
        self.assertTrue(self.service.decide(1, 2, "not_duplicate", None))
        with self.database.connect() as conn:
            conn.execute("UPDATE files SET modified_at=2.0 WHERE id=2")
        self.assertEqual(self.service.candidates(), [])
        self.assertEqual(len(self.service.candidates(status="not_duplicate")), 1)
        self.assertTrue(self.service.decide(1, 2, "active", None))
        self.assertEqual(len(self.service.candidates()), 1)

    def test_explicit_hash_verification_distinguishes_exact_files(self):
        (self.base / "first.mkv").write_bytes(b"same")
        (self.base / "second.mkv").write_bytes(b"same")
        self.assertEqual(self.service.verify(1, 2, None), "exact")
        self.assertEqual(self.service.candidates()[0]["classification"], "verified_exact")

    def test_tv_candidates_only_compare_overlapping_episode_coordinates(self):
        with self.database.connect() as conn:
            conn.execute("UPDATE titles SET kind='tv' WHERE id=1")
            conn.execute("UPDATE files SET season=1,episode_start=1,episode_end=1 WHERE id=1")
            conn.execute("UPDATE files SET season=1,episode_start=2,episode_end=2 WHERE id=2")
        self.assertEqual(self.service.candidates(), [])
        with self.database.connect() as conn:
            conn.execute("UPDATE files SET episode_start=1,episode_end=2 WHERE id=2")
        self.assertEqual(len(self.service.candidates()), 1)

    def test_selected_duplicate_can_be_trashed_and_restored(self):
        original = self.base / "first.mkv"
        original.write_bytes(b"first")
        trash_id = self.trash.move(1, 30, None)
        self.assertFalse(original.exists())
        self.assertEqual(len(self.trash.items()), 1)
        self.assertEqual(self.trash.impact()["pending_bytes"], 5)
        self.assertEqual(self.trash.impact()["reclaimed_bytes"], 0)
        pending = self.trash.history("pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["action_label"], "Moved to managed Trash")
        self.assertEqual(pending[0]["size_bytes"], 5)
        with self.database.connect() as conn:
            self.assertIsNone(conn.execute("SELECT id FROM files WHERE id=1").fetchone())
        restored = self.trash.restore(trash_id)
        self.assertEqual(restored, str(original))
        self.assertTrue(original.exists())
        with self.database.connect() as conn:
            row = conn.execute("SELECT path FROM files WHERE title_id=1 AND filename='first.mkv'").fetchone()
        self.assertEqual(row["path"], str(original))
        self.assertEqual(self.trash.impact()["handled_bytes"], 0)
        restored_history = self.trash.history("restored")
        self.assertEqual(len(restored_history), 1)
        self.assertEqual(restored_history[0]["status"], "restored")

    def test_manual_removal_is_verified_before_catalog_update(self):
        original = self.base / "first.mkv"
        original.write_bytes(b"still here")
        with self.assertRaises(DuplicateTrashError):
            self.trash.verify_manually_removed(1)
        original.unlink()
        self.assertEqual(self.trash.verify_manually_removed(1), str(original))
        with self.database.connect() as conn:
            self.assertIsNone(conn.execute("SELECT id FROM files WHERE id=1").fetchone())
        self.assertEqual(self.trash.impact()["reclaimed_bytes"], 5)
        self.assertEqual(self.trash.impact()["reclaimed_files"], 1)
        manual_history = self.trash.history("manual")
        self.assertEqual(len(manual_history), 1)
        self.assertEqual(manual_history[0]["status"], "manual_deleted")

    def test_purged_trash_counts_as_reclaimed_storage(self):
        original = self.base / "first.mkv"
        original.write_bytes(b"first")
        self.trash.move(1, 0, None)
        with self.database.connect() as conn:
            conn.execute("UPDATE duplicate_trash SET purge_after='2000-01-01T00:00:00+00:00'")
        self.assertEqual(self.trash.purge_expired(), 1)
        impact = self.trash.impact()
        self.assertEqual(impact["pending_bytes"], 0)
        self.assertEqual(impact["reclaimed_bytes"], 5)
        purged_history = self.trash.history("purged")
        self.assertEqual(len(purged_history), 1)
        self.assertEqual(purged_history[0]["status"], "purged")


if __name__ == "__main__":
    unittest.main()

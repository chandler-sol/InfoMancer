from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.duplicates import DuplicateService


class DuplicateServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.database = Database(self.base / "catalog.db")
        self.database.initialize()
        self.service = DuplicateService(self.database)
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


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from scripts.benchmark_library import benchmark_once


class BenchmarkHarnessTests(unittest.TestCase):
    def test_small_synthetic_library_exercises_core_measurements(self):
        result = benchmark_once(12, hash_limit=0)
        self.assertEqual(result["requested_files"], 12)
        self.assertEqual(result["catalog_files"], 12)
        self.assertGreater(result["catalog_titles"], 0)
        for key in (
            "initial_scan_seconds",
            "incremental_scan_seconds",
            "library_page_seconds",
            "search_seconds",
            "review_query_seconds",
            "database_backup_seconds",
            "portable_recovery_seconds",
        ):
            self.assertIn(key, result)
            self.assertGreaterEqual(float(result[key]), 0.0)

    def test_library_read_path_indexes_are_installed(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "indexes.db")
            database.initialize()
            with database.connect() as conn:
                indexes = {
                    row["name"]
                    for table in (
                        "titles", "user_title_state", "app_settings", "announcements",
                        "title_tags", "expected_episodes", "files",
                    )
                    for row in conn.execute(f"PRAGMA index_list({table})").fetchall()
                }
        self.assertTrue({
            "idx_titles_updated",
            "idx_user_title_state_updated",
            "idx_app_settings_updated",
            "idx_announcements_updated",
            "idx_title_tags_tag",
            "idx_expected_aired_lookup",
            "idx_files_episode_range",
        }.issubset(indexes))


if __name__ == "__main__":
    unittest.main()

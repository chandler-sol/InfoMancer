import unittest

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


if __name__ == "__main__":
    unittest.main()

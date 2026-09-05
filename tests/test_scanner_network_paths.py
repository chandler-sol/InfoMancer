from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import scanner


class ScannerNetworkPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_winerror_1272_resolution_failure_does_not_degrade_readable_mapping(self):
        movie = self.root / "A" / "Arrival (2016).mkv"
        movie.parent.mkdir()
        movie.write_bytes(b"test")

        blocked = OSError(
            1272,
            "You can't access this shared folder because your organization's "
            "security policies block unauthenticated guest access",
        )
        blocked.winerror = 1272
        errors: list[str] = []

        with mock.patch.object(Path, "resolve", side_effect=blocked):
            files = list(scanner._walk_files(self.root, errors))

        self.assertEqual(files, [movie])
        self.assertEqual(errors, [])

    def test_non_1272_resolution_error_is_still_a_read_error(self):
        movie = self.root / "Movie.mkv"
        movie.write_bytes(b"test")
        blocked = OSError(13, "Permission denied")
        errors: list[str] = []

        with mock.patch.object(Path, "resolve", side_effect=blocked):
            files = list(scanner._walk_files(self.root, errors))

        self.assertEqual(files, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("Permission denied", errors[0])


if __name__ == "__main__":
    unittest.main()

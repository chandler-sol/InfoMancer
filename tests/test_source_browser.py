from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.source_browser import SourceBrowserError, list_folders, preview_folder


class SourceBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.allowed = (self.root,)

    def tearDown(self):
        self.temp.cleanup()

    def test_root_chooser_keeps_unavailable_configured_locations_visible(self):
        unavailable = self.root / "offline-network-share"
        result = list_folders("", (self.root, unavailable))
        locations = {row["name"]: row for row in result["locations"]}
        self.assertTrue(locations[self.root.name]["accessible"])
        self.assertIn("offline-network-share", locations)
        self.assertFalse(locations["offline-network-share"]["accessible"])

    def test_unavailable_configured_location_cannot_be_browsed(self):
        unavailable = self.root / "offline-network-share"
        with self.assertRaises(SourceBrowserError):
            list_folders(str(unavailable), (unavailable,))

    def test_lists_only_visible_child_folders(self):
        (self.root / "Movies").mkdir()
        (self.root / "TV").mkdir()
        (self.root / ".Trash-1000").mkdir()
        result = list_folders(str(self.root), self.allowed)
        self.assertEqual([row["name"] for row in result["folders"]], ["Movies", "TV"])

    def test_rejects_paths_outside_configured_locations(self):
        with self.assertRaises(SourceBrowserError):
            list_folders(str(self.root.parent), self.allowed)

    def test_rejects_symlink_that_escapes_configured_location(self):
        outside = Path(self.temp.name).parent
        link = self.root / "outside-link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlinks are unavailable on this platform")
        with self.assertRaises(SourceBrowserError):
            list_folders(str(link), self.allowed)

    def test_movie_preview_understands_alphabet_and_number_buckets(self):
        for bucket in ("A", "# 0-9"):
            (self.root / bucket).mkdir()
        (self.root / "A" / "Alien (1979).mkv").write_bytes(b"a")
        (self.root / "A" / "Arrival (2016).mp4").write_bytes(b"a")
        (self.root / "# 0-9" / "1917 (2019).mkv").write_bytes(b"a")
        result = preview_folder(str(self.root), self.allowed)
        self.assertEqual(result["recommended_kind"], "movie")
        self.assertEqual(result["movie_count"], 3)
        self.assertEqual(result["bucket_count"], 2)

    def test_tv_preview_counts_series_and_recognized_episodes(self):
        for show in ("1883 (2021)", "1923 (2022)"):
            season = self.root / show / "Season 01"
            season.mkdir(parents=True)
            (season / f"{show} - S01E01.mkv").write_bytes(b"a")
            (season / f"{show} - S01E02.mkv").write_bytes(b"a")
        result = preview_folder(str(self.root), self.allowed)
        self.assertEqual(result["recommended_kind"], "tv")
        self.assertEqual(result["show_count"], 2)
        self.assertEqual(result["episode_count"], 4)

    def test_mixed_movie_buckets_and_tv_episodes_require_override(self):
        (self.root / "A").mkdir()
        (self.root / "A" / "Alien (1979).mkv").write_bytes(b"a")
        season = self.root / "Example Show" / "Season 01"
        season.mkdir(parents=True)
        (season / "Example Show - S01E01.mkv").write_bytes(b"a")
        result = preview_folder(str(self.root), self.allowed)
        self.assertEqual(result["recommended_kind"], "mixed")
        self.assertTrue(result["warning"])


if __name__ == "__main__":
    unittest.main()

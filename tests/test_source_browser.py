from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import source_browser
from app.source_browser import SourceBrowserError, list_folders, preview_folder


class SourceBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.allowed = (self.root,)

    def tearDown(self):
        self.temp.cleanup()

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

    def test_inaccessible_configured_root_does_not_break_location_listing(self):
        blocked = (self.root.parent / "Blocked network drive").resolve()
        real_accessible = source_browser._root_is_accessible

        def accessible(path: Path) -> bool:
            if path == blocked:
                return False
            return real_accessible(path)

        with mock.patch.object(source_browser, "_root_is_accessible", side_effect=accessible):
            result = list_folders("", (self.root, blocked))

        self.assertEqual(len(result["locations"]), 1)
        self.assertEqual(result["locations"][0]["path"], str(self.root))

    def test_windows_style_resolution_error_becomes_source_browser_error(self):
        with mock.patch.object(
            Path,
            "resolve",
            side_effect=OSError(1272, "Guest access is blocked"),
        ):
            with self.assertRaises(SourceBrowserError) as caught:
                source_browser._resolved(Path("B:/"))
        self.assertIn("cannot access", str(caught.exception))

    def test_unresolvable_child_folder_is_skipped(self):
        (self.root / "Movies").mkdir()
        (self.root / "Blocked").mkdir()
        real_resolved = source_browser._resolved

        def resolve_path(value):
            path = Path(value)
            if path.name == "Blocked":
                raise SourceBrowserError("blocked")
            return real_resolved(value)

        with mock.patch.object(source_browser, "_resolved", side_effect=resolve_path):
            result = list_folders(str(self.root), self.allowed)

        self.assertEqual([row["name"] for row in result["folders"]], ["Movies"])

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

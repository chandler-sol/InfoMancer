from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import SCHEMA
from app.naming import plex_episode_filename, plex_movie_filename, plex_show_folder
from app.scanner import (
    movie_release_title, parse_episode, parse_title, scan_root, scan_title, title_and_year,
)


class NamingTests(unittest.TestCase):
    def test_episode_parser_handles_range(self):
        parsed = parse_episode("Some.Show.S02E03-E04.1080p.WEB-DL.x265.mkv")
        self.assertEqual((parsed.season, parsed.start, parsed.end), (2, 3, 4))
        self.assertEqual(parsed.parsed_title, "Some Show")

    def test_title_parser_removes_year_and_provider_tag(self):
        self.assertEqual(title_and_year("The Office (2005) {tvdb-73244}"), ("The Office", 2005))

    def test_title_parser_understands_lifecycle_ranges_and_numeric_titles(self):
        ended = parse_title("1923 (2022 -2025)")
        self.assertEqual((ended.title, ended.year, ended.end_year, ended.continuing),
                         ("1923", 2022, 2025, False))
        current = parse_title("30 for 30 (2009 - Present)")
        self.assertEqual((current.title, current.year, current.end_year, current.continuing),
                         ("30 for 30", 2009, None, True))

    def test_noisy_movie_release_is_trimmed_after_year(self):
        cleaned = movie_release_title("Avengers.Endgame.2019.x264.ads;jflaksjd;flkajdsf")
        self.assertEqual(cleaned, "Avengers Endgame (2019)")

    def test_plex_names(self):
        self.assertEqual(
            plex_show_folder("The Office", 2005, 73244),
            "The Office (2005) {tvdb-73244}",
        )
        self.assertEqual(
            plex_show_folder("1923", 2022, 416491, end_year=2025, continuing=False),
            "1923 (2022 - 2025) {tvdb-416491}",
        )
        self.assertEqual(
            plex_episode_filename("The Office", 2005, 1, 2, "Diversity Day", ".MKV"),
            "The Office - S01E02 - Diversity Day.mkv",
        )
        self.assertEqual(
            plex_episode_filename("Example", 2020, 1, 1, "Premiere", ".mkv", episode_end=2),
            "Example - S01E01-E02 - Premiere.mkv",
        )
        self.assertEqual(
            plex_movie_filename("Avengers Endgame", 2019, ".MKV", tmdb_id="299534"),
            "Avengers Endgame (2019) {tmdb-299534}.mkv",
        )


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def add_root(self, kind: str) -> sqlite3.Row:
        self.conn.execute("INSERT INTO roots(path,kind,label) VALUES (?,?,?)", (str(self.root), kind, "Test"))
        return self.conn.execute("SELECT * FROM roots").fetchone()

    def test_standalone_movies_are_separate_titles(self):
        (self.root / "Arrival (2016).mkv").write_bytes(b"a")
        (self.root / "Alien (1979).mp4").write_bytes(b"b")
        result = scan_root(self.conn, self.add_root("movie"))
        self.assertEqual(result["titles"], 2)
        titles = self.conn.execute("SELECT title,year FROM titles ORDER BY title").fetchall()
        self.assertEqual([(x["title"], x["year"]) for x in titles], [("Alien", 1979), ("Arrival", 2016)])

    def test_movies_inside_alphabet_bucket_are_separate_titles(self):
        bucket = self.root / "A"
        bucket.mkdir()
        (bucket / "Alien (1979).mkv").write_bytes(b"a")
        (bucket / "Arrival (2016).mp4").write_bytes(b"b")
        result = scan_root(self.conn, self.add_root("movie"))
        self.assertEqual(result["titles"], 2)
        titles = self.conn.execute("SELECT title,year FROM titles ORDER BY title").fetchall()
        self.assertEqual([(x["title"], x["year"]) for x in titles], [("Alien", 1979), ("Arrival", 2016)])

    def test_movies_inside_numbered_bucket_are_separate_titles(self):
        bucket = self.root / "# 0-9"
        bucket.mkdir()
        (bucket / "1917 (2019).mkv").write_bytes(b"a")
        (bucket / "8 Mile (2002).mp4").write_bytes(b"b")
        result = scan_root(self.conn, self.add_root("movie"))
        self.assertEqual(result["titles"], 2)
        titles = self.conn.execute(
            "SELECT title,year FROM titles ORDER BY title"
        ).fetchall()
        self.assertEqual(
            [(row["title"], row["year"]) for row in titles],
            [("1917", 2019), ("8 Mile", 2002)],
        )

    def test_rescan_removes_missing_file_from_catalog_only(self):
        show = self.root / "Example Show (2020)" / "Season 01"
        show.mkdir(parents=True)
        media = show / "Example.Show.S01E01.1080p.mkv"
        media.write_bytes(b"video")
        root_row = self.add_root("tv")
        scan_root(self.conn, root_row)
        media.unlink()
        scan_root(self.conn, root_row)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM titles").fetchone()[0], 0)

    def test_scan_reports_video_progress(self):
        show = self.root / "Example Show" / "Season 01"
        show.mkdir(parents=True)
        (show / "Example.Show.S01E01.mkv").write_bytes(b"video")
        updates = []
        scan_root(self.conn, self.add_root("tv"), lambda files, titles: updates.append((files, titles)))
        self.assertTrue(updates)
        self.assertEqual(updates[-1], (1, 1))

    def test_only_newly_inserted_titles_receive_discovery_timestamp(self):
        media = self.root / "Arrival (2016).mkv"
        media.write_bytes(b"video")
        root_row = self.add_root("movie")
        scan_root(self.conn, root_row)
        discovered = self.conn.execute(
            "SELECT discovered_at FROM titles"
        ).fetchone()["discovered_at"]
        self.assertIsNotNone(discovered)

        self.conn.execute("UPDATE titles SET discovered_at=NULL")
        scan_root(self.conn, root_row)
        discovered_after_rescan = self.conn.execute(
            "SELECT discovered_at FROM titles"
        ).fetchone()["discovered_at"]
        self.assertIsNone(discovered_after_rescan)

    def test_series_rescan_finds_new_episode_and_preserves_original_names(self):
        show = self.root / "Example Show (2020)" / "Season 01"
        show.mkdir(parents=True)
        first = show / "Example.Show.S01E01.1080p.mkv"
        first.write_bytes(b"one")
        root_row = self.add_root("tv")
        scan_root(self.conn, root_row)
        title = self.conn.execute("SELECT * FROM titles").fetchone()

        second = show / "Example.Show.S01E02.1080p.mkv"
        second.write_bytes(b"two")
        result = scan_title(self.conn, title)

        self.assertEqual(result["files"], 2)
        rows = self.conn.execute(
            "SELECT filename, original_filename FROM files ORDER BY filename"
        ).fetchall()
        self.assertEqual(
            [(row["filename"], row["original_filename"]) for row in rows],
            [
                ("Example.Show.S01E01.1080p.mkv", "Example.Show.S01E01.1080p.mkv"),
                ("Example.Show.S01E02.1080p.mkv", "Example.Show.S01E02.1080p.mkv"),
            ],
        )
        self.assertIsNotNone(
            self.conn.execute("SELECT last_scanned_at FROM titles").fetchone()["last_scanned_at"]
        )


if __name__ == "__main__":
    unittest.main()

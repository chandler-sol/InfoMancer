from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.naming import contained_destination, safe_component
from app.scanner import SourceUnavailableError, scan_root
from app.season_folders import SeasonFolderError, SeasonFolderService


class FilesystemTortureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.media = self.base / "media"
        self.show = self.media / "Torture Show (2020)"
        self.show.mkdir(parents=True)
        self.database = Database(self.base / "infomancer.db")
        self.database.initialize()
        with self.database.connect() as conn:
            self.root_id = conn.execute(
                "INSERT INTO roots(path,kind,label,health_status) VALUES (?,?,?,?)",
                (str(self.media), "tv", "TV", "healthy"),
            ).lastrowid
            self.title_id = conn.execute(
                """INSERT INTO titles(root_id,kind,title,folder_path,metadata_title)
                   VALUES (?,?,?,?,?)""",
                (self.root_id, "tv", "Torture Show", str(self.show), "Torture Show"),
            ).lastrowid
        self.service = SeasonFolderService(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def add_episode(self, episode: int, season: int = 1) -> tuple[int, Path]:
        path = self.show / f"Torture Show - S{season:02d}E{episode:02d}.mkv"
        path.write_bytes(b"test")
        with self.database.connect() as conn:
            file_id = conn.execute(
                """INSERT INTO files(
                     title_id,path,filename,extension,size_bytes,season,episode_start,
                     episode_end,seen_scan
                   ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    self.title_id, str(path), path.name, ".mkv", path.stat().st_size,
                    season, episode, episode, "test",
                ),
            ).lastrowid
        return int(file_id), path

    def test_destination_collision_is_blocked_in_preview_and_source_survives(self):
        file_id, source = self.add_episode(1)
        destination = self.show / "Season 01" / source.name
        destination.parent.mkdir()
        destination.write_bytes(b"occupied")
        preview = self.service.preview(self.title_id)
        proposal = next(item for item in preview["proposals"] if item["file_id"] == file_id)
        self.assertEqual(proposal["status"], "blocked")
        self.assertIn("already exists", proposal["reason"])
        self.assertTrue(source.is_file())
        self.assertEqual(destination.read_bytes(), b"occupied")

    def test_source_disappearing_after_preview_fails_closed(self):
        file_id, source = self.add_episode(2)
        self.assertEqual(len(self.service.preview(self.title_id)["ready"]), 1)
        source.unlink()
        with self.assertRaisesRegex(SeasonFolderError, "preview changed"):
            self.service.apply(self.title_id, [file_id])
        with self.database.connect() as conn:
            row = conn.execute("SELECT path FROM files WHERE id=?", (file_id,)).fetchone()
        self.assertEqual(row["path"], str(source))

    def test_mid_batch_failure_rolls_first_move_back(self):
        first_id, first = self.add_episode(3)
        second_id, second = self.add_episode(4)
        original_current = self.service._current_proposal

        def fail_second(title_id: int, file_id: int):
            if file_id == second_id:
                raise SeasonFolderError("simulated permission loss")
            return original_current(title_id, file_id)

        self.service._current_proposal = fail_second
        with self.assertRaisesRegex(SeasonFolderError, "simulated permission loss"):
            self.service.apply(self.title_id, [first_id, second_id])
        self.assertTrue(first.is_file())
        self.assertTrue(second.is_file())
        self.assertFalse((self.show / "Season 01" / first.name).exists())
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT id,path FROM files WHERE id IN (?,?) ORDER BY id",
                (first_id, second_id),
            ).fetchall()
        self.assertEqual({row["path"] for row in rows}, {str(first), str(second)})

    def test_symlinked_media_file_outside_library_is_blocked(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are unavailable")
        outside = self.base / "outside.mkv"
        outside.write_bytes(b"outside")
        link = self.show / "Torture Show - S01E05.mkv"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")
        with self.database.connect() as conn:
            file_id = conn.execute(
                """INSERT INTO files(title_id,path,filename,extension,season,episode_start,
                   episode_end,seen_scan) VALUES (?,?,?,?,?,?,?,?)""",
                (self.title_id, str(link), link.name, ".mkv", 1, 5, 5, "test"),
            ).lastrowid
        proposal = next(
            item for item in self.service.preview(self.title_id)["proposals"]
            if item["file_id"] == file_id
        )
        self.assertEqual(proposal["status"], "blocked")
        self.assertIn("outside the configured library boundary", proposal["reason"])
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_scanner_does_not_follow_directory_symlink_outside_root(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are unavailable")
        outside = self.base / "outside-show"
        outside.mkdir()
        (outside / "Outside - S01E01.mkv").write_bytes(b"outside")
        link = self.media / "linked-show"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")
        with self.database.connect() as conn:
            root = conn.execute("SELECT * FROM roots WHERE id=?", (self.root_id,)).fetchone()
            scan_root(conn, root)
            count = conn.execute(
                "SELECT COUNT(*) count FROM files WHERE path LIKE ?", (f"{outside}%",)
            ).fetchone()["count"]
        self.assertEqual(count, 0)

    def test_disconnected_root_is_reported_without_catalog_cleanup(self):
        file_id, source = self.add_episode(6)
        renamed = self.base / "media-offline"
        self.media.rename(renamed)
        with self.database.connect() as conn:
            root = conn.execute("SELECT * FROM roots WHERE id=?", (self.root_id,)).fetchone()
            with self.assertRaises(SourceUnavailableError):
                scan_root(conn, root)
            row = conn.execute("SELECT id,path FROM files WHERE id=?", (file_id,)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["path"], str(source))

    def test_windows_reserved_components_are_neutralized_cross_platform(self):
        for value in ("CON", "prn", "AUX.txt", "NUL", "COM1", "LPT9"):
            with self.subTest(value=value):
                rendered = safe_component(value)
                self.assertNotEqual(rendered.split(".", 1)[0].upper(), value.split(".", 1)[0].upper())
                self.assertTrue(rendered.endswith("_"))
        self.assertEqual(safe_component("Title. "), "Title")
        self.assertEqual(safe_component("A\x00B"), "AB")

    def test_contained_destination_rejects_parent_escape(self):
        source = self.show / "episode.mkv"
        source.write_bytes(b"x")
        with self.assertRaises(ValueError):
            contained_destination(source, "../escaped.mkv")


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.operation_history import OperationHistoryService
from app.season_folders import SeasonFolderError, SeasonFolderService


class SeasonFolderServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "tv"
        self.show = self.root / "Example Show"
        self.show.mkdir(parents=True)
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            root_id = int(conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES (?,?,?)",
                (str(self.root), "tv", "TV"),
            ).lastrowid)
            self.title_id = int(conn.execute(
                "INSERT INTO titles(root_id,kind,title,folder_path) VALUES (?,?,?,?)",
                (root_id, "tv", "Example Show", str(self.show)),
            ).lastrowid)
        self.service = SeasonFolderService(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def add_file(self, name: str, season, episode: int = 1, *, folder: Path | None = None) -> int:
        parent = folder or self.show
        parent.mkdir(parents=True, exist_ok=True)
        path = parent / name
        path.write_bytes(b"video")
        with self.database.connect() as conn:
            return int(conn.execute(
                """INSERT INTO files(title_id,path,filename,extension,season,episode_start,seen_scan)
                   VALUES (?,?,?,?,?,?,?)""",
                (self.title_id, str(path), name, ".mkv", season, episode, "scan"),
            ).lastrowid)

    def test_preview_maps_specials_and_numbered_seasons_without_writing(self):
        special = self.add_file("special.mkv", 0)
        regular = self.add_file("episode.mkv", 2)
        unparsed = self.add_file("mystery.mkv", None)
        preview = self.service.preview(self.title_id)
        ready = {item["file_id"]: item for item in preview["ready"]}
        special_destination = Path(ready[special]["destination"])
        regular_destination = Path(ready[regular]["destination"])
        self.assertEqual(special_destination.parent.name, "Specials")
        self.assertEqual(special_destination.name, "special.mkv")
        self.assertEqual(regular_destination.parent.name, "Season 02")
        self.assertEqual(regular_destination.name, "episode.mkv")
        self.assertEqual(preview["skipped_unparsed"], 1)
        self.assertFalse((self.show / "Specials").exists())
        self.assertFalse((self.show / "Season 02").exists())
        self.assertNotIn(unparsed, ready)

    def test_apply_moves_selected_file_updates_catalog_and_can_be_undone(self):
        file_id = self.add_file("episode.mkv", 1)
        source = self.show / "episode.mkv"
        moved = self.service.apply(self.title_id, [file_id])
        destination = self.show / "Season 01" / "episode.mkv"
        self.assertTrue(destination.is_file())
        self.assertFalse(source.exists())
        with self.database.connect() as conn:
            row = conn.execute("SELECT path FROM files WHERE id=?", (file_id,)).fetchone()
        self.assertEqual(row["path"], str(destination))
        history = OperationHistoryService(self.database)
        operation_id = history.record_file_rename(
            file_id, moved[0]["source"], moved[0]["destination"], None,
            label="Episode moved into season folder",
        )
        history.undo(operation_id, None)
        self.assertTrue(source.is_file())
        self.assertFalse(destination.exists())

    def test_multi_file_batch_rolls_back_earlier_moves_when_later_file_changes(self):
        first_id = self.add_file("first.mkv", 1, episode=1)
        second_id = self.add_file("second.mkv", 2, episode=1)
        original_current = self.service._current_proposal
        calls = 0

        def fail_second(title_id: int, file_id: int):
            nonlocal calls
            calls += 1
            if calls == 2:
                return None
            return original_current(title_id, file_id)

        self.service._current_proposal = fail_second
        try:
            with self.assertRaisesRegex(SeasonFolderError, "Nothing from this batch was kept"):
                self.service.apply(self.title_id, [first_id, second_id])
        finally:
            self.service._current_proposal = original_current

        first_source = self.show / "first.mkv"
        second_source = self.show / "second.mkv"
        self.assertTrue(first_source.is_file())
        self.assertTrue(second_source.is_file())
        self.assertFalse((self.show / "Season 01").exists())
        self.assertFalse((self.show / "Season 02").exists())
        with self.database.connect() as conn:
            paths = {
                row["id"]: row["path"]
                for row in conn.execute(
                    "SELECT id,path FROM files WHERE id IN (?,?)",
                    (first_id, second_id),
                )
            }
        self.assertEqual(paths[first_id], str(first_source))
        self.assertEqual(paths[second_id], str(second_source))

    def test_destination_collision_blocks_preview_and_apply(self):
        file_id = self.add_file("episode.mkv", 3)
        season = self.show / "Season 03"
        season.mkdir()
        (season / "episode.mkv").write_bytes(b"other")
        preview = self.service.preview(self.title_id)
        blocked = {item["file_id"]: item for item in preview["blocked"]}
        self.assertIn(file_id, blocked)
        self.assertIn("already exists", blocked[file_id]["reason"])
        with self.assertRaisesRegex(SeasonFolderError, "preview changed"):
            self.service.apply(self.title_id, [file_id])
        self.assertTrue((self.show / "episode.mkv").is_file())

    def test_existing_correct_folder_is_not_proposed_again(self):
        file_id = self.add_file("episode.mkv", 4, folder=self.show / "Season 04")
        preview = self.service.preview(self.title_id)
        organized = {item["file_id"] for item in preview["organized"]}
        self.assertIn(file_id, organized)
        self.assertEqual(preview["ready"], [])


class SeasonFolderUiContractTests(unittest.TestCase):
    def test_full_title_view_links_to_preview_first_workflow(self):
        root = Path(__file__).resolve().parents[1]
        detail = (root / "app/templates/detail.html").read_text(encoding="utf-8")
        preview = (root / "app/templates/season_folders.html").read_text(encoding="utf-8")
        routes = (root / "app/routes/titles.py").read_text(encoding="utf-8")
        self.assertIn("Organize into Season Folders", detail)
        self.assertIn('action="/titles/{{ title.id }}/organize-seasons"', preview)
        self.assertIn("Existing destinations will never be overwritten", preview)
        self.assertIn('file_protection.require_media_write("move episode files into season folders")', routes)
        self.assertIn("operation_history.record_file_rename", routes)


if __name__ == "__main__":
    unittest.main()

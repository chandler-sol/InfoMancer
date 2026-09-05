from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.file_hashes import MediaHashService
from app.path_reconciliation import reconcile_root_paths
from app.scanner import scan_root


REPO_ROOT = Path(__file__).resolve().parents[1]


class PathReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.media = self.base / "media"
        self.media.mkdir()
        self.db = Database(self.base / "catalog.db")
        self.db.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def add_root(self, kind: str) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES (?,?,?)",
                (str(self.media), kind, "Test"),
            )
            return int(cursor.lastrowid)

    def scan(self, root_id: int) -> None:
        with self.db.connect() as conn:
            root = conn.execute("SELECT * FROM roots WHERE id=?", (root_id,)).fetchone()
            scan_root(conn, root)

    def test_standalone_movie_rename_preserves_catalog_identity(self):
        bucket = self.media / "# 0-9"
        bucket.mkdir()
        original = bucket / "12 Years a Slave (2013).mkv"
        original.write_bytes(b"same-media-content")
        root_id = self.add_root("movie")
        self.scan(root_id)

        with self.db.connect() as conn:
            title = conn.execute("SELECT * FROM titles").fetchone()
            file_row = conn.execute("SELECT * FROM files").fetchone()
            title_id = int(title["id"])
            file_id = int(file_row["id"])
            conn.execute(
                "UPDATE titles SET tmdb_id='76203', metadata_title='12 Years a Slave' WHERE id=?",
                (title_id,),
            )
            conn.execute(
                "UPDATE files SET media_info_error=? WHERE id=?",
                (
                    "The media file is no longer available at its cataloged path. Reconnect its storage or rescan the source if the file moved.",
                    file_id,
                ),
            )

        renamed = bucket / "12 Years a Slave (2013) {tmdb-76203}.mkv"
        original.rename(renamed)
        result = reconcile_root_paths(self.db, root_id)
        self.assertEqual(result["reconciled"], 1)

        self.scan(root_id)
        with self.db.connect() as conn:
            titles = conn.execute("SELECT * FROM titles").fetchall()
            files = conn.execute("SELECT * FROM files").fetchall()
        self.assertEqual(len(titles), 1)
        self.assertEqual(len(files), 1)
        self.assertEqual(int(titles[0]["id"]), title_id)
        self.assertEqual(int(files[0]["id"]), file_id)
        self.assertEqual(titles[0]["folder_path"], str(renamed))
        self.assertEqual(files[0]["path"], str(renamed))
        self.assertEqual(titles[0]["tmdb_id"], "76203")
        self.assertIsNone(files[0]["media_info_error"])

    def test_episode_filename_change_preserves_file_row(self):
        season = self.media / "Example Show (2020)" / "Season 01"
        season.mkdir(parents=True)
        original = season / "Example Show - S01E01.mkv"
        original.write_bytes(b"episode-content")
        root_id = self.add_root("tv")
        self.scan(root_id)

        with self.db.connect() as conn:
            file_id = int(conn.execute("SELECT id FROM files").fetchone()["id"])

        renamed = season / "Example Show - S01E01 - Pilot.mkv"
        original.rename(renamed)
        result = reconcile_root_paths(self.db, root_id)
        self.assertEqual(result["reconciled"], 1)
        self.scan(root_id)

        with self.db.connect() as conn:
            files = conn.execute("SELECT id,path FROM files").fetchall()
        self.assertEqual(len(files), 1)
        self.assertEqual(int(files[0]["id"]), file_id)
        self.assertEqual(files[0]["path"], str(renamed))

    def test_ambiguous_movie_candidates_use_historical_hash(self):
        bucket = self.media / "H"
        bucket.mkdir()
        original = bucket / "Hash Choice (2020).mkv"
        original.write_bytes(b"A" * 64)
        root_id = self.add_root("movie")
        self.scan(root_id)

        with self.db.connect() as conn:
            file_id = int(conn.execute("SELECT id FROM files").fetchone()["id"])
        MediaHashService(self.db).hash_file(file_id)

        correct = bucket / "Hash Choice (2020) REMUX.mkv"
        wrong = bucket / "Hash Choice (2020) WEB-DL.mkv"
        original.rename(correct)
        wrong.write_bytes(b"B" * 64)

        result = reconcile_root_paths(self.db, root_id)
        self.assertEqual(result["reconciled"], 1)
        self.assertEqual(result["hash_resolved"], 1)

        with self.db.connect() as conn:
            file_row = conn.execute("SELECT id,path FROM files WHERE id=?", (file_id,)).fetchone()
            hash_row = conn.execute(
                "SELECT status,size_bytes,modified_at FROM media_file_hashes WHERE file_id=?",
                (file_id,),
            ).fetchone()
        self.assertEqual(file_row["path"], str(correct))
        self.assertEqual(hash_row["status"], "complete")
        self.assertEqual(int(hash_row["size_bytes"]), 64)


class FollowupUiContractTests(unittest.TestCase):
    def source(self, relative: str) -> str:
        return (REPO_ROOT / relative).read_text(encoding="utf-8")

    def test_more_filters_click_away_is_loaded(self):
        loader = self.source("app/static/workspace-ui.js")
        dismiss = self.source("app/static/library-filter-dismiss.js")
        self.assertIn("library-filter-dismiss.js", loader)
        self.assertIn("!menu.contains(event.target)", dismiss)
        self.assertIn("event.key !== 'Escape'", dismiss)

    def test_task_clear_keeps_open_widget_visible(self):
        loader = self.source("app/static/workspace-ui.js")
        polish = self.source("app/static/task-widget-open-polish.js")
        self.assertIn("task-widget-open-polish.js", loader)
        self.assertIn("action.textContent.trim() !== 'Clear'", polish)
        self.assertIn("widget.classList.add('visible')", polish)

    def test_media_failure_page_has_navigation_dismiss_and_alignment_polish(self):
        template = self.source("app/templates/media_info_failures.html")
        css = self.source("app/static/media-failures.css")
        route = self.source("app/routes/settings_quick_actions.py")
        self.assertIn('href="/settings/system"', template)
        self.assertIn('/media-info/failures/{{ file.id }}/dismiss', template)
        self.assertIn("media-failure-identity", template)
        self.assertIn("media-failures-heading", css)
        self.assertIn("display: block !important", css)
        self.assertIn('"/media-info/failures/{file_id}/dismiss"', route)

    def test_windows_media_inspection_hides_helper_console(self):
        media_info = self.source("app/media_info.py")
        self.assertIn("CREATE_NO_WINDOW", media_info)
        self.assertIn("STARTF_USESHOWWINDOW", media_info)
        self.assertIn("_quiet_subprocess_options()", media_info)

    def test_reachable_degraded_source_gets_distinct_connection_copy(self):
        polish = self.source("app/routes/final_polish.py")
        self.assertIn("The source root is reachable", polish)
        self.assertIn("complete scan confirms the full catalog", polish)
        self.assertIn("could not reach the configured source from the app process", polish)


if __name__ == "__main__":
    unittest.main()

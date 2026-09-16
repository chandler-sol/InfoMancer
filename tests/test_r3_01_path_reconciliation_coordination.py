from __future__ import annotations

import threading
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import app.catalog_mutation as catalog_mutation
import app.path_reconciliation as path_reconciliation
from app.db import Database
from app.safe_file_rename import SafeFileRenameService
from app.scanner import scan_root


class R301PathReconciliationCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.media = self.base / "media"
        self.media.mkdir()
        self.db = Database(self.base / "catalog.db")
        self.db.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def add_tv_root(self) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES (?,?,?)",
                (str(self.media), "tv", "TV"),
            )
            return int(cursor.lastrowid)

    def scan(self, root_id: int) -> None:
        with self.db.connect() as conn:
            root = conn.execute("SELECT * FROM roots WHERE id=?", (root_id,)).fetchone()
            scan_root(conn, root)

    def make_episode(self):
        show = self.media / "Example Show (2020)"
        season = show / "Season 01"
        season.mkdir(parents=True)
        episode = season / "Example Show - S01E01.mkv"
        episode.write_bytes(b"episode-content-for-r3-01")
        root_id = self.add_tv_root()
        self.scan(root_id)
        with self.db.connect() as conn:
            title = conn.execute("SELECT id,folder_path FROM titles").fetchone()
            file_row = conn.execute("SELECT id,path FROM files").fetchone()
        return root_id, show, season, episode, int(title["id"]), int(file_row["id"])

    @staticmethod
    def paused_walk(candidate: Path, observed: threading.Event, release: threading.Event):
        def walk(_root: Path, _errors: list[str]):
            yield candidate
            observed.set()
            if not release.wait(5):
                raise RuntimeError("timed out waiting to resume reconciliation")
        return walk

    def test_folder_rename_waits_for_reconciliation_and_file_identity_survives(self):
        root_id, show, season, episode, title_id, file_id = self.make_episode()
        renamed_episode = season / "Example Show - S01E01 - Pilot.mkv"
        episode.rename(renamed_episode)
        renamed_show = self.media / "Example Show (2020) {tvdb-123}"

        candidate_observed = threading.Event()
        release_reconciliation = threading.Event()
        rename_attempting_root_lock = threading.Event()
        rename_done = threading.Event()
        reconcile_result: dict[str, object] = {}
        reconcile_error: list[BaseException] = []
        rename_error: list[BaseException] = []

        real_root_lock = catalog_mutation.root_mutation_lock

        @contextmanager
        def observed_root_lock(database_key: str, requested_root_id: int):
            rename_attempting_root_lock.set()
            with real_root_lock(database_key, requested_root_id):
                yield

        def run_reconciliation():
            try:
                reconcile_result.update(path_reconciliation.reconcile_root_paths(self.db, root_id))
            except BaseException as exc:
                reconcile_error.append(exc)

        def run_folder_rename():
            try:
                SafeFileRenameService(self.db).rename_folder(title_id, show, renamed_show)
            except BaseException as exc:
                rename_error.append(exc)
            finally:
                rename_done.set()

        with patch.object(
            path_reconciliation,
            "_walk_files",
            self.paused_walk(renamed_episode, candidate_observed, release_reconciliation),
        ), patch("app.safe_file_rename.root_mutation_lock", observed_root_lock):
            reconcile_thread = threading.Thread(target=run_reconciliation)
            reconcile_thread.start()
            self.assertTrue(candidate_observed.wait(5))

            rename_thread = threading.Thread(target=run_folder_rename)
            rename_thread.start()
            self.assertTrue(rename_attempting_root_lock.wait(5))
            self.assertFalse(
                rename_done.wait(0.25),
                "folder rename acquired the root mutation lease while reconciliation still held it",
            )

            release_reconciliation.set()
            reconcile_thread.join(5)
            rename_thread.join(5)

        self.assertFalse(reconcile_thread.is_alive())
        self.assertFalse(rename_thread.is_alive())
        self.assertEqual(reconcile_error, [])
        self.assertEqual(rename_error, [])
        self.assertEqual(reconcile_result.get("reconciled"), 1)

        final_episode = renamed_show / "Season 01" / renamed_episode.name
        self.assertTrue(final_episode.is_file())
        with self.db.connect() as conn:
            title = conn.execute("SELECT id,folder_path FROM titles WHERE id=?", (title_id,)).fetchone()
            file_row = conn.execute("SELECT id,path FROM files WHERE id=?", (file_id,)).fetchone()
        self.assertIsNotNone(title)
        self.assertIsNotNone(file_row)
        self.assertEqual(int(title["id"]), title_id)
        self.assertEqual(int(file_row["id"]), file_id)
        self.assertEqual(title["folder_path"], str(renamed_show))
        self.assertEqual(file_row["path"], str(final_episode))

        self.scan(root_id)
        with self.db.connect() as conn:
            file_rows = conn.execute("SELECT id,path FROM files ORDER BY id").fetchall()
        self.assertEqual(len(file_rows), 1)
        self.assertEqual(int(file_rows[0]["id"]), file_id)
        self.assertEqual(file_rows[0]["path"], str(final_episode))

    def test_direct_catalog_file_path_change_is_not_overwritten_by_stale_reconciliation(self):
        root_id, _show, season, episode, _title_id, file_id = self.make_episode()
        renamed_episode = season / "Example Show - S01E01 - Pilot.mkv"
        episode.rename(renamed_episode)

        candidate_observed = threading.Event()
        release_reconciliation = threading.Event()
        result: dict[str, object] = {}
        errors: list[BaseException] = []

        def run_reconciliation():
            try:
                result.update(path_reconciliation.reconcile_root_paths(self.db, root_id))
            except BaseException as exc:
                errors.append(exc)

        with patch.object(
            path_reconciliation,
            "_walk_files",
            self.paused_walk(renamed_episode, candidate_observed, release_reconciliation),
        ):
            worker = threading.Thread(target=run_reconciliation)
            worker.start()
            self.assertTrue(candidate_observed.wait(5))

            raw_catalog_path = season / "owned-by-another-writer.mkv"
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE files SET path=?,filename=? WHERE id=?",
                    (str(raw_catalog_path), raw_catalog_path.name, file_id),
                )

            release_reconciliation.set()
            worker.join(5)

        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result.get("reconciled"), 0)
        with self.db.connect() as conn:
            file_row = conn.execute("SELECT id,path FROM files WHERE id=?", (file_id,)).fetchone()
        self.assertEqual(int(file_row["id"]), file_id)
        self.assertEqual(file_row["path"], str(raw_catalog_path))

    def test_direct_catalog_title_path_change_is_not_overwritten_by_stale_reconciliation(self):
        root_id, show, _season, episode, title_id, file_id = self.make_episode()
        renamed_show = self.media / "Example Show (2020) {tvdb-123}"
        show.rename(renamed_show)
        candidate = renamed_show / "Season 01" / episode.name

        candidate_observed = threading.Event()
        release_reconciliation = threading.Event()
        result: dict[str, object] = {}
        errors: list[BaseException] = []

        def run_reconciliation():
            try:
                result.update(path_reconciliation.reconcile_root_paths(self.db, root_id))
            except BaseException as exc:
                errors.append(exc)

        with patch.object(
            path_reconciliation,
            "_walk_files",
            self.paused_walk(candidate, candidate_observed, release_reconciliation),
        ):
            worker = threading.Thread(target=run_reconciliation)
            worker.start()
            self.assertTrue(candidate_observed.wait(5))

            raw_title_path = self.media / "catalog-path-owned-by-another-writer"
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE titles SET folder_path=? WHERE id=?",
                    (str(raw_title_path), title_id),
                )

            release_reconciliation.set()
            worker.join(5)

        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result.get("reconciled"), 0)
        with self.db.connect() as conn:
            title = conn.execute("SELECT id,folder_path FROM titles WHERE id=?", (title_id,)).fetchone()
            file_row = conn.execute("SELECT id,path FROM files WHERE id=?", (file_id,)).fetchone()
        self.assertEqual(int(title["id"]), title_id)
        self.assertEqual(title["folder_path"], str(raw_title_path))
        self.assertEqual(int(file_row["id"]), file_id)
        self.assertEqual(file_row["path"], str(episode))


if __name__ == "__main__":
    unittest.main()

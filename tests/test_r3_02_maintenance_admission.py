from __future__ import annotations

import os
import threading
import unittest

os.environ.setdefault("INFOMANCER_AUTH_MODE", "disabled")

import app.main as main
from app.maintenance_gate import APPLICATION_MAINTENANCE_GATE


class R302MaintenanceAdmissionTests(unittest.TestCase):
    def setUp(self):
        status = APPLICATION_MAINTENANCE_GATE.status()
        self.assertEqual(status["active_operations"], 0)
        APPLICATION_MAINTENANCE_GATE.end_exclusive()
        self.addCleanup(APPLICATION_MAINTENANCE_GATE.end_exclusive)

        saved_jobs = {
            "media": dict(main.media_info_job),
            "movie": dict(main.movie_match_job),
            "tv": dict(main.tv_match_job),
            "imdb": dict(main.imdb_genre_job),
        }

        def restore_jobs():
            for lock, job, saved in (
                (main.media_info_lock, main.media_info_job, saved_jobs["media"]),
                (main.movie_match_lock, main.movie_match_job, saved_jobs["movie"]),
                (main.tv_match_lock, main.tv_match_job, saved_jobs["tv"]),
                (main.imdb_genre_lock, main.imdb_genre_job, saved_jobs["imdb"]),
            ):
                with lock:
                    job.clear()
                    job.update(saved)

        self.addCleanup(restore_jobs)

    def _capture_events(self, callback):
        observed: list[tuple[str, int]] = []
        original = main.record_event

        def recording_event(_category, message, **_kwargs):
            observed.append((
                str(message),
                int(APPLICATION_MAINTENANCE_GATE.status()["active_operations"]),
            ))

        main.record_event = recording_event
        try:
            callback()
        finally:
            main.record_event = original
        return observed

    def _assert_logged_under_admission(self, observed, expected: str) -> None:
        self.assertTrue(observed, f"{expected} did not reach its logging tail")
        self.assertTrue(
            all(active > 0 for _message, active in observed),
            f"worker logged outside maintenance admission: {observed}",
        )
        self.assertTrue(
            any(expected in message for message, _active in observed),
            f"expected logging tail was not observed: {observed}",
        )
        self.assertEqual(
            APPLICATION_MAINTENANCE_GATE.status()["active_operations"], 0
        )

    def test_media_inspection_keeps_lease_through_completion_logging_tail(self):
        observed = self._capture_events(
            lambda: main.run_media_inspection([-987654321])
        )
        self._assert_logged_under_admission(observed, "Media inspection finished")

    def test_movie_match_keeps_lease_through_completion_logging_tail(self):
        observed = self._capture_events(lambda: main.run_movie_match_analysis([]))
        self._assert_logged_under_admission(observed, "Movie match lookup finished")

    def test_tv_match_keeps_lease_through_completion_logging_tail(self):
        observed = self._capture_events(lambda: main.run_tv_match_analysis([]))
        self._assert_logged_under_admission(observed, "TV match lookup finished")

    def test_unexpected_media_inspection_error_is_logged_before_lease_release(self):
        observed = self._capture_events(
            lambda: main.run_media_inspection([object()])
        )
        self.assertTrue(observed, "unexpected worker error was not logged")
        self.assertTrue(
            all(active > 0 for _message, active in observed),
            f"unexpected worker error logged outside admission: {observed}",
        )
        self.assertTrue(any(
            "unexpected error" in message.casefold()
            for message, _active in observed
        ))
        with main.media_info_lock:
            job = dict(main.media_info_job)
        self.assertEqual(job.get("status"), "error")
        self.assertTrue(job.get("error"))
        self.assertEqual(
            APPLICATION_MAINTENANCE_GATE.status()["active_operations"], 0
        )

    def test_exclusive_maintenance_blocks_recovery_visible_workers_before_database_touch(self):
        self.assertTrue(
            APPLICATION_MAINTENANCE_GATE.try_begin_exclusive("portable recovery")
        )
        original_db = main.db

        class ForbiddenDatabase:
            def connect(self):
                raise AssertionError(
                    "background worker touched catalog state after exclusive maintenance began"
                )

        main.db = ForbiddenDatabase()
        try:
            self.assertIsNone(main.run_media_inspection([-987654321]))
            self.assertIsNone(main.run_movie_match_analysis([987654321]))
            self.assertIsNone(main.run_tv_match_analysis([987654321]))
            self.assertIsNone(main.run_imdb_genre_sync([987654321], None, "test"))
        finally:
            main.db = original_db

        for lock, job in (
            (main.media_info_lock, main.media_info_job),
            (main.movie_match_lock, main.movie_match_job),
            (main.tv_match_lock, main.tv_match_job),
            (main.imdb_genre_lock, main.imdb_genre_job),
        ):
            with lock:
                state = dict(job)
            self.assertEqual(state.get("status"), "paused")
            self.assertIn("Exclusive maintenance", state.get("error", ""))

        status = APPLICATION_MAINTENANCE_GATE.status()
        self.assertTrue(status["exclusive"])
        self.assertEqual(status["active_operations"], 0)

    def test_starting_states_cover_recovery_dispatch_gaps(self):
        for lock, job in (
            (main.media_info_lock, main.media_info_job),
            (main.movie_match_lock, main.movie_match_job),
            (main.tv_match_lock, main.tv_match_job),
        ):
            with lock:
                job.clear()
                job.update({"status": "starting", "processed": 0})
            self.assertTrue(main._other_background_work_running())
            with lock:
                job.clear()
                job.update({"status": "idle"})

        with main.imdb_genre_lock:
            main.imdb_genre_job.clear()
            main.imdb_genre_job.update({"status": "starting"})
        self.assertEqual(main.imdb_genre_job.get("status"), "starting")

    def test_legacy_rename_refresh_thread_blocks_recovery_for_full_thread_lifetime(self):
        release = threading.Event()
        started = threading.Event()

        def hold_worker():
            started.set()
            release.wait(timeout=5)

        worker = threading.Thread(
            target=hold_worker,
            name="infomancer-rename-proposals",
            daemon=True,
        )
        worker.start()
        self.assertTrue(started.wait(timeout=2))
        try:
            self.assertTrue(main._other_background_work_running())
        finally:
            release.set()
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()

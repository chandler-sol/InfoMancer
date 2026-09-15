from __future__ import annotations

import os
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

        saved_job = dict(main.media_info_job)

        def restore_job():
            with main.media_info_lock:
                main.media_info_job.clear()
                main.media_info_job.update(saved_job)

        self.addCleanup(restore_job)

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

    def test_media_inspection_keeps_lease_through_completion_logging_tail(self):
        observed = self._capture_events(
            lambda: main.run_media_inspection([-987654321])
        )
        self.assertTrue(observed, "media inspection did not reach its logging tail")
        self.assertTrue(
            all(active > 0 for _message, active in observed),
            f"media inspection logged outside maintenance admission: {observed}",
        )
        self.assertTrue(
            any("Media inspection finished" in message for message, _active in observed)
        )
        self.assertEqual(
            APPLICATION_MAINTENANCE_GATE.status()["active_operations"], 0
        )

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

    def test_exclusive_maintenance_blocks_media_inspection_before_database_touch(self):
        self.assertTrue(
            APPLICATION_MAINTENANCE_GATE.try_begin_exclusive("portable recovery")
        )
        original_db = main.db

        class ForbiddenDatabase:
            def connect(self):
                raise AssertionError(
                    "media inspection touched catalog state after exclusive maintenance began"
                )

        main.db = ForbiddenDatabase()
        try:
            result = main.run_media_inspection([-987654321])
        finally:
            main.db = original_db

        self.assertIsNone(result)
        with main.media_info_lock:
            job = dict(main.media_info_job)
        self.assertEqual(job.get("status"), "paused")
        self.assertIn("Exclusive maintenance", job.get("error", ""))
        status = APPLICATION_MAINTENANCE_GATE.status()
        self.assertTrue(status["exclusive"])
        self.assertEqual(status["active_operations"], 0)

    def test_media_inspection_starting_state_blocks_recovery_dispatch_gap(self):
        with main.media_info_lock:
            main.media_info_job.clear()
            main.media_info_job.update({"status": "starting", "processed": 0})
        self.assertTrue(main._other_background_work_running())


if __name__ == "__main__":
    unittest.main()

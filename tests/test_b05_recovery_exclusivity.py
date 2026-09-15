from __future__ import annotations

import unittest

import app.main as main
from app.maintenance_gate import APPLICATION_MAINTENANCE_GATE


class ScanWorkerMaintenanceLeaseTests(unittest.TestCase):
    def setUp(self):
        status = APPLICATION_MAINTENANCE_GATE.status()
        self.assertEqual(status["active_operations"], 0)
        APPLICATION_MAINTENANCE_GATE.end_exclusive()
        self.addCleanup(APPLICATION_MAINTENANCE_GATE.end_exclusive)

    def _capture_event_admission(self, callback):
        observed: list[int] = []
        original = main.record_event

        def recording_event(*args, **kwargs):
            observed.append(
                int(APPLICATION_MAINTENANCE_GATE.status()["active_operations"])
            )

        main.record_event = recording_event
        try:
            callback()
        finally:
            main.record_event = original
        self.assertTrue(observed, "worker did not reach its final event/logging path")
        self.assertTrue(
            all(count > 0 for count in observed),
            f"worker touched its event tail outside maintenance admission: {observed}",
        )

    def test_source_scan_keeps_operation_lease_through_error_logging_tail(self):
        self._capture_event_admission(lambda: main.run_scan(-987654321))
        self.assertEqual(
            APPLICATION_MAINTENANCE_GATE.status()["active_operations"], 0
        )

    def test_title_scan_keeps_operation_lease_through_error_logging_tail(self):
        self._capture_event_admission(lambda: main.run_title_scan(-987654321))
        self.assertEqual(
            APPLICATION_MAINTENANCE_GATE.status()["active_operations"], 0
        )

    def test_scan_all_keeps_operation_lease_through_final_worker_tail(self):
        self._capture_event_admission(lambda: main.run_scan_all([]))
        self.assertEqual(
            APPLICATION_MAINTENANCE_GATE.status()["active_operations"], 0
        )

    def test_exclusive_maintenance_prevents_source_scan_from_touching_database(self):
        self.assertTrue(
            APPLICATION_MAINTENANCE_GATE.try_begin_exclusive("portable recovery")
        )
        original_missing_file_ids = main.run_scan.__globals__["missing_file_ids"]

        def forbidden_database_touch(*args, **kwargs):
            raise AssertionError("scan touched catalog state after exclusive maintenance began")

        main.run_scan.__globals__["missing_file_ids"] = forbidden_database_touch
        try:
            changed = main.run_scan(-987654321)
        finally:
            main.run_scan.__globals__["missing_file_ids"] = original_missing_file_ids
        self.assertEqual(changed, [])
        self.assertEqual(main.scan_jobs[-987654321]["status"], "paused")


if __name__ == "__main__":
    unittest.main()

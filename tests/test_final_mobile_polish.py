from pathlib import Path
import unittest
from unittest.mock import patch

from app import main


ROOT = Path(__file__).resolve().parents[1]


class FinalMobilePolishTests(unittest.TestCase):
    def test_mobile_polish_assets_cover_reported_layout_regressions(self):
        css = (ROOT / "app/static/final-mobile-polish.css").read_text(encoding="utf-8")
        script = (ROOT / "app/static/final-mobile-polish.js").read_text(encoding="utf-8")
        bootstrap = (ROOT / "app/static/app-shell-bootstrap.js").read_text(encoding="utf-8")

        self.assertIn(".settings-metrics > div", css)
        self.assertIn(".topbar:has(.global-search.open) .task-widget", css)
        self.assertIn(".safety-mode-choice", css)
        self.assertIn("#logging .settings-card-head", css)
        self.assertIn('name="hash_schedule_frequency"', css)
        self.assertIn('name="hash_schedule_day"', css)
        self.assertIn('name="hash_schedule_time"', css)
        self.assertIn("fingerprint-schedule-handoff", css)

        self.assertIn("/settings/scheduled-tasks", script)
        self.assertIn("scan-all", script)
        self.assertIn("media-fingerprints", script)
        self.assertIn("/api/tasks/${encodeURIComponent(task.id)}/cancel", script)
        self.assertIn("X-CSRF-Token", script)
        self.assertIn("role-librarian", script)

        self.assertIn("final-mobile-polish.css", bootstrap)
        self.assertIn("final-mobile-polish.js", bootstrap)

    def test_scan_all_cancellation_stops_before_next_source(self):
        calls = []
        with main.scan_all_lock:
            previous_scan_all = dict(main.scan_all_job)
        with main.scan_lock:
            previous_scan_jobs = dict(main.scan_jobs)

        def fake_run_scan(root_id: int, *, hash_after: bool = True, force_cleanup: bool = False):
            calls.append(root_id)
            with main.scan_lock:
                main.scan_jobs[root_id] = {
                    "status": "complete",
                    "source_status": "healthy",
                }
            if root_id == 1:
                result = main.cancel_background_task("scan-all")
                self.assertTrue(result["ok"])
            return []

        try:
            with patch.object(main, "run_scan", side_effect=fake_run_scan), \
                 patch.object(main, "record_event"):
                main.run_scan_all([(1, "One"), (2, "Two")])

            self.assertEqual(calls, [1])
            with main.scan_all_lock:
                self.assertEqual(main.scan_all_job["status"], "cancelled")
                self.assertEqual(main.scan_all_job["completed"], 1)
                self.assertEqual(main.scan_all_job["total"], 2)
        finally:
            with main.scan_all_lock:
                main.scan_all_job.clear()
                main.scan_all_job.update(previous_scan_all)
            with main.scan_lock:
                main.scan_jobs.clear()
                main.scan_jobs.update(previous_scan_jobs)

    def test_fingerprint_cancellation_uses_existing_cooperative_event(self):
        with main.media_hash_lock:
            previous = dict(main.media_hash_job)
            main.media_hash_job.clear()
            main.media_hash_job.update({"status": "running"})
        main.media_hash_cancel.clear()
        main.media_hash_pause.set()
        try:
            with patch.object(main, "record_event"):
                result = main.cancel_background_task("media-fingerprints")
            self.assertTrue(result["ok"])
            self.assertTrue(main.media_hash_cancel.is_set())
            self.assertFalse(main.media_hash_pause.is_set())
        finally:
            main.media_hash_cancel.clear()
            main.media_hash_pause.clear()
            with main.media_hash_lock:
                main.media_hash_job.clear()
                main.media_hash_job.update(previous)


if __name__ == "__main__":
    unittest.main()

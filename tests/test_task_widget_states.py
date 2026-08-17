from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TaskWidgetStateContracts(unittest.TestCase):
    def test_task_widget_only_surfaces_live_work_and_recent_results(self):
        script = (ROOT / "app/static/task-widget.js").read_text(encoding="utf-8")
        styles = (ROOT / "app/static/task-widget.css").read_text(encoding="utf-8")
        routes = (ROOT / "app/routes/system.py").read_text(encoding="utf-8")
        loader = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")

        self.assertIn('x.id!=="media-fingerprints-queued"', script)
        self.assertIn("TTL=600000", script)
        self.assertIn('"determinate":"indeterminate"', script)
        self.assertIn('aria-valuenow', script)
        self.assertIn('/api/task-failures', script)
        self.assertIn('has-failure', script)
        self.assertIn('Details', script)
        self.assertIn('Open Activity', script)

        self.assertIn('.task-widget.has-failure', styles)
        self.assertIn('.task-track.determinate', styles)
        self.assertIn('.task-track.indeterminate', styles)
        self.assertIn('.task-state-badge.failed', styles)
        self.assertIn('.task-state-badge.complete', styles)

        self.assertIn('workspace-ui-core.js', loader)
        self.assertIn('task-widget.js', loader)
        self.assertIn('task-widget.css', loader)
        self.assertIn('@router.get("/api/task-failures"', routes)
        self.assertIn('dependencies=[Depends(require_librarian)]', routes)


if __name__ == "__main__":
    unittest.main()

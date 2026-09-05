from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SourceLiveRefreshContracts(unittest.TestCase):
    def test_sources_mark_scans_that_were_active_when_page_rendered(self):
        template = (ROOT / "app/templates/sources.html").read_text(encoding="utf-8")

        self.assertIn('data-scan-active="{{', template)
        self.assertIn("jobs.get(root.id, {}).get('status')", template)
        self.assertIn("('starting', 'running')", template)

    def test_sources_reuse_task_center_polling_and_refresh_after_scan_finishes(self):
        source = (ROOT / "app/static/source-actions.js").read_text(encoding="utf-8")

        self.assertIn('document.addEventListener("infomancer:tasks"', source)
        self.assertIn('id === "scan-all" || /^scan-\\d+$/.test(id)', source)
        self.assertIn('observedScanTasks.add(`scan-${link.dataset.sourceId}`)', source)
        self.assertIn("rememberScanTask(form, kind)", source)
        self.assertIn("if (!currentScanTasks.has(taskId))", source)
        self.assertIn("window.location.reload()", source)
        self.assertNotIn("fetch('/api/tasks'", source)
        self.assertNotIn('fetch("/api/tasks"', source)
        self.assertNotIn("setInterval(", source)


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SourceScanStatusUiTests(unittest.TestCase):
    def test_sources_template_exposes_per_source_scan_status(self):
        template = (ROOT / "app" / "templates" / "sources.html").read_text(encoding="utf-8")
        self.assertIn("data-source-scan-status", template)
        self.assertIn("jobs.get(root.id, {}).get('status')", template)
        self.assertIn("video files", template)
        self.assertIn("titles", template)

    def test_source_actions_reuses_canonical_task_events_for_row_progress(self):
        script = (ROOT / "app" / "static" / "source-actions.js").read_text(encoding="utf-8")
        self.assertIn('document.addEventListener("infomancer:tasks"', script)
        self.assertIn("rowForSourceId", script)
        self.assertIn("setRowScanState", script)
        self.assertIn('scanButton.textContent = "Scanning…"', script)
        self.assertNotIn('fetch("/api/scans/', script)

    def test_source_scan_status_has_visible_active_row_treatment(self):
        css = (ROOT / "app" / "static" / "sources.css").read_text(encoding="utf-8")
        self.assertIn(".root-row.source-row-scanning", css)
        self.assertIn(".source-scan-status", css)
        self.assertIn("animation:source-action-pulse", css)


if __name__ == "__main__":
    unittest.main()

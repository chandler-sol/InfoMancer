from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SourceHealthModalTests(unittest.TestCase):
    def test_sources_load_health_modal_assets(self):
        bootstrap = (ROOT / "app/static/app-shell-bootstrap.js").read_text(encoding="utf-8")
        self.assertIn("window.location.pathname === '/sources'", bootstrap)
        self.assertIn("source-health.css", bootstrap)
        self.assertIn("source-health.js", bootstrap)

    def test_health_guidance_wraps_instead_of_truncating(self):
        css = (ROOT / "app/static/source-health.css").read_text(encoding="utf-8")
        self.assertIn(".source-health-guidance", css)
        self.assertIn("white-space: normal", css)
        self.assertIn("overflow: visible", css)
        self.assertIn("text-overflow: clip", css)
        self.assertIn("overflow-wrap: anywhere", css)

    def test_degraded_badge_opens_diagnostic_modal_instead_of_library_link(self):
        script = (ROOT / "app/static/source-health.js").read_text(encoding="utf-8")
        self.assertIn(".source-health-degraded, .source-health-offline", script)
        self.assertIn("event.preventDefault()", script)
        self.assertIn("event.stopPropagation()", script)
        self.assertIn("dialog.showModal()", script)
        self.assertIn("View in Review", script)
        self.assertIn("Related logs", script)
        self.assertIn("Rescan source", script)
        self.assertIn("Open title", script)

    def test_health_endpoint_recovers_exact_guarded_files_from_scan_id(self):
        route = (ROOT / "app/routes/source_health.py").read_text(encoding="utf-8")
        routers = (ROOT / "app/routes/__init__.py").read_text(encoding="utf-8")
        self.assertIn('/api/sources/{root_id}/health-details', route)
        self.assertIn("context_json", route)
        self.assertIn("scan_id", route)
        self.assertIn("f.seen_scan!=?", route)
        self.assertIn("source-degraded", route)
        self.assertIn("/review?bucket=sources", route)
        self.assertIn("build_source_health_router", routers)


if __name__ == "__main__":
    unittest.main()

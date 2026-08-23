from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "app" / "templates" / "logs.html"


class LiveLogStatusContracts(unittest.TestCase):
    def test_live_status_reports_refresh_failures_instead_of_failing_silently(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn('id="log-live-status"', template)
        self.assertIn('role="status"', template)
        self.assertIn("const markPaused = () =>", template)
        self.assertIn("Live updates are paused · retrying automatically", template)
        self.assertIn("if (!response.ok) {", template)
        self.assertIn("markPaused();", template)
        self.assertNotIn("if (!response.ok) return;", template)
        self.assertNotIn("catch (_) {}", template)

    def test_live_log_json_parse_is_guarded(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("const text = await response.text();", template)
        self.assertIn("payload = JSON.parse(text);", template)
        self.assertNotIn("await response.json()", template)

    def test_live_log_payload_shape_must_include_events_array(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("if (!Array.isArray(payload?.events)) {", template)
        self.assertIn("const events = payload.events;", template)
        self.assertNotIn("Array.isArray(payload?.events) ? payload.events : []", template)


if __name__ == "__main__":
    unittest.main()

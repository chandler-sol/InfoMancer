from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DashboardCommandContracts(unittest.TestCase):
    def test_operational_dashboard_is_the_single_supported_home_surface(self):
        route = (ROOT / "app/routes/dashboard.py").read_text(encoding="utf-8")
        template = (ROOT / "app/templates/dashboard_command.html").read_text(encoding="utf-8")

        self.assertIn('"dashboard_command.html"', route)
        self.assertIn("single supported Home surface", route)
        self.assertNotIn('dashboard_old_test.html', route)
        self.assertIn("event_log.activity(user_id, unread_only=True", route)
        self.assertIn("activity_unread_display", route)

        self.assertIn("Your library today", template)
        self.assertIn("SINCE YOUR LAST CHECK-IN", template)
        self.assertIn("NEEDS ATTENTION", template)
        self.assertIn("RECENTLY CHANGED", template)
        self.assertIn('data-home-live-task', template)
        self.assertNotIn('layout=old', template)

    def test_operational_dashboard_has_scoped_responsive_styles(self):
        styles = (ROOT / "app/static/dashboard-command.css").read_text(encoding="utf-8")
        self.assertIn(".home-ops-overview", styles)
        self.assertIn(".home-ops-health", styles)
        self.assertIn(".home-ops-workspace", styles)
        self.assertIn(".home-favorite-strip", styles)
        self.assertIn("@media(max-width:760px)", styles)
        self.assertIn("@media(prefers-reduced-motion:reduce)", styles)
        self.assertIn(".home-ops-panel-head>a{font-weight:800}", styles)
        self.assertIn(".home-change-card>b{font-weight:900}", styles)


if __name__ == "__main__":
    unittest.main()

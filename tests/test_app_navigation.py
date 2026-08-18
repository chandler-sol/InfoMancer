from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AppNavigationContracts(unittest.TestCase):
    def test_workspace_ui_loads_navigation_assets_with_version(self):
        loader = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")

        self.assertIn("app-navigation.css", loader)
        self.assertIn("app-navigation.js", loader)
        self.assertIn("${version}", loader)

    def test_internal_navigation_gets_immediate_feedback_without_hijacking_navigation(self):
        script = (ROOT / "app/static/app-navigation.js").read_text(encoding="utf-8")
        css = (ROOT / "app/static/app-navigation.css").read_text(encoding="utf-8")

        self.assertIn('document.addEventListener("click"', script)
        self.assertIn("event.defaultPrevented", script)
        self.assertIn("url.origin !== window.location.origin", script)
        self.assertIn('classList.add("app-navigation-pending")', script)
        self.assertNotIn("preventDefault()", script)
        self.assertIn("html.app-navigation-pending::after", css)
        self.assertIn("infomancer-navigation-progress", css)
        self.assertIn("prefers-reduced-motion:reduce", css)


if __name__ == "__main__":
    unittest.main()

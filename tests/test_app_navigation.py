from pathlib import Path
import tempfile
import unittest

from app.routes.performance import _static_asset_version


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

    def test_static_asset_version_survives_restart_equivalent_recalculation(self):
        with tempfile.TemporaryDirectory() as temporary:
            static_dir = Path(temporary)
            (static_dir / "app.css").write_text("body{color:white}", encoding="utf-8")
            nested = static_dir / "scripts"
            nested.mkdir()
            (nested / "app.js").write_text("const ready = true;", encoding="utf-8")

            first = _static_asset_version(static_dir)
            self.assertEqual(first, _static_asset_version(static_dir))

            (static_dir / "app.css").write_text("body{color:black}", encoding="utf-8")
            self.assertNotEqual(first, _static_asset_version(static_dir))


if __name__ == "__main__":
    unittest.main()

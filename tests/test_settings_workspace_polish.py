from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SettingsWorkspacePolishContracts(unittest.TestCase):
    def test_settings_stylesheet_owns_polish_without_workspace_loader_dependency(self):
        settings_css = (ROOT / "app/static/settings.css").read_text(encoding="utf-8")
        workspace_ui = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")

        self.assertIn('@import url("settings-polish.css")', settings_css)
        self.assertNotIn("settings-polish.css", workspace_ui)
        self.assertNotIn("settings-polish.js", workspace_ui)

    def test_general_settings_are_balanced_without_changing_form_controls(self):
        script = (ROOT / "app/static/settings-polish.js").read_text(encoding="utf-8")

        self.assertIn('form.settings-page-grid[action="/settings/general"]', script)
        self.assertIn('select[name="default_library_view"]', script)
        self.assertIn("regionalCard.append(defaultView)", script)
        self.assertIn("settings-field-stack", script)
        self.assertIn("REGION & INTERFACE", script)
        self.assertIn("LIBRARY BROWSING", script)

    def test_settings_cards_share_first_paint_alignment_and_complex_pages_keep_shape(self):
        css = (ROOT / "app/static/settings-polish.css").read_text(encoding="utf-8")

        self.assertIn("body .settings-page-grid", css)
        self.assertIn('body form.settings-page-grid[action="/settings/general"]', css)
        self.assertIn("align-items: stretch", css)
        self.assertIn("min-height: 286px", css)
        self.assertIn("body .settings-page-grid:has(.system-overview-grid)", css)
        self.assertIn("body .scheduled-task-layout", css)
        self.assertIn("body .scheduled-fingerprint-card", css)
        self.assertIn("grid-column: 1 / -1", css)
        self.assertIn("body .split:has(.source-add-panel)", css)


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SettingsWorkspacePolishContracts(unittest.TestCase):
    def test_workspace_loader_mounts_settings_polish_only_on_settings_surfaces(self):
        script = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")

        self.assertIn("document.querySelector('.settings-section-nav')", script)
        self.assertIn("settings-polish.css", script)
        self.assertIn("settings-polish.js", script)

    def test_general_settings_are_balanced_without_changing_form_controls(self):
        script = (ROOT / "app/static/settings-polish.js").read_text(encoding="utf-8")

        self.assertIn('form.settings-page-grid[action="/settings/general"]', script)
        self.assertIn('select[name="default_library_view"]', script)
        self.assertIn("regionalCard.append(defaultView)", script)
        self.assertIn("settings-field-stack", script)
        self.assertIn("REGION & INTERFACE", script)
        self.assertIn("LIBRARY BROWSING", script)

    def test_settings_cards_share_alignment_and_scheduled_tasks_are_reorganized(self):
        css = (ROOT / "app/static/settings-polish.css").read_text(encoding="utf-8")

        self.assertIn(".settings-page-grid", css)
        self.assertIn("align-items: stretch", css)
        self.assertIn(".settings-general-card", css)
        self.assertIn("grid-template-rows: repeat(2", css)
        self.assertIn("body.settings-section-system .settings-page-grid", css)
        self.assertIn("body.settings-section-scheduled-tasks .scheduled-fingerprint-card", css)
        self.assertIn("grid-column: 1 / -1", css)
        self.assertIn("body.settings-section-sources .split", css)


if __name__ == "__main__":
    unittest.main()

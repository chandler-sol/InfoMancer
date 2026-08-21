from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SettingsLayoutConsistencyContracts(unittest.TestCase):
    def test_grid_forms_fill_their_settings_columns(self):
        css = (ROOT / "app/static/settings-polish.css").read_text(encoding="utf-8")

        self.assertIn("body .settings-page-grid > .settings-form", css)
        self.assertIn("max-width: none", css)
        self.assertIn("width: 100%", css)

    def test_user_management_uses_shared_settings_shell(self):
        template = (ROOT / "app/templates/admin_users.html").read_text(encoding="utf-8")

        self.assertIn('class="settings-heading app-settings-heading"', template)
        self.assertIn('class="settings-page-grid user-management-grid"', template)
        self.assertIn('class="panel settings-card user-list-panel"', template)
        self.assertIn('class="panel settings-card settings-form create-user-form"', template)
        self.assertNotIn('class="settings-grid"', template)

    def test_user_management_cards_do_not_force_equal_heights(self):
        css = (ROOT / "app/static/settings-polish.css").read_text(encoding="utf-8")

        self.assertIn("body .settings-page-grid.user-management-grid", css)
        self.assertIn("body .settings-page-grid.user-management-grid > .settings-card", css)
        self.assertIn("height: auto", css)


if __name__ == "__main__":
    unittest.main()

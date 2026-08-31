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

    def test_system_media_and_export_cards_stretch_as_a_pair(self):
        css = (ROOT / "app/static/settings.css").read_text(encoding="utf-8")

        self.assertIn("> #media-information", css)
        self.assertIn("> #media-information + .settings-card", css)
        self.assertIn("align-self:stretch", css)

    def test_fingerprint_checkboxes_keep_checkbox_and_label_together(self):
        css = (ROOT / "app/static/settings.css").read_text(encoding="utf-8")

        self.assertIn("label.checkbox-label", css)
        self.assertIn("label.scheduled-checkbox", css)
        self.assertIn('input[type="checkbox"]', css)
        self.assertIn("width:16px", css)

    def test_accessible_storage_locations_are_compact_links(self):
        template = (ROOT / "app/templates/settings.html").read_text(encoding="utf-8")
        css = (ROOT / "app/static/settings.css").read_text(encoding="utf-8")

        self.assertIn('class="storage-location-chips"', template)
        self.assertIn('class="storage-location-chip" href="/sources"', template)
        self.assertNotIn("{% if not loop.last %}<br>{% endif %}", template)
        self.assertIn(".storage-location-chips", css)
        self.assertIn("flex-wrap:wrap", css)

    def test_scheduled_tasks_use_one_consistent_medium_width_column(self):
        css = (ROOT / "app/static/settings.css").read_text(encoding="utf-8")

        self.assertIn("body .scheduled-task-layout", css)
        self.assertIn("width:min(100%,1040px)", css)
        self.assertIn("grid-template-columns:minmax(0,1fr)", css)
        self.assertIn("body .scheduled-fingerprint-card", css)
        self.assertIn("grid-column:auto", css)


if __name__ == "__main__":
    unittest.main()

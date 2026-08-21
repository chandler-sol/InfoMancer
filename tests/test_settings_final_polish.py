from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"
STATIC = ROOT / "app" / "static"


class SettingsFinalPolishContracts(unittest.TestCase):
    def test_scheduled_tasks_use_human_schedule_controls_without_placeholder_card(self):
        template = (TEMPLATES / "scheduled_tasks.html").read_text(encoding="utf-8")
        script = (STATIC / "scheduled-tasks.js").read_text(encoding="utf-8")
        styles = (STATIC / "scheduled-tasks.css").read_text(encoding="utf-8")

        self.assertNotIn("scheduled-task-future", template)
        self.assertNotIn("SCHEDULE CENTER", template)
        self.assertIn('name="hash_schedule_day"', template)
        self.assertIn("data-schedule-day", template)
        self.assertIn('name="hash_schedule_time"', template)
        self.assertIn("data-schedule-hour", template)
        self.assertIn("data-schedule-minute", template)
        self.assertIn("data-schedule-period", template)
        self.assertIn("weekdays", script)
        self.assertIn("syncTime", script)
        self.assertIn(".scheduled-checkbox", styles)
        self.assertIn("padding-right: 42px", styles)

    def test_settings_spacing_assets_are_owned_by_their_surfaces(self):
        recovery = (TEMPLATES / "recovery_restore.html").read_text(encoding="utf-8")
        settings_nav = (TEMPLATES / "_settings_nav.html").read_text(encoding="utf-8")
        settings_final = (STATIC / "settings-final-polish.css").read_text(encoding="utf-8")

        self.assertIn("recovery-polish.css", recovery)
        self.assertIn("settings-final-polish.css", settings_nav)
        self.assertIn(".sources-page-head ~ .settings-handoff", settings_final)
        self.assertIn("display: none !important", settings_final)

    def test_saved_views_explain_the_saved_state_and_keep_checkbox_aligned(self):
        script = (STATIC / "library-saved-views-polish.js").read_text(encoding="utf-8")
        styles = (STATIC / "library-saved-views-polish.css").read_text(encoding="utf-8")

        self.assertIn("saved-view-explainer", script)
        self.assertIn("current Library scope, filters, and sorting", script)
        self.assertIn(".saved-view-pin-choice input[type=\"checkbox\"]", styles)
        self.assertIn("width: 16px", styles)

    def test_bulk_collection_choices_have_dedicated_modal_layout(self):
        template = (TEMPLATES / "organize_bulk.html").read_text(encoding="utf-8")
        styles = (STATIC / "organize-bulk-polish.css").read_text(encoding="utf-8")

        self.assertIn("organize-bulk-polish.css", template)
        self.assertIn("organize-collection-grid", template)
        self.assertIn("organize-collection-option", template)
        self.assertIn("grid-template-columns: 18px minmax(0, 1fr)", styles)

    def test_roadmap_tracks_multiple_external_search_providers_after_08(self):
        roadmap = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")

        self.assertIn("multiple user-configured external search providers", roadmap)
        self.assertIn("## 0.9: candidate product-expansion themes", roadmap)


if __name__ == "__main__":
    unittest.main()

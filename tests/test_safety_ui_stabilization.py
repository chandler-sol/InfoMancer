import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class SafetyUiStabilizationContractTests(unittest.TestCase):
    def test_lockdown_guards_automatic_permanent_trash_cleanup(self):
        background = (ROOT / "app" / "background.py").read_text(encoding="utf-8")
        self.assertIn('self.app_settings.get("lockdown_mode") == "1"', background)
        self.assertIn("preventing permanent managed-trash deletion", background)

    def test_sources_use_standard_confirmation_and_one_edit_surface(self):
        template = (ROOT / "app" / "templates" / "sources.html").read_text(encoding="utf-8")
        routes = (ROOT / "app" / "routes" / "settings.py").read_text(encoding="utf-8")
        self.assertIn("source-action-rail", template)
        self.assertIn("source-trash-button", template)
        self.assertIn("data-workspace-confirm", template)
        self.assertIn("media files will not be deleted", template)
        self.assertNotIn("Type REMOVE", template)
        self.assertIn("closeEditors(editor)", template)
        self.assertIn('event.key === "Escape"', template)
        self.assertIn('@librarian_post("/roots/{root_id}/delete")', routes)
        self.assertNotIn('confirm != "REMOVE"', routes)

    def test_quality_defaults_and_review_alignment_are_wired(self):
        routes = (ROOT / "app" / "routes" / "review.py").read_text(encoding="utf-8")
        template = (ROOT / "app" / "templates" / "library_health.html").read_text(encoding="utf-8")
        styles = (ROOT / "app" / "static" / "workspace.css").read_text(encoding="utf-8")
        self.assertIn('/library-health/quality-defaults', routes)
        self.assertIn("LIBRARY DEFAULTS", template)
        self.assertIn("Inheriting library defaults", (ROOT / "app" / "mie.py").read_text(encoding="utf-8"))
        self.assertIn("grid-template-columns: auto minmax(0, 1fr) auto", styles)

    def test_system_settings_expose_standard_and_lockdown_modes(self):
        template = (ROOT / "app" / "templates" / "settings.html").read_text(encoding="utf-8")
        routes = (ROOT / "app" / "routes" / "settings.py").read_text(encoding="utf-8")
        self.assertIn("Standard Mode", template)
        self.assertIn("Lockdown Mode", template)
        self.assertIn('/settings/safety', template)
        self.assertIn('@librarian_post("/settings/safety")', routes)


if __name__ == "__main__":
    unittest.main()

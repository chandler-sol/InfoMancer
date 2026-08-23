import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class SafetyUiStabilizationContractTests(unittest.TestCase):
    def test_lockdown_guards_automatic_permanent_trash_cleanup(self):
        background = (ROOT / "app" / "background.py").read_text(encoding="utf-8")
        self.assertIn('protection_mode in {"readonly", "lockdown"}', background)
        self.assertIn("preventing permanent managed-trash deletion", background)

    def test_sources_use_standard_confirmation_and_one_edit_surface(self):
        template = (ROOT / "app" / "templates" / "sources.html").read_text(encoding="utf-8")
        actions = (ROOT / "app" / "static" / "source-actions.js").read_text(encoding="utf-8")
        routes = (ROOT / "app" / "routes" / "settings.py").read_text(encoding="utf-8")
        self.assertIn("source-action-rail", template)
        self.assertIn("source-trash-button", template)
        self.assertIn("data-workspace-confirm", template)
        self.assertIn("media files will not be deleted", template)
        self.assertNotIn("Type REMOVE", template)
        self.assertNotIn("<script>", template)
        self.assertIn("closeEditors(editor)", actions)
        self.assertIn('event.key === "Escape"', actions)
        self.assertIn('@librarian_post("/roots/{root_id}/delete")', routes)
        self.assertNotIn('confirm != "REMOVE"', routes)
        self.assertIn("Media source removed from InfoMancer", routes)

    def test_quality_defaults_and_review_alignment_are_wired(self):
        routes = (ROOT / "app" / "routes" / "review.py").read_text(encoding="utf-8")
        template = (ROOT / "app" / "templates" / "library_health.html").read_text(encoding="utf-8")
        styles = (ROOT / "app" / "static" / "workspace.css").read_text(encoding="utf-8")
        self.assertIn('/library-health/quality-defaults', routes)
        self.assertEqual(routes.count('"quality_defaults": mie.library_quality_defaults()'), 2)
        self.assertIn("LIBRARY DEFAULTS", template)
        self.assertIn("Remove source override", template)
        self.assertIn("Inheriting library defaults", (ROOT / "app" / "mie.py").read_text(encoding="utf-8"))
        self.assertIn("grid-template-columns: minmax(0, 1fr) auto", styles)
        self.assertIn('.mie-finding-head:has(> input[type="checkbox"])', styles)
        self.assertIn("grid-template-columns: auto minmax(0, 1fr) auto", styles)

    def test_managed_trash_explains_lockdown_pause(self):
        routes = (ROOT / "app" / "routes" / "review.py").read_text(encoding="utf-8")
        trash = (ROOT / "app" / "templates" / "duplicate_trash.html").read_text(encoding="utf-8")
        preview = (ROOT / "app" / "templates" / "duplicate_trash_preview.html").read_text(encoding="utf-8")
        self.assertIn('"lockdown_mode": app_settings.get("lockdown_mode") == "1"', routes)
        self.assertIn('"read_only_mode": app_settings.get("read_only_mode") == "1"', routes)
        self.assertIn("Automatic permanent removal is paused", trash)
        self.assertIn("Paused by Lockdown Mode", preview)

    def test_system_settings_expose_read_only_standard_and_lockdown_modes(self):
        template = (ROOT / "app" / "templates" / "settings.html").read_text(encoding="utf-8")
        routes = (ROOT / "app" / "routes" / "settings.py").read_text(encoding="utf-8")
        self.assertIn("Read-Only Mode", template)
        self.assertIn("Standard Mode", template)
        self.assertIn("Lockdown Mode", template)
        self.assertIn('/settings/safety', template)
        self.assertIn('@librarian_post("/settings/safety")', routes)


if __name__ == "__main__":
    unittest.main()

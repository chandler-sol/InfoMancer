import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class UiPolishContractTests(unittest.TestCase):
    def test_source_remove_uses_normal_button_shell(self):
        template = (ROOT / "app" / "templates" / "sources.html").read_text(encoding="utf-8")
        styles = (ROOT / "app" / "static" / "workspace.css").read_text(encoding="utf-8")
        self.assertIn('class="button source-trash-button"', template)
        self.assertIn(".source-trash-button.button", styles)
        self.assertIn("width: 19px; height: 19px", styles)

    def test_shift_range_selection_suppresses_native_text_selection(self):
        script = (ROOT / "app" / "static" / "workspace-core.js").read_text(encoding="utf-8")
        styles = (ROOT / "app" / "static" / "workspace.css").read_text(encoding="utf-8")
        self.assertIn('document.addEventListener("mousedown"', script)
        self.assertIn("event.shiftKey", script)
        self.assertIn("event.preventDefault()", script)
        self.assertIn(".cover-card[data-workspace-title-id]", styles)
        self.assertIn("user-select: none", styles)

    def test_system_safety_fingerprints_and_backups_have_polish_hooks(self):
        template = (ROOT / "app" / "templates" / "settings.html").read_text(encoding="utf-8")
        styles = (ROOT / "app" / "static" / "workspace.css").read_text(encoding="utf-8")
        self.assertLess(template.index('id="fingerprints"'), template.index('id="safety"'))
        self.assertLess(template.index('id="safety"'), template.index('id="backups"'))
        self.assertIn('id="hashing-settings-form"', template)
        self.assertIn("hashing-command-bar", template)
        self.assertIn("hash-time-label", template)
        self.assertIn("backup-protection-card", template)
        self.assertIn("backup-header-actions", template)
        self.assertIn("backup-upload-row", template)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr))", styles)


if __name__ == "__main__":
    unittest.main()

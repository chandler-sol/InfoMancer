import unittest
from pathlib import Path


class MobileUiContractTests(unittest.TestCase):
    def test_organize_dialog_keeps_progressive_fallback(self):
        base = Path("app/templates/base.html").read_text(encoding="utf-8")
        organize = Path("app/templates/organize.html").read_text(encoding="utf-8")
        script = Path("app/static/organize-dialog.js").read_text(encoding="utf-8")

        self.assertIn('id="organize-dialog"', base)
        self.assertIn("data-organize-content", organize)
        self.assertIn("typeof dialog.showModal", script)
        self.assertIn("window.location.assign(url)", script)
        self.assertIn('dialog.addEventListener("cancel"', script)

    def test_mobile_task_widget_and_footer_have_explicit_states(self):
        base = Path("app/templates/base.html").read_text(encoding="utf-8")
        progress = Path("app/static/progress.css").read_text(encoding="utf-8")
        header = Path("app/static/header.css").read_text(encoding="utf-8")

        self.assertIn('aria-label="No background tasks or notifications"', base)
        self.assertIn('classList.add("has-attention")', base)
        self.assertIn(".task-widget-toggle .task-card-copy", progress)
        self.assertIn("footer a:hover", header)

    def test_installation_name_is_hidden_but_compatibility_key_remains(self):
        settings = Path("app/templates/settings.html").read_text(encoding="utf-8")
        setup = Path("app/templates/getting_started.html").read_text(encoding="utf-8")
        app_settings = Path("app/app_settings.py").read_text(encoding="utf-8")

        self.assertNotIn('name="installation_name"', settings)
        self.assertNotIn('name="installation_name"', setup)
        self.assertIn('"installation_name"', app_settings)


if __name__ == "__main__":
    unittest.main()

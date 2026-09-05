from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DesktopFirstRunRecoveryTests(unittest.TestCase):
    def test_desktop_detects_pending_setup_from_live_core_not_database_creation(self):
        rust = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
        self.assertIn("fn probe_setup_pending(port: u16)", rust)
        self.assertIn('write_all(b"GET /setup HTTP/1.1', rust)
        self.assertIn("200 => Ok(true)", rust)
        self.assertIn("303", rust)
        self.assertIn("let first_run = match wait_for_local_core(port).await", rust)
        self.assertNotIn('let first_run = !data_dir.join("infomancer.db").exists()', rust)

    def test_desktop_preserves_launcher_history_during_setup_and_remote_connect(self):
        launcher = (ROOT / "desktop/ui/index.html").read_text(encoding="utf-8")
        self.assertIn("window.location.assign(startup.setup_url)", launcher)
        self.assertIn("window.location.assign(normalized)", launcher)
        self.assertNotIn("window.location.replace(startup.setup_url)", launcher)

    def test_token_copy_has_fallback_and_manual_selection(self):
        launcher = (ROOT / "desktop/ui/index.html").read_text(encoding="utf-8")
        self.assertIn("fallbackCopy", launcher)
        self.assertIn("document.execCommand('copy')", launcher)
        self.assertIn("Select & copy", launcher)
        self.assertIn("user-select:all", launcher)

    def test_first_run_account_page_can_return_to_launcher(self):
        template = (ROOT / "app/templates/setup.html").read_text(encoding="utf-8")
        script = (ROOT / "app/static/setup-navigation.js").read_text(encoding="utf-8")
        self.assertIn("data-first-run-back", template)
        self.assertIn("setup-navigation.js", template)
        self.assertIn("window.history.back()", script)
        self.assertIn("window.history.length <= 1", script)


if __name__ == "__main__":
    unittest.main()

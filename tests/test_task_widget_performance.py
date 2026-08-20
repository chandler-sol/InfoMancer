from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TaskWidgetPerformanceTests(unittest.TestCase):
    def test_task_widget_reuses_base_poll_and_avoids_unchanged_dom_work(self):
        source = (ROOT / "app/static/task-widget.js").read_text(encoding="utf-8")

        self.assertIn("let receivedTaskEvent = false", source)
        self.assertIn("receivedTaskEvent = true", source)
        self.assertIn("if (!receivedTaskEvent) sync();", source)
        self.assertIn("}, 350);", source)
        self.assertIn("const taskSetSignature =", source)
        self.assertIn("if (nextSignature === activeSignature) return;", source)
        self.assertIn("activeSignature = nextSignature", source)

    def test_library_controller_batch_is_fetched_without_network_waterfall(self):
        source = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")

        self.assertIn("const loadScriptsOrdered = async (paths)", source)
        self.assertIn("const pending = paths.map((path) => loadScript(path));", source)
        self.assertIn("await Promise.all(pending);", source)
        self.assertIn("script.async = false", source)
        self.assertIn('return loadScriptsOrdered([', source)

    def test_account_avatar_has_one_network_owner(self):
        loader = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")
        auth_css = (ROOT / "app/static/auth.css").read_text(encoding="utf-8")

        self.assertIn('avatarImage.src = "/account/avatar/current"', loader)
        self.assertNotIn("background-image:url('/account/avatar/current')", auth_css)
        self.assertIn(".account-avatar{overflow:hidden}", auth_css)


if __name__ == "__main__":
    unittest.main()

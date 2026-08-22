from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TaskWidgetPerformanceTests(unittest.TestCase):
    def test_task_widget_owns_polling_and_avoids_unchanged_dom_work(self):
        source = (ROOT / "app/static/task-widget.js").read_text(encoding="utf-8")

        self.assertEqual(source.count("fetch('/api/tasks'"), 1)
        self.assertIn("const taskSetSignature =", source)
        self.assertIn("const signatureChanged = nextSignature !== activeSignature", source)
        self.assertIn("if (signatureChanged || scheduledChanged) queueMicrotask(render);", source)
        self.assertIn("activeSignature = nextSignature", source)
        self.assertIn("const localOnlyTask =", source)
        self.assertIn("!localOnlyTask(task)", source)

    def test_library_controller_batch_is_fetched_without_network_waterfall(self):
        source = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")

        self.assertIn("await loadScript('library-controller.js')", source)
        self.assertIn("const pending = [", source)
        self.assertIn("].map((path) => loadScript(path));", source)
        self.assertIn("return Promise.all(pending);", source)
        self.assertIn("script.async = false", source)

    def test_account_avatar_has_one_network_owner(self):
        loader = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")
        auth_css = (ROOT / "app/static/auth.css").read_text(encoding="utf-8")

        self.assertIn("avatarImage.src = '/account/avatar/current'", loader)
        self.assertNotIn("background-image:url('/account/avatar/current')", auth_css)
        self.assertIn(".account-avatar{overflow:hidden}", auth_css)


if __name__ == "__main__":
    unittest.main()

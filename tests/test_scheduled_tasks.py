from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ScheduledTaskWorkspaceContracts(unittest.TestCase):
    def test_scheduled_tasks_page_is_registered_and_librarian_scoped(self):
        routes_init = (ROOT / "app/routes/__init__.py").read_text(encoding="utf-8")
        routes = (ROOT / "app/routes/scheduled_tasks.py").read_text(encoding="utf-8")
        template = (ROOT / "app/templates/scheduled_tasks.html").read_text(encoding="utf-8")
        nav = (ROOT / "app/templates/_settings_nav.html").read_text(encoding="utf-8")

        self.assertIn("build_scheduled_tasks_router", routes_init)
        self.assertLess(
            routes_init.index("build_scheduled_tasks_router,"),
            routes_init.index("build_settings_router,"),
        )
        self.assertIn('@librarian_get("/settings/scheduled-tasks"', routes)
        self.assertIn('@librarian_post("/settings/scheduled-tasks/fingerprints"', routes)
        self.assertIn('@librarian_post("/settings/scheduled-tasks/trash-retention"', routes)
        self.assertIn("dependencies.append(Depends(require_librarian))", routes)
        self.assertIn("Scheduled Tasks", template)
        self.assertIn("scheduled-tasks.css", template)
        self.assertIn("Next run", template)
        self.assertIn("Last scheduled run", template)
        self.assertIn("MANAGED TRASH", template)
        self.assertIn('href="/settings/scheduled-tasks"', nav)

    def test_task_widget_links_to_schedule_center_without_showing_backlog(self):
        script = (ROOT / "app/static/task-widget.js").read_text(encoding="utf-8")
        self.assertIn('link.href = "/settings/scheduled-tasks"', script)
        self.assertIn('task.id !== "media-fingerprints-queued"', script)
        self.assertIn("No Tasks Currently Active", script)


if __name__ == "__main__":
    unittest.main()

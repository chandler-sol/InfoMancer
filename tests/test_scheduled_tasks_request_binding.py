from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ScheduledTasksRequestBindingContracts(unittest.TestCase):
    def test_request_is_imported_at_module_scope_for_fastapi(self):
        route = (ROOT / "app/routes/scheduled_tasks.py").read_text(encoding="utf-8")

        self.assertIn("from fastapi import APIRouter, Depends, Request", route)
        self.assertNotIn('Request = ctx.get("Request")', route)
        self.assertIn("def scheduled_tasks_page(request: Request):", route)
        self.assertIn("def save_fingerprint_schedule(\n        request: Request,", route)
        self.assertIn("def save_trash_retention(request: Request", route)


if __name__ == "__main__":
    unittest.main()

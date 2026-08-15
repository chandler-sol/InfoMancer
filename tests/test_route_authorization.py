from __future__ import annotations

import unittest

from app import main
from app.access import require_librarian


class RouteAuthorizationTests(unittest.TestCase):
    def dependencies_for(self, path: str, method: str):
        for route in main.app.routes:
            if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
                return [item.call for item in route.dependant.dependencies]
        self.fail(f"Route not found: {method} {path}")

    def test_sensitive_routes_attach_librarian_dependency(self):
        for path, method in (
            ("/settings", "GET"), ("/sources", "GET"), ("/duplicates", "GET"),
            ("/admin/users", "GET"), ("/scan-all", "POST"),
            ("/titles/{title_id}/metadata/enrich", "POST"),
        ):
            with self.subTest(path=path, method=method):
                self.assertIn(require_librarian, self.dependencies_for(path, method))

    def test_member_self_service_routes_do_not_require_librarian(self):
        for path, method in (("/account/profile", "GET"), ("/account/profile", "POST"), ("/titles/{title_id}/favorite", "POST")):
            with self.subTest(path=path, method=method):
                self.assertNotIn(require_librarian, self.dependencies_for(path, method))


if __name__ == "__main__":
    unittest.main()

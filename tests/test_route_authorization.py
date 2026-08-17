from __future__ import annotations

import re
import unittest

from app import main
from app.access import require_librarian


class RouteAuthorizationTests(unittest.TestCase):
    def dependencies_for(self, path: str, method: str):
        for route in main.app.routes:
            if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
                return [item.call for item in route.dependant.dependencies]
        self.fail(f"Route not found: {method} {path}")

    @staticmethod
    def member_safe_unsafe_route(path: str) -> bool:
        if path in {
            "/login", "/setup", "/forgot-password", "/logout",
            "/titles/organize-bulk", "/tags/create", "/activity/read",
        }:
            return True
        if path.startswith((
            "/activate/", "/recovery/", "/account/", "/engagement/", "/library/views",
        )):
            return True
        return bool(
            re.fullmatch(r"/titles/\{title_id\}/(?:favorite|organize)", path)
            or re.fullmatch(r"/api/titles/\{title_id\}/(?:favorite|tags/\{tag_id\})", path)
            or re.fullmatch(r"/files/\{file_id\}/favorite", path)
            or re.fullmatch(r"/tags/\{tag_id\}/(?:rename|delete)", path)
        )

    def test_sensitive_routes_attach_librarian_dependency(self):
        for path, method in (
            ("/settings", "GET"), ("/sources", "GET"), ("/duplicates", "GET"),
            ("/admin/users", "GET"), ("/scan-all", "POST"),
            ("/titles/{title_id}/metadata/enrich", "POST"),
        ):
            with self.subTest(path=path, method=method):
                self.assertIn(require_librarian, self.dependencies_for(path, method))

    def test_member_self_service_routes_do_not_require_librarian(self):
        for path, method in (
            ("/account/profile", "GET"), ("/account/profile", "POST"),
            ("/exports/library", "GET"),
            ("/titles/{title_id}/favorite", "POST"),
            ("/activity/read", "POST"),
        ):
            with self.subTest(path=path, method=method):
                self.assertNotIn(require_librarian, self.dependencies_for(path, method))

    def test_every_unsafe_route_is_explicitly_member_safe_or_librarian_only(self):
        """A newly added mutating route must make its authorization choice explicit."""
        unsafe_methods = {"POST", "PUT", "PATCH", "DELETE"}
        failures: list[str] = []
        for route in main.app.routes:
            methods = unsafe_methods.intersection(getattr(route, "methods", set()))
            if not methods:
                continue
            path = getattr(route, "path", "")
            dependencies = [item.call for item in route.dependant.dependencies]
            if require_librarian in dependencies or self.member_safe_unsafe_route(path):
                continue
            failures.append(f"{','.join(sorted(methods))} {path}")
        self.assertEqual(
            failures, [],
            "Unsafe routes without an explicit Librarian dependency or reviewed "
            f"Member/public exception: {failures}",
        )


if __name__ == "__main__":
    unittest.main()

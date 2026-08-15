from __future__ import annotations

import unittest
from pathlib import Path

from app import main
from app.access import require_librarian


ROOT = Path(__file__).resolve().parent.parent


class RouteDecompositionTests(unittest.TestCase):
    def test_main_is_composition_root_not_route_monolith(self):
        main_source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertLess(len(main_source.splitlines()), 5000)
        self.assertIn("ROUTER_BUILDERS", main_source)
        self.assertIn("RouteContext(globals())", main_source)
        self.assertNotIn('@app.get("/library"', main_source)
        self.assertNotIn('@librarian_get("/duplicates"', main_source)
        self.assertIn('@app.get("/login"', main_source)

    def test_domain_router_modules_exist(self):
        for name in ("system", "operations", "dashboard", "review", "library", "settings", "collections", "titles"):
            path = ROOT / "app" / "routes" / f"{name}.py"
            self.assertTrue(path.exists(), name)
            source = path.read_text(encoding="utf-8")
            self.assertIn("APIRouter", source)
            self.assertNotIn("from __future__ import annotations", source)

    def test_compatibility_handler_aliases_remain_available(self):
        for name in ("library", "title_detail", "duplicate_review", "collections_page", "sources"):
            self.assertTrue(callable(getattr(main, name, None)), name)

    def test_extracted_protected_routes_keep_librarian_dependency(self):
        targets = {
            ("/duplicates", "GET"),
            ("/sources", "GET"),
            ("/titles/{title_id}/metadata/enrich", "POST"),
        }
        found = set()
        for route in main.app.routes:
            key_candidates = {(getattr(route, "path", ""), method) for method in getattr(route, "methods", set())}
            for key in targets.intersection(key_candidates):
                dependencies = [item.call for item in route.dependant.dependencies]
                self.assertIn(require_librarian, dependencies, key)
                found.add(key)
        self.assertEqual(found, targets)

    def test_live_route_context_tracks_main_service_replacement(self):
        original = main.db
        sentinel = object()
        try:
            main.db = sentinel
            self.assertIs(main._route_context.live("db")._value(), sentinel)
        finally:
            main.db = original


if __name__ == "__main__":
    unittest.main()

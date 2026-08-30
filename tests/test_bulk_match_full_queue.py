from __future__ import annotations

import unittest
from pathlib import Path

from app import main


ROOT = Path(__file__).resolve().parent.parent


class BulkMatchFullQueueTests(unittest.TestCase):
    def test_active_bulk_match_routes_are_registered_before_legacy_review_routes(self):
        for path, method in (("/movies/bulk-match", "GET"), ("/movies/bulk-match", "POST"),
                             ("/shows/bulk-match", "GET"), ("/shows/bulk-match", "POST")):
            matches = [
                route for route in main.app.routes
                if getattr(route, "path", "") == path and method in getattr(route, "methods", set())
            ]
            self.assertGreaterEqual(len(matches), 1, (path, method))
            self.assertEqual(matches[0].endpoint.__module__, "app.routes.bulk_matching")

    def test_active_bulk_match_routes_do_not_cap_review_or_apply_at_fifty(self):
        source = (ROOT / "app" / "routes" / "bulk_matching.py").read_text(encoding="utf-8")
        self.assertNotIn("LIMIT 50", source)
        self.assertNotIn("OFFSET", source)
        self.assertNotIn("matches[:50]", source)
        self.assertIn("for value in matches:", source)

    def test_bulk_match_templates_have_no_review_pagination(self):
        for name in ("bulk_movie_match.html", "bulk_tv_match.html"):
            source = (ROOT / "app" / "templates" / name).read_text(encoding="utf-8")
            self.assertNotIn("Previous 50", source, name)
            self.assertNotIn("Next 50", source, name)
            self.assertNotIn("offset=", source, name)


if __name__ == "__main__":
    unittest.main()

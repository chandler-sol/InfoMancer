from __future__ import annotations

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.http_performance import LibrarySurfacePartialMiddleware


class HttpPerformanceTests(unittest.TestCase):
    def test_versioned_static_assets_are_immutable_in_browser_cache(self):
        client = TestClient(main.app)
        response = client.get("/static/app.css?v=test")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("cache-control"),
            "public, max-age=31536000, immutable",
        )

    def test_unversioned_static_assets_keep_normal_validation_policy(self):
        client = TestClient(main.app)
        response = client.get("/static/infomancer-icon.svg")
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(
            response.headers.get("cache-control"),
            "public, max-age=31536000, immutable",
        )

    def test_navigation_progress_waits_before_showing(self):
        source = (Path(__file__).resolve().parents[1] / "app/static/app-navigation.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("}, 120);", source)
        self.assertIn("showPendingSoon();", source)

    def test_library_surface_switch_parses_only_requested_fragment(self):
        source = (Path(__file__).resolve().parents[1] / "app/static/library-surface-lazy.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("const extractSurface =", source)
        self.assertNotIn("new DOMParser().parseFromString(await response.text(), 'text/html')", source)
        self.assertIn("pointerenter", source)
        self.assertIn("announce: false", source)

    def test_library_partial_middleware_extracts_only_requested_surface(self):
        body = (
            b"<html><main>before"
            b'<section class="cover-library" id="cover-library"><article>A</article></section>'
            b'<section class="panel table-wrap library-table"><table><tbody><tr><td>B</td></tr></tbody></table></section>'
            b"after</main></html>"
        )
        covers = LibrarySurfacePartialMiddleware._extract(body, "covers")
        listing = LibrarySurfacePartialMiddleware._extract(body, "list")
        self.assertEqual(
            covers,
            b'<section class="cover-library" id="cover-library"><article>A</article></section>',
        )
        self.assertEqual(
            listing,
            b'<section class="panel table-wrap library-table"><table><tbody><tr><td>B</td></tr></tbody></table></section>',
        )
        self.assertNotIn(b"<html>", covers)
        self.assertNotIn(b"before", listing)

    def test_task_failure_checks_back_off_when_idle_or_hidden(self):
        source = (Path(__file__).resolve().parents[1] / "app/static/task-widget.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("window.setTimeout(pollFailures, 30000)", source)
        self.assertIn("window.setTimeout(pollFailures, 60000)", source)
        self.assertIn("if (failureRequest) return failureRequest", source)


if __name__ == "__main__":
    unittest.main()

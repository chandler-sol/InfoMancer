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

    def test_large_text_assets_are_compressed_after_cache_policy(self):
        client = TestClient(main.app)
        response = client.get(
            "/static/library.css?v=test",
            headers={"Accept-Encoding": "gzip"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("content-encoding"), "gzip")
        self.assertIn("Accept-Encoding", response.headers.get("vary", ""))
        self.assertEqual(
            response.headers.get("cache-control"),
            "public, max-age=31536000, immutable",
        )

    def test_templates_have_a_real_static_cache_version(self):
        version = main.templates.env.globals.get("static_version", "")
        self.assertTrue(version)
        self.assertNotEqual(version, main.APP_VERSION)

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

    def test_navigation_reserves_scrollbar_space_and_compact_icons_stay_centered(self):
        source = (Path(__file__).resolve().parents[1] / "app/static/app-navigation.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("scrollbar-gutter: stable", source)
        self.assertIn("sidebar-collapsed .brand .workspace-nav-alpha", source)
        self.assertIn("sidebar-collapsed .site-menu-panel a > .menu-count", source)
        self.assertIn("display:none", source)

    def test_system_navigation_sticks_below_topbar_and_uses_eased_offsets(self):
        styles = (Path(__file__).resolve().parents[1] / "app/static/settings-system-nav.css").read_text(
            encoding="utf-8"
        )
        script = (Path(__file__).resolve().parents[1] / "app/static/settings-system-nav.js").read_text(
            encoding="utf-8"
        )
        workspace = (Path(__file__).resolve().parents[1] / "app/static/workspace-ui.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("position: sticky", styles)
        self.assertIn("top: 80px", styles)
        self.assertIn("scroll-margin-top: 158px", styles)
        self.assertIn("#logging .logging-options", styles)
        self.assertIn("input:checked + span", styles)
        self.assertIn("easeInOutCubic", script)
        self.assertIn("chromeOffset()", script)
        self.assertIn("Math.min(850, Math.max(430", script)
        self.assertIn("history.pushState", script)
        self.assertIn("settings-system-nav.css", workspace)
        self.assertIn("settings-system-nav.js", workspace)

    def test_settings_polish_is_part_of_initial_css_not_late_workspace_loading(self):
        settings = (Path(__file__).resolve().parents[1] / "app/static/settings.css").read_text(
            encoding="utf-8"
        )
        workspace = (Path(__file__).resolve().parents[1] / "app/static/workspace-ui.js").read_text(
            encoding="utf-8"
        )
        polish = (Path(__file__).resolve().parents[1] / "app/static/settings-polish.css").read_text(
            encoding="utf-8"
        )
        self.assertTrue(settings.startswith('@import url("settings-polish.css");'))
        self.assertNotIn("settings-polish.css", workspace)
        self.assertNotIn("settings-polish.js", workspace)
        self.assertNotIn("settings-workspace-polished", polish)
        self.assertNotIn("settings-section-general", polish)
        self.assertIn("Cache-proof Settings shell overrides", settings)
        self.assertIn("body .app-settings-heading", settings)

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

    def test_library_selection_actions_share_the_display_toolbar_and_sticky_row(self):
        script = (Path(__file__).resolve().parents[1] / "app/static/library-selection-toolbar.js").read_text(
            encoding="utf-8"
        )
        styles = (Path(__file__).resolve().parents[1] / "app/static/library-selection-toolbar.css").read_text(
            encoding="utf-8"
        )
        workspace = (Path(__file__).resolve().parents[1] / "app/static/workspace-ui.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("toolbar.insertBefore(actions, viewToolbar)", script)
        self.assertIn("has-selection-actions", script)
        self.assertIn("grid-template-columns: minmax(120px, auto) minmax(0, 1fr) minmax(260px, auto)", styles)
        self.assertIn("position: sticky", styles)
        self.assertIn("library-selection-toolbar.css", workspace)
        self.assertIn("library-selection-toolbar.js", workspace)

    def test_canonical_action_menu_glyph_is_absolutely_centered(self):
        styles = (Path(__file__).resolve().parents[1] / "app/static/action-menu.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("position: relative", styles)
        self.assertIn("top: 50%", styles)
        self.assertIn("left: 50%", styles)
        self.assertIn("transform: translate(-50%, -50%)", styles)

    def test_task_failure_checks_back_off_when_idle_or_hidden(self):
        source = (Path(__file__).resolve().parents[1] / "app/static/task-widget.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("document.hidden ? 60000 : active.length ? 5000 : open ? 15000 : 30000", source)
        self.assertIn("failureTimer = window.setTimeout", source)
        self.assertIn("if (failureRequest) return failureRequest", source)

    def test_title_detail_assets_are_not_loaded_on_every_page(self):
        source = (Path(__file__).resolve().parents[1] / "app/static/workspace.js").read_text(
            encoding="utf-8"
        )
        guard = 'if (document.querySelector(".media-dossier"))'
        self.assertIn(guard, source)
        self.assertGreater(source.index('loadScript("title-detail-ux.js"'), source.index(guard))
        self.assertGreater(source.index('ensureStyle("title-detail-ux.css"'), source.index(guard))

    def test_active_title_metadata_polling_uses_worker_state_before_sqlite(self):
        source = (Path(__file__).resolve().parents[1] / "app/routes/title_metadata_async.py").read_text(
            encoding="utf-8"
        )
        active_branch = source.index("if task_is_this_title:")
        durable_read = source.index("with db.connect() as conn:", active_branch)
        self.assertLess(active_branch, durable_read)
        self.assertIn('task["status"] in {"starting", "running"}', source)
        self.assertIn("LEFT JOIN metadata_refresh_queue", source)


if __name__ == "__main__":
    unittest.main()

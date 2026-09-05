from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class WorkspaceFoundationTests(unittest.TestCase):
    def test_08_alpha_version_and_workspace_assets_are_enabled(self):
        main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn('APP_VERSION = "0.8.1-beta.1"', main)
        self.assertIn("path='workspace.css'", base)
        self.assertIn("path='workspace.js'", base)
        self.assertIn("path='workspace-ui.js'", base)

    def test_workspace_navigation_is_server_rendered_and_collapsible(self):
        base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        loader = (ROOT / "app" / "static" / "workspace.js").read_text(encoding="utf-8")
        core = (ROOT / "app" / "static" / "workspace-core.js").read_text(encoding="utf-8")
        styles = (ROOT / "app" / "static" / "workspace.css").read_text(encoding="utf-8")
        self.assertIn('site-menu-panel workspace-nav-ready', base)
        self.assertIn('data-workspace-nav', base)
        for label in ("Dashboard", "Library", "Review", "Sources", "Activity"):
            self.assertIn(f"<span>{label}</span>", base)
        for href in ("/movies", "/shows", "/collections", "/favorites", "/review", "/duplicates", "/bulk-match"):
            self.assertIn(f'href="{href}"', base)
        for section in ("library", "review", "more"):
            self.assertIn(f'data-workspace-section="{section}"', base)
        self.assertIn('loadScript("workspace-core.js"', loader)
        self.assertIn("enhanceWorkspaceNavigation", core)
        self.assertNotIn("cloneLink", core)
        self.assertNotIn("replaceChildren(primary)", core)
        self.assertIn("sidebar-collapsed .workspace-nav-section", styles)
        self.assertIn("0.8 α", base)
        self.assertIn('class="domain-current"', base)
        self.assertIn("request.url.path == '/library'", base)
        self.assertIn("sidebar-collapsed .workspace-nav-primary > a.domain-current", styles)
        self.assertIn('<summary aria-label="Browse shortcuts">Browse</summary>', base)
        self.assertIn("<span>Custom Libraries</span>", base)
        self.assertIn('content: "‹"', styles)
        self.assertIn("font-size: 10.5px", styles)

    def test_workspace_removes_home_layout_switcher_from_shell(self):
        base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertNotIn('class="home-layout-toggle"', base)
        self.assertNotIn('action="/account/home-layout"', base)

    def test_library_inspector_preserves_full_detail_navigation(self):
        core = (ROOT / "app" / "static" / "workspace-core.js").read_text(encoding="utf-8")
        partial = (ROOT / "app" / "templates" / "_workspace_inspector.html").read_text(encoding="utf-8")
        self.assertIn("workspace-inspector", core)
        self.assertIn("Open full details", partial)
        self.assertIn("dblclick", core)
        self.assertIn('event.key === "Escape"', core)
        self.assertIn('event.key === "Enter"', core)

    def test_w2_inspector_is_server_backed_and_history_aware(self):
        core = (ROOT / "app" / "static" / "workspace-core.js").read_text(encoding="utf-8")
        styles = (ROOT / "app" / "static" / "workspace.css").read_text(encoding="utf-8")
        library = (ROOT / "app" / "templates" / "library.html").read_text(encoding="utf-8")
        controller = (ROOT / "app" / "static" / "library-controller.js").read_text(encoding="utf-8")
        partial = (ROOT / "app" / "templates" / "_workspace_inspector.html").read_text(encoding="utf-8")
        routes = (ROOT / "app" / "routes" / "library.py").read_text(encoding="utf-8")
        self.assertIn('/library/inspector/{title_id}', routes)
        self.assertIn('/api/titles/{title_id}/favorite', routes)
        self.assertIn('/api/titles/{title_id}/tags/{tag_id}', routes)
        self.assertIn('fetch(`/library/inspector/', core)
        self.assertIn('workspaceInspectorTitleId', core)
        self.assertIn('popstate', core)
        self.assertIn('event.shiftKey', core)
        self.assertIn('event.metaKey || event.ctrlKey', core)
        self.assertIn('selectedTitleId ? "replace" : "push"', core)
        self.assertIn('infomancer-library-selection:', controller)
        self.assertIn('infomancer:library-selection-updated', controller)
        self.assertIn('data-workspace-title-id', library)
        self.assertIn('Health &amp; attention', partial)
        self.assertIn('Edition &amp; Version', partial)
        self.assertIn('data-workspace-tag', partial)
        self.assertIn('server-backed Inspector', styles)

    def test_detail_workspace_adds_local_people_previews(self):
        core = (ROOT / "app" / "static" / "workspace-core.js").read_text(encoding="utf-8")
        styles = (ROOT / "app" / "static" / "workspace.css").read_text(encoding="utf-8")
        self.assertIn("enhanceCreditHoverCards", core)
        self.assertIn("workspace-person-popover", core)
        self.assertIn("Search library for this person", core)
        self.assertIn("workspace-person-popover", styles)
        self.assertIn("media-dossier .detail-page-head", styles)


if __name__ == "__main__":
    unittest.main()

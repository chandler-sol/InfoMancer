from __future__ import annotations

import unittest
from pathlib import Path


class Release081UiPolishContracts(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]

    def read(self, path: str) -> str:
        return (self.root / path).read_text(encoding="utf-8")

    def test_navigation_paint_guard_waits_for_critical_styles_without_blank_shell(self):
        bootstrap = self.read("app/static/app-shell-bootstrap.js")
        self.assertIn("shell-preparing", bootstrap)
        self.assertIn("navigation-paint-stability.css", bootstrap)
        self.assertIn("body.shell-preparing .library-table", bootstrap)
        self.assertIn("body.shell-preparing #cover-library", bootstrap)
        self.assertIn("visibility: visible !important", bootstrap)
        self.assertIn("body.shell-preparing .library-view-toolbar", bootstrap)
        self.assertNotIn("body.shell-preparing > footer { visibility: hidden", bootstrap)
        self.assertIn("release-081-ui-polish.css", bootstrap)
        self.assertIn("action-menu.css", bootstrap)
        self.assertIn("library-controls.css", bootstrap)
        self.assertIn("pageControllersReady", bootstrap)
        self.assertIn("Promise.all([domReady, pageControllersReady, ...criticalStyles])", bootstrap)
        self.assertIn("release-081-ui-polish.js", bootstrap)

    def test_full_page_navigation_keeps_old_workspace_painted_and_avoids_content_morph(self):
        script = self.read("app/static/app-navigation.js")
        styles = self.read("app/static/app-navigation.css")
        modern = self.read("app/static/modern.css")
        stable = self.read("app/static/navigation-paint-stability.css")
        self.assertIn("coverOutgoingPage", script)
        self.assertIn("app-navigation-leaving", script)
        self.assertNotIn("html.app-navigation-leaving::before", styles)
        self.assertIn("visibility:hidden !important", styles)
        self.assertIn("html.app-navigation-leaving body.has-app-sidebar main.shell", stable)
        self.assertIn("visibility: visible !important", stable)
        self.assertIn("@view-transition", modern)
        self.assertIn("view-transition-name: infomancer-chrome", modern)
        self.assertIn("view-transition-name: infomancer-content", modern)
        self.assertIn("main.shell,", stable)
        self.assertIn("view-transition-name: none !important", stable)
        self.assertIn("::view-transition-old(root)", stable)
        self.assertIn("::view-transition-new(root)", stable)
        self.assertIn("infomancer-root-reveal", stable)

    def test_library_navigation_waits_only_for_moving_controls(self):
        bootstrap = self.read("app/static/app-shell-bootstrap.js")
        stable = self.read("app/static/navigation-paint-stability.css")
        self.assertIn("const librarySurface = ['/library', '/movies', '/shows'].includes(path)", bootstrap)
        self.assertIn("library-surface-route", bootstrap)
        self.assertIn("view-transition-name: none !important", bootstrap)
        self.assertIn("const libraryLayoutReady", bootstrap)
        self.assertIn("library-density-ready", bootstrap)
        self.assertIn("toolbar.parentElement === tabs", bootstrap)
        self.assertIn("MutationObserver", bootstrap)
        self.assertIn("window.setTimeout(finish, 1500)", bootstrap)
        self.assertIn("Promise.all([shellCriticalReady, libraryLayoutReady])", bootstrap)
        self.assertIn("body.shell-preparing .library-view-toolbar", bootstrap)
        self.assertIn("body.shell-preparing #cover-size-control", bootstrap)
        self.assertIn("#cover-library", stable)
        self.assertIn("animation: none !important", stable)

    def test_disabled_controls_are_not_reported_as_busy(self):
        css = self.read("app/static/release-081-ui-polish.css")
        self.assertIn("button:disabled", css)
        self.assertIn("cursor: not-allowed", css)
        self.assertIn('button[aria-busy="true"]', css)
        self.assertIn("cursor: progress", css)

    def test_collection_creation_user_creation_and_cover_add_are_modalized(self):
        script = self.read("app/static/release-081-ui-polish.js")
        self.assertIn("+ Create Collection", script)
        self.assertIn("Manual", script)
        self.assertIn("Smart", script)
        self.assertIn("+ Add user", script)
        self.assertIn("create-user-form", script)
        self.assertIn("collection-detail-art-action", script)

    def test_collection_picker_menu_and_editor_have_release_owned_contracts(self):
        css = self.read("app/static/release-081-ui-polish.css")
        script = self.read("app/static/release-081-ui-polish.js")
        template = self.read("app/templates/collections.html")
        self.assertIn(".collection-picker-menu > summary", css)
        self.assertIn(".collection-picker-menu > summary::before", css)
        self.assertIn("collection-picker-card-actions", css)
        self.assertIn("collection-picker-editor-form", css)
        self.assertIn("openCollectionDetailsEditor", script)
        self.assertIn("data-collection-edit", template)
        self.assertIn("data-has-custom-artwork", template)
        self.assertIn("Edit Smart Collection", template)

    def test_bulk_add_to_collection_uses_modal_instead_of_page_form_submit(self):
        script = self.read("app/static/release-081-library-actions.js")
        self.assertIn("release-bulk-collection-dialog", script)
        self.assertIn("openCollectionDialog", script)
        self.assertIn("fetch('/titles/collections-bulk'", script)
        self.assertNotIn("form.submit();", script)

    def test_future_collection_inspector_and_episode_ordering_are_recorded(self):
        roadmap = self.read("docs/ROADMAP_1_0_PLUS.md")
        self.assertIn("Collection Inspector", roadmap)
        self.assertIn("advanced Smart Collection rule builder", roadmap)
        self.assertIn("Alternate and custom episode ordering", roadmap)
        self.assertIn("drag-and-drop ordering", roadmap)


if __name__ == "__main__":
    unittest.main()

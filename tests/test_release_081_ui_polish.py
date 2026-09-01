from __future__ import annotations

import unittest
from pathlib import Path


class Release081UiPolishContracts(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]

    def read(self, path: str) -> str:
        return (self.root / path).read_text(encoding="utf-8")

    def test_navigation_paint_guard_waits_for_critical_styles(self):
        bootstrap = self.read("app/static/app-shell-bootstrap.js")
        self.assertIn("shell-preparing", bootstrap)
        self.assertIn("release-081-ui-polish.css", bootstrap)
        self.assertIn("action-menu.css", bootstrap)
        self.assertIn("library-controls.css", bootstrap)
        self.assertIn("Promise.all([domReady, ...criticalStyles])", bootstrap)
        self.assertIn("release-081-ui-polish.js", bootstrap)

    def test_disabled_controls_are_not_reported_as_busy(self):
        css = self.read("app/static/release-081-ui-polish.css")
        self.assertIn("button:disabled", css)
        self.assertIn("cursor: not-allowed", css)
        self.assertIn('button[aria-busy="true"]', css)
        self.assertIn("cursor: progress", css)

    def test_collection_creation_and_user_creation_are_modalized(self):
        script = self.read("app/static/release-081-ui-polish.js")
        self.assertIn("+ Create Collection", script)
        self.assertIn("Manual", script)
        self.assertIn("Smart", script)
        self.assertIn("+ Add user", script)
        self.assertIn("create-user-form", script)
        self.assertIn("collection-detail-art-action", script)

    def test_collection_picker_menu_has_release_owned_trigger_geometry(self):
        css = self.read("app/static/release-081-ui-polish.css")
        self.assertIn(".collection-picker-menu > summary", css)
        self.assertIn(".collection-picker-menu > summary::before", css)
        self.assertIn("collection-picker-card-actions", css)

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

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
TEMPLATES = ROOT / "app" / "templates"


class UiPolishRegressionTests(unittest.TestCase):
    def test_saved_views_dismisses_on_outside_click_and_escape(self):
        source = (STATIC / "library-saved-views-polish.js").read_text(encoding="utf-8")
        self.assertIn("document.addEventListener('pointerdown'", source)
        self.assertIn("!manager.contains(event.target)", source)
        self.assertIn("event.key !== 'Escape'", source)
        self.assertIn("summary?.focus()", source)

    def test_title_media_facts_stay_in_one_scrollable_rail(self):
        source = (STATIC / "detail-page-polish.css").read_text(encoding="utf-8")
        self.assertIn("detail-technical-rail dl", source)
        self.assertIn("display: flex !important", source)
        self.assertIn("overflow-x: auto !important", source)
        self.assertIn("flex: 1 0 130px", source)

    def test_title_and_inspector_artwork_fill_their_summary_tracks(self):
        source = (STATIC / "detail-page-polish.css").read_text(encoding="utf-8")
        self.assertIn("detail-page-head .detail-poster-column", source)
        self.assertIn("align-self: stretch", source)
        self.assertIn(".workspace-inspector-summary", source)
        self.assertIn("width: 120px", source)

    def test_title_workflow_dialog_has_real_gutters_and_centered_close_control(self):
        source = (STATIC / "detail-page-polish.css").read_text(encoding="utf-8")
        self.assertIn("padding: 30px 32px 34px !important", source)
        self.assertIn(".organize-dialog.title-workflow-dialog .organize-dialog-close", source)
        self.assertIn("place-items: center", source)
        self.assertIn("width: 44px", source)
        self.assertIn("height: 44px", source)

    def test_sidebar_has_dedicated_top_left_toggle_with_direction_change(self):
        source = (STATIC / "app-navigation.css").read_text(encoding="utf-8")
        self.assertIn("top: 34px", source)
        self.assertIn("left: 12px", source)
        self.assertIn("rotate(180deg)", source)
        self.assertIn("border-radius: 8px", source)

    def test_review_overflow_button_uses_canonical_centered_menu_control(self):
        action_menu = (STATIC / "action-menu.css").read_text(encoding="utf-8")
        review = (TEMPLATES / "review.html").read_text(encoding="utf-8")
        self.assertIn(".workspace-context-toggle::before", action_menu)
        self.assertIn("top: 50%", action_menu)
        self.assertIn("left: 50%", action_menu)
        self.assertIn("translate(-50%, -50%)", action_menu)
        self.assertIn('class="workspace-context-toggle"', review)


if __name__ == "__main__":
    unittest.main()

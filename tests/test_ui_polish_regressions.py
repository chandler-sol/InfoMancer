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

    def test_title_media_facts_are_scroll_free_quality_cards(self):
        source = (STATIC / "detail-page-polish.css").read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: repeat(6, minmax(0, 1fr))", source)
        self.assertIn("overflow: visible !important", source)
        self.assertNotIn("overflow-x: auto !important", source)

    def test_title_source_is_single_clickable_library_filter(self):
        route = (ROOT / "app/routes/title_media_info.py").read_text(encoding="utf-8")
        script = (STATIC / "detail-page-polish.js").read_text(encoding="utf-8")
        styles = (STATIC / "detail-page-polish.css").read_text(encoding="utf-8")
        self.assertIn('"source_href": f"/library?root=', route)
        self.assertIn("value.href = sourceHrefState", script)
        self.assertIn('.dossier-on-disk .file-source").forEach((node) => node.remove())', script)
        self.assertIn(".dossier-on-disk .file-source {\n  display: none !important;", styles)

    def test_title_and_inspector_artwork_fill_their_summary_tracks(self):
        source = (STATIC / "detail-page-polish.css").read_text(encoding="utf-8")
        self.assertIn("detail-page-head .detail-poster-column", source)
        self.assertIn("align-self: stretch", source)
        self.assertIn(".workspace-inspector-summary", source)
        self.assertIn("width: 120px", source)

    def test_title_workflows_cannot_retain_horizontal_scroll(self):
        script = (STATIC / "detail-page-polish.js").read_text(encoding="utf-8")
        styles = (STATIC / "detail-page-polish.css").read_text(encoding="utf-8")
        self.assertIn("body.scrollLeft = 0", script)
        self.assertIn("overflow-x: hidden !important", styles)
        self.assertIn("max-width: 100% !important", styles)
        self.assertIn("margin-left: 0 !important", styles)
        self.assertIn(".organize-dialog.title-workflow-dialog .organize-dialog-close", styles)
        self.assertIn("width: 44px", styles)
        self.assertIn("height: 44px", styles)

    def test_library_inspector_is_opaque(self):
        source = (STATIC / "library-selection-polish.css").read_text(encoding="utf-8")
        self.assertIn(".workspace-inspector {\n  background: #0d1218;", source)
        self.assertIn(".library-inspector-selection-bar {", source)
        self.assertIn("-webkit-backdrop-filter: none", source)
        self.assertIn("backdrop-filter: none", source)

    def test_library_bulk_bar_starts_at_two_and_stays_single_line_on_desktop(self):
        script = (STATIC / "library-selection-toolbar.js").read_text(encoding="utf-8")
        styles = (STATIC / "library-selection-toolbar.css").read_text(encoding="utf-8")
        self.assertIn("actions.hidden = count < 2", script)
        self.assertIn("library-multi-selection", styles)
        self.assertIn("flex-wrap: nowrap", styles)
        self.assertIn("white-space: nowrap", styles)

    def test_library_multi_select_supports_bulk_favorite_and_modal_organize(self):
        toolbar = (STATIC / "library-selection-toolbar.js").read_text(encoding="utf-8")
        dialog = (STATIC / "organize-dialog.js").read_text(encoding="utf-8")
        template = (TEMPLATES / "organize_bulk.html").read_text(encoding="utf-8")
        route = (ROOT / "app/routes/title_bulk_actions.py").read_text(encoding="utf-8")
        self.assertIn("/titles/favorite-bulk", toolbar)
        self.assertIn("Add to Favorites", toolbar)
        self.assertIn("url: '/titles/organize-bulk'", toolbar)
        self.assertIn("method: 'POST'", toolbar)
        self.assertIn("organize-bulk", dialog)
        self.assertIn("event.detail.method", dialog)
        self.assertIn("data-organize-content", template)
        self.assertIn("data-organize-bulk", template)
        self.assertIn('@router.post("/titles/favorite-bulk")', route)
        self.assertIn("favorite=1", route)

    def test_sidebar_control_geometry_is_known_before_header_paint(self):
        base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        progress = (STATIC / "progress.css").read_text(encoding="utf-8")
        navigation = (STATIC / "app-navigation.css").read_text(encoding="utf-8")
        self.assertLess(base.index("progress.css"), base.index("header.css"))
        self.assertIn("width: 28px !important", progress)
        self.assertIn("height: 28px !important", progress)
        self.assertIn("body.has-app-sidebar {\n    transition: none !important;", progress)
        self.assertIn("rotate(180deg) !important", progress)
        self.assertNotIn("width: 34px;", navigation)
        self.assertNotIn("left: 12px;", navigation)

    def test_profile_page_keeps_sidebar_avatar_visible_before_preview_js(self):
        source = (STATIC / "profile.css").read_text(encoding="utf-8")
        self.assertNotIn("background-image:url('/account/avatar/current')", source)
        self.assertIn(".account-avatar[style*=\"background-image\"]", source)
        self.assertIn("background-size:cover", source)

    def test_profile_preview_renders_initials_as_text_in_account_rail(self):
        source = (STATIC / "profile.js").read_text(encoding="utf-8")
        self.assertIn('accountAvatar.style.removeProperty("background-image")', source)
        self.assertIn('selectedIcon === "initials"\n      ? initialFor()', source)
        self.assertIn("sidebarSymbols[selectedIcon] || initialFor()", source)
        self.assertIn('accountAvatar.dataset.profileAvatarKind = "image"', source)

    def test_account_rail_uses_canonical_avatar_endpoint_as_real_image(self):
        source = (STATIC / "workspace-ui.js").read_text(encoding="utf-8")
        self.assertIn('document.querySelector(".account-avatar")', source)
        self.assertIn('avatarImage.src = `/account/avatar/current?v=${Date.now()}`', source)
        self.assertIn("accountAvatar.replaceChildren(avatarImage)", source)
        self.assertIn('accountAvatar.dataset.profileAvatarPreview === "1"', source)
        self.assertIn('avatarImage.style.objectFit = "cover"', source)

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

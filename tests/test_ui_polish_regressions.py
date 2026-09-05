from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
TEMPLATES = ROOT / "app" / "templates"


class UiPolishRegressionTests(unittest.TestCase):
    def test_saved_views_dismisses_on_outside_click_and_escape(self):
        source = (STATIC / "library-saved-views.js").read_text(encoding="utf-8")
        self.assertIn("document.addEventListener('pointerdown'", source)
        self.assertIn("!manager.contains(event.target)", source)
        self.assertIn("event.key !== 'Escape'", source)
        self.assertIn("summary?.focus()", source)

    def test_letter_jump_ignores_stale_responses_and_parses_only_surface(self):
        source = (STATIC / "library-letter-jump.js").read_text(encoding="utf-8")
        self.assertIn("let jumpSerial = 0", source)
        self.assertIn("if (serial !== jumpSerial) return", source)
        self.assertIn("const extractSurface = (html, view)", source)
        self.assertIn("template.innerHTML = html.slice", source)
        self.assertNotIn("new DOMParser().parseFromString", source)
        self.assertIn("infomancer:before-navigate", source)
        self.assertIn("aria-busy", source)

    def test_title_media_facts_are_scroll_free_quality_cards(self):
        source = (STATIC / "detail-page.css").read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: repeat(6, minmax(0, 1fr))", source)
        self.assertIn("overflow: visible !important", source)
        self.assertNotIn("overflow-x: auto !important", source)

    def test_title_source_is_single_clickable_library_filter(self):
        route = (ROOT / "app/routes/title_media_info.py").read_text(encoding="utf-8")
        script = (STATIC / "detail-page.js").read_text(encoding="utf-8")
        styles = (STATIC / "detail-page.css").read_text(encoding="utf-8")
        self.assertIn('"source_href": f"/library?root=', route)
        self.assertIn("value.href = sourceHrefState", script)
        self.assertIn('.dossier-on-disk .file-source").forEach((node) => node.remove())', script)
        self.assertIn(".dossier-on-disk .file-source {\n  display: none !important;", styles)

    def test_title_and_inspector_artwork_fill_their_summary_tracks(self):
        source = (STATIC / "detail-page.css").read_text(encoding="utf-8")
        self.assertIn("detail-page-head .detail-poster-column", source)
        self.assertIn("align-self: stretch", source)
        self.assertIn(".workspace-inspector-summary", source)
        self.assertIn("width: 120px", source)

    def test_title_workflows_cannot_retain_horizontal_scroll(self):
        script = (STATIC / "detail-page.js").read_text(encoding="utf-8")
        styles = (STATIC / "detail-page.css").read_text(encoding="utf-8")
        self.assertIn("body.scrollLeft = 0", script)
        self.assertIn("overflow-x: hidden !important", styles)
        self.assertIn("max-width: 100% !important", styles)
        self.assertIn("margin-left: 0 !important", styles)
        self.assertIn(".organize-dialog.title-workflow-dialog .organize-dialog-close", styles)
        self.assertIn("width: 44px", styles)
        self.assertIn("height: 44px", styles)

    def test_shared_dialog_shell_has_one_stable_scroll_axis(self):
        critical = (STATIC / "progress.css").read_text(encoding="utf-8")
        self.assertIn("width: min(1100px, calc(100vw - 40px)) !important", critical)
        self.assertIn("overflow-x: hidden !important", critical)
        self.assertIn("scrollbar-gutter: stable", critical)
        self.assertIn(".organize-dialog-close::before", critical)
        self.assertIn("font-size: 0 !important", critical)
        self.assertIn("backdrop-filter: none !important", critical)

    def test_shared_dialog_cancels_stale_fetches_and_double_submits(self):
        source = (STATIC / "organize-dialog.js").read_text(encoding="utf-8")
        self.assertIn("new AbortController()", source)
        self.assertIn("signal: request.controller.signal", source)
        self.assertIn('error?.name === "AbortError"', source)
        self.assertIn('form.dataset.submitting === "1"', source)
        self.assertIn("requestSerial", source)
        self.assertIn("heading?.focus({preventScroll: true})", source)

    def test_workspace_actions_are_single_flight_and_navigation_safe(self):
        source = (STATIC / "workspace-ui-core.js").read_text(encoding="utf-8")
        self.assertIn('form.dataset.workspaceSubmitting === "1"', source)
        self.assertIn("activeActionControllers", source)
        self.assertIn("signal: controller.signal", source)
        self.assertIn('error?.name !== "AbortError"', source)
        self.assertIn("formDataFor(form, submitter)", source)
        self.assertIn("infomancer:before-navigate", source)
        self.assertIn("resetTransientState", source)

    def test_workspace_drawer_and_confirm_restore_focus(self):
        source = (STATIC / "workspace-ui-core.js").read_text(encoding="utf-8")
        self.assertIn('aria-labelledby", "workspace-confirm-title', source)
        self.assertIn("data-workspace-confirm-cancel", source)
        self.assertIn("opener?.isConnected", source)
        self.assertIn('class="workspace-drawer-panel" tabindex="-1"', source)
        self.assertIn('body.setAttribute("aria-busy", "true")', source)
        self.assertIn('trigger.setAttribute("aria-expanded", "true")', source)
        self.assertIn("restoreFocus", source)

    def test_library_inspector_is_opaque(self):
        source = (STATIC / "library-selection.css").read_text(encoding="utf-8")
        self.assertIn(".workspace-inspector {\n  background: #0d1218;", source)
        self.assertIn(".library-inspector-selection-bar {", source)
        self.assertIn("-webkit-backdrop-filter: none", source)
        self.assertIn("backdrop-filter: none", source)

    def test_library_selection_does_not_force_inspector_open(self):
        source = (STATIC / "library-selection-polish.js").read_text(encoding="utf-8")
        self.assertNotIn("inspectTitle(entries[0].id, {explicit: false})", source)
        self.assertIn("dismissInspectorForBulkSelection", source)
        self.assertIn("selectedEntries().length > 1", source)
        self.assertIn("document.addEventListener('infomancer:library-compare-selected'", source)
        self.assertIn("bar.append(meta, chooser)", source)
        self.assertNotIn("bar.append(meta, chooser, compare)", source)

    def test_library_bulk_bar_starts_at_two_and_stays_single_line_on_desktop(self):
        script = (STATIC / "library-selection-toolbar.js").read_text(encoding="utf-8")
        styles = (STATIC / "library-selection.css").read_text(encoding="utf-8")
        self.assertIn("const shouldHide = count < 2", script)
        self.assertIn("if (actions.hidden !== shouldHide) actions.hidden = shouldHide", script)
        self.assertNotIn("new MutationObserver(sync).observe(actions", script)
        self.assertIn("selectionCountLabel.textContent = `${count} selected`", script)
        self.assertIn("library-multi-selection", styles)
        self.assertIn("flex-wrap: nowrap", styles)
        self.assertIn("white-space: nowrap", styles)
        self.assertIn("library-bulk-separator", styles)
        self.assertIn("backdrop-filter: none", styles)

    def test_library_bulk_bar_exposes_favorite_compare_and_grouped_match(self):
        toolbar = (STATIC / "library-selection-toolbar.js").read_text(encoding="utf-8")
        styles = (STATIC / "library-selection.css").read_text(encoding="utf-8")
        self.assertIn("workspace-inspector-favorite library-bulk-favorite", toolbar)
        self.assertIn("fetch('/titles/favorite-bulk'", toolbar)
        self.assertIn("library-bulk-compare", toolbar)
        self.assertIn("infomancer:library-compare-selected", toolbar)
        self.assertIn("matchSummary.textContent = 'Match'", toolbar)
        self.assertIn("Movies (${movies.length})", toolbar)
        self.assertIn("TV Shows (${shows.length})", toolbar)
        self.assertIn("Sort Titles", toolbar)
        self.assertIn("Refresh Metadata", toolbar)
        self.assertIn(".library-bulk-favorite", styles)

    def test_library_cover_grid_fills_both_page_edges_and_captions_are_inset(self):
        grid = (STATIC / "library-performance.css").read_text(encoding="utf-8")
        styles = (STATIC / "library-selection.css").read_text(encoding="utf-8")
        self.assertIn("#cover-library.cover-library {", grid)
        self.assertIn("repeat(auto-fill, minmax(min(100%, var(--cover-size)), var(--cover-size)))", grid)
        self.assertIn("justify-content: space-between", grid)
        self.assertIn("#cover-library.cover-library > .cover-card", grid)
        self.assertIn("width: min(var(--cover-size), 100%)", grid)
        self.assertNotIn("justify-content: flex-start !important", styles)
        self.assertIn("#cover-library .cover-card-link > strong", styles)
        self.assertIn("padding: 8px 8px 0", styles)
        self.assertIn("#cover-library .cover-card-meta", styles)
        self.assertIn("padding: 3px 8px 8px", styles)

    def test_library_bulk_organize_is_modal_and_retains_bulk_favorites(self):
        toolbar = (STATIC / "library-selection-toolbar.js").read_text(encoding="utf-8")
        dialog = (STATIC / "organize-dialog.js").read_text(encoding="utf-8")
        template = (TEMPLATES / "organize_bulk.html").read_text(encoding="utf-8")
        route = (ROOT / "app/routes/title_bulk_actions.py").read_text(encoding="utf-8")
        self.assertIn("url: '/titles/organize-bulk'", toolbar)
        self.assertIn("method: 'POST'", toolbar)
        self.assertIn("organize-bulk", dialog)
        self.assertIn("event.detail.method", dialog)
        self.assertIn("data-organize-content", template)
        self.assertIn("data-organize-bulk", template)
        self.assertIn("data-bulk-favorite-selected", template)
        self.assertIn('fetch("/titles/favorite-bulk"', dialog)
        self.assertIn('@router.post("/titles/favorite-bulk")', route)
        self.assertIn("favorite=1", route)

    def test_sort_titles_keeps_grid_slot_when_poster_is_missing(self):
        template = (TEMPLATES / "sort_titles_dialog.html").read_text(encoding="utf-8")
        styles = (STATIC / "library-selection.css").read_text(encoding="utf-8")
        self.assertIn("sort-title-poster-placeholder", template)
        self.assertIn("title.display_title[:1]", template)
        self.assertIn(".sort-title-poster-placeholder", styles)
        self.assertIn("minmax(180px, 1fr)", styles)

    def test_runtime_controllers_wait_for_layout_styles(self):
        workspace_ui = (STATIC / "workspace-ui.js").read_text(encoding="utf-8")
        workspace = (STATIC / "workspace.js").read_text(encoding="utf-8")
        self.assertIn("link.fetchPriority = 'high'", workspace_ui)
        self.assertIn("libraryStyles.then", workspace_ui)
        self.assertIn("await loadScript('library-controller.js')", workspace_ui)
        self.assertIn("].map((path) => loadScript(path))", workspace_ui)
        self.assertIn("'library-density.js'", workspace_ui)
        self.assertIn("'library-selection-polish.js'", workspace_ui)
        self.assertIn("globalStyles.then(() => requestAnimationFrame", workspace_ui)
        self.assertIn("Promise.all([coreReady, styleReady, castStyleReady])", workspace)
        self.assertIn('link.fetchPriority = "high"', workspace)
        self.assertIn("absoluteAssetUrl", workspace)
        self.assertIn("link.href === absolute", workspace)

    def test_workspace_polish_is_consolidated_without_css_import_waterfall(self):
        review = (STATIC / "review.css").read_text(encoding="utf-8")
        self.assertFalse((STATIC / "workspace-detail-polish.css").exists())
        self.assertNotIn("@import url(", review)
        self.assertIn("Consolidated 0.8 workspace/detail polish", review)
        self.assertIn("body.has-app-sidebar main.shell:has(> .catalog-tabs)", review)
        self.assertIn("workspace-inspector-season", review)
        self.assertIn("@media (max-width: 520px)", review)

    def test_navigation_lifecycle_closes_transient_state(self):
        source = (STATIC / "app-navigation.js").read_text(encoding="utf-8")
        self.assertIn("const announceNavigation", source)
        self.assertIn("infomancer:before-navigate", source)
        self.assertIn("const beginNavigation", source)
        self.assertIn("document.addEventListener('submit'", source)
        self.assertIn('navigator.connection?.effectiveType', source)
        self.assertIn("window.addEventListener('pageshow', clearPending)", source)

    def test_action_menus_are_viewport_bounded_and_touch_accessible(self):
        source = (STATIC / "action-menu.css").read_text(encoding="utf-8")
        self.assertIn("max-height: min(70dvh, 520px)", source)
        self.assertIn("overscroll-behavior: contain", source)
        self.assertIn("scrollbar-gutter: stable", source)
        self.assertIn("@media (pointer: coarse)", source)
        self.assertIn("min-height: 44px", source)
        self.assertIn("@media (prefers-reduced-motion: reduce)", source)

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
        self.assertIn("document.querySelector('.account-avatar')", source)
        self.assertIn("avatarImage.src = '/account/avatar/current'", source)
        self.assertNotIn("avatarImage.src = `/account/avatar/current?v=${Date.now()}`", source)
        self.assertIn("accountAvatar.replaceChildren(avatarImage)", source)
        self.assertIn("accountAvatar.dataset.profileAvatarPreview === '1'", source)
        self.assertIn("avatarImage.style.objectFit = 'cover'", source)

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

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ResponsiveSweepTests(unittest.TestCase):
    def test_mobile_application_chrome_stays_inside_dynamic_viewport(self):
        css = (ROOT / "app/static/final-mobile-polish.css").read_text(encoding="utf-8")

        self.assertIn("100dvh", css)
        self.assertIn("env(safe-area-inset-top)", css)
        self.assertIn("env(safe-area-inset-bottom)", css)
        for selector in (
            ".global-search-suggestions",
            ".task-popover",
            ".source-browser",
            ".collection-management-dialog",
            ".collection-floating-menu",
            ".workspace-confirm-dialog",
            ".workspace-command-palette",
            ".announcement-popup",
            ".setup-choice-card",
            ".workspace-drawer-panel",
        ):
            self.assertIn(selector, css)
        self.assertIn("overscroll-behavior: contain", css)

    def test_mobile_header_popovers_have_scrollable_height_budget(self):
        css = (ROOT / "app/static/final-mobile-polish.css").read_text(encoding="utf-8")

        for selector in (
            ".topbar .global-search-suggestions",
            ".topbar .task-popover",
            ".topbar .site-menu-panel",
            ".topbar .account-menu-popover",
        ):
            self.assertIn(selector, css)
        self.assertIn(
            "max-height: calc(100dvh - 84px - env(safe-area-inset-bottom))",
            css,
        )
        self.assertIn("-webkit-overflow-scrolling: touch", css)

    def test_active_settings_tab_is_revealed_after_viewport_changes(self):
        script = (ROOT / "app/static/final-mobile-polish.js").read_text(encoding="utf-8")

        self.assertIn("revealActiveSettingsTab", script)
        self.assertIn(".settings-section-nav", script)
        self.assertIn('[aria-current="page"]', script)
        self.assertIn("nav.scrollLeft", script)
        self.assertIn("window.visualViewport?.addEventListener('resize'", script)

    def test_mobile_metadata_results_put_commit_action_on_its_own_row(self):
        css = (ROOT / "app/static/final-mobile-polish.css").read_text(encoding="utf-8")

        self.assertIn(".results > .result", css)
        self.assertIn("grid-template-columns: 70px minmax(0, 1fr)", css)
        self.assertIn(".results > .result > form", css)
        self.assertIn("grid-column: 1 / -1", css)

    def test_settings_and_tables_keep_touch_scrolling_contained(self):
        css = (ROOT / "app/static/final-mobile-polish.css").read_text(encoding="utf-8")

        self.assertIn("scroll-snap-type: x proximity", css)
        self.assertIn("scroll-snap-align: center", css)
        for selector in (
            ".table-wrap",
            ".settings-history",
            ".settings-table-wrap",
            ".mie-history-table",
        ):
            self.assertIn(selector, css)


if __name__ == "__main__":
    unittest.main()

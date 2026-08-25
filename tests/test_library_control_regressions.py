from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
TEMPLATES = ROOT / "app" / "templates"


class LibraryControlRegressionTests(unittest.TestCase):
    def test_library_control_polish_is_loaded_on_catalog_pages(self) -> None:
        bootstrap = (STATIC / "app-shell-bootstrap.js").read_text(encoding="utf-8")
        self.assertIn("['/library', '/movies', '/shows']", bootstrap)
        self.assertIn("library-controls.css", bootstrap)
        self.assertIn("versionQuery", bootstrap)

    def test_shared_dialog_close_controls_are_loaded_and_font_independent(self) -> None:
        base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        bootstrap = (STATIC / "app-shell-bootstrap.js").read_text(encoding="utf-8")
        css = (STATIC / "dialog-controls.css").read_text(encoding="utf-8")
        bulk_css = (STATIC / "organize-bulk.css").read_text(encoding="utf-8")
        self.assertIn("path='dialog-controls.css'", base)
        self.assertNotIn("/static/dialog-controls.css", bootstrap)
        for selector in (
            ".organize-dialog-close",
            ".source-browser-close",
            ".overview-dialog-close",
            ".title-cast-dialog-close",
            ".profile-account-dialog-close",
            ".profile-avatar-dialog-close",
            ".workspace-inspector-close",
            "#task-dismiss",
        ):
            self.assertIn(selector, css)
        self.assertIn("font-size: 0 !important", css)
        self.assertIn("translate(-50%, -50%) rotate(45deg)", css)
        self.assertIn("translate(-50%, -50%) rotate(-45deg)", css)
        self.assertNotIn(".organize-dialog-close", bulk_css)

    def test_more_filters_uses_same_owned_chrome_as_neighboring_selects(self) -> None:
        css = (STATIC / "library-controls.css").read_text(encoding="utf-8")
        self.assertIn("--library-filter-chevron", css)
        self.assertIn(".library-controls > select,", css)
        self.assertIn(".more-filters-menu > summary", css)
        self.assertIn("appearance: none", css)
        self.assertIn("-webkit-appearance: none", css)
        self.assertIn("background-image: var(--library-filter-chevron)", css)
        self.assertIn("background-position: right 13px center", css)
        self.assertIn("height: 44px", css)
        self.assertIn("padding: 8px 36px 8px 12px", css)
        self.assertIn("border: 1px solid var(--line)", css)
        self.assertIn("border-radius: 3px", css)
        self.assertIn("background-color: #0c1117", css)
        self.assertIn("font: inherit", css)
        self.assertIn(".more-filters-menu > summary::after", css)
        self.assertIn("content: none !important", css)

    def test_more_filters_stacks_above_selection_bar(self) -> None:
        css = (STATIC / "library-controls.css").read_text(encoding="utf-8")
        self.assertIn(".more-filters-menu[open]", css)
        self.assertIn("z-index: 40", css)
        self.assertIn(".more-filters-panel", css)
        self.assertIn("z-index: 41", css)

    def test_library_organize_dialog_has_visible_open_and_loading_states(self) -> None:
        css = (STATIC / "library-controls.css").read_text(encoding="utf-8")
        self.assertIn(".organize-dialog[open]", css)
        self.assertIn("opacity: 1", css)
        self.assertIn("transform: translateY(0) scale(1)", css)
        self.assertIn(".organize-dialog.loading[open] .organize-dialog-body:empty::before", css)
        self.assertIn('content: "Loading organization tools…"', css)

    def test_bulk_organize_manage_tags_aligns_with_form_on_desktop(self) -> None:
        css = (STATIC / "library-controls.css").read_text(encoding="utf-8")
        self.assertIn(".organize-dialog .organize-bulk-page .page-head", css)
        self.assertIn("padding-right: 0", css)
        self.assertIn(".organize-dialog .organize-bulk-page .page-head > .button", css)
        self.assertIn("margin-left: auto", css)

    def test_bulk_organize_still_uses_shared_dialog_contract(self) -> None:
        toolbar = (STATIC / "library-selection-toolbar.js").read_text(encoding="utf-8")
        dialog = (STATIC / "organize-dialog.js").read_text(encoding="utf-8")
        template = (TEMPLATES / "organize_bulk.html").read_text(encoding="utf-8")
        self.assertIn("url: '/titles/organize-bulk'", toolbar)
        self.assertIn("method: 'POST'", toolbar)
        self.assertIn("infomancer:open-dialog", dialog)
        self.assertIn("data-organize-content", template)
        self.assertIn("data-organize-bulk", template)


if __name__ == "__main__":
    unittest.main()

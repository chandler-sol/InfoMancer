from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
TEMPLATES = ROOT / "app" / "templates"


class LibraryControlRegressionTests(unittest.TestCase):
    def test_library_control_polish_is_loaded_on_catalog_pages(self) -> None:
        bootstrap = (STATIC / "app-shell-bootstrap.js").read_text(encoding="utf-8")
        self.assertIn("['/library', '/movies', '/shows']", bootstrap)
        self.assertIn("library-controls-polish.css", bootstrap)
        self.assertIn("versionQuery", bootstrap)

    def test_more_filters_matches_filter_row_and_stacks_above_selection_bar(self) -> None:
        css = (STATIC / "library-controls-polish.css").read_text(encoding="utf-8")
        self.assertIn(".more-filters-menu[open]", css)
        self.assertIn("z-index: 40", css)
        self.assertIn(".more-filters-panel", css)
        self.assertIn("z-index: 41", css)
        self.assertIn(".more-filters-menu > summary", css)
        self.assertIn("height: 44px", css)
        self.assertIn("border: 1px solid var(--line)", css)
        self.assertIn("border-radius: 3px", css)
        self.assertIn("background: #0c1117", css)
        self.assertIn("font: inherit", css)

    def test_library_organize_dialog_has_visible_open_and_loading_states(self) -> None:
        css = (STATIC / "library-controls-polish.css").read_text(encoding="utf-8")
        self.assertIn(".organize-dialog[open]", css)
        self.assertIn("opacity: 1", css)
        self.assertIn("transform: translateY(0) scale(1)", css)
        self.assertIn(".organize-dialog.loading[open] .organize-dialog-body:empty::before", css)
        self.assertIn('content: "Loading organization tools…"', css)

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

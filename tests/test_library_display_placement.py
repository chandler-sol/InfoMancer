from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LibraryDisplayPlacementContracts(unittest.TestCase):
    def test_density_moves_display_toolbar_to_catalog_tabs(self):
        source = (ROOT / "app/static/library-density.js").read_text(encoding="utf-8")

        self.assertIn("const viewToolbar = document.querySelector('.library-view-toolbar')", source)
        self.assertIn("const catalogTabs = document.querySelector('.catalog-tabs')", source)
        self.assertIn("catalogTabs.append(viewToolbar)", source)
        self.assertIn("library-surface-lazy.js remains the sole", source)

    def test_scope_row_right_aligns_display_controls(self):
        source = (ROOT / "app/static/library-density.css").read_text(encoding="utf-8")

        self.assertIn(".catalog-tabs > .library-view-toolbar", source)
        self.assertIn("order: 100", source)
        self.assertIn("margin: 0 0 0 auto", source)
        self.assertIn("justify-content: flex-end", source)
        self.assertIn(".library-display-toolbar.has-letter-jump", source)
        self.assertIn("justify-content: flex-start", source)

    def test_saved_views_stay_before_display_controls(self):
        source = (ROOT / "app/static/library-saved-views-polish.css").read_text(
            encoding="utf-8"
        )

        self.assertIn(".catalog-tabs .catalog-saved-views", source)
        self.assertIn("order: 20", source)

    def test_display_toolbar_is_hidden_during_handoff(self):
        source = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")

        self.assertIn("const libraryViewToolbar = document.querySelector('.library-view-toolbar')", source)
        self.assertIn("libraryViewToolbar.style.visibility = 'hidden'", source)
        self.assertIn("libraryViewToolbar?.style.removeProperty('visibility')", source)
        self.assertIn("libraryViewToolbar?.removeAttribute('aria-hidden')", source)


if __name__ == "__main__":
    unittest.main()

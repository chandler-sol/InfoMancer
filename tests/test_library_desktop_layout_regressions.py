import unittest
from pathlib import Path


class LibraryDesktopLayoutRegressionTests(unittest.TestCase):
    def test_collapsed_sidebar_hover_is_compact_and_scroll_free(self):
        styles = Path("app/static/app-navigation.css").read_text(encoding="utf-8")
        self.assertIn("width:224px", styles)
        self.assertIn("left:209px", styles)
        self.assertIn("width:204px", styles)
        self.assertIn("overflow-x:hidden", styles)

    def test_library_workspace_uses_one_contained_axis(self):
        styles = Path("app/static/library-performance.css").read_text(encoding="utf-8")
        self.assertIn("main.shell:has(> .catalog-tabs)", styles)
        self.assertIn("max-width: none", styles)
        self.assertIn(".library-controls", styles)
        self.assertIn(".library-display-toolbar", styles)
        self.assertIn("#cover-library", styles)
        self.assertIn("max-width: 100%", styles)
        self.assertIn("min-width: 0", styles)


if __name__ == "__main__":
    unittest.main()

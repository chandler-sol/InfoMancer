from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODERN = ROOT / "app" / "static" / "modern.css"


class NavigationPaintStabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.css = MODERN.read_text(encoding="utf-8")

    def test_cross_document_navigation_never_names_main_content(self) -> None:
        transition = self.css.split("@view-transition", 1)[1]
        self.assertIn("view-transition-name: infomancer-chrome", transition)
        self.assertNotIn("view-transition-name: infomancer-content", transition)
        self.assertNotIn("view-transition-name: infomancer-footer", transition)
        self.assertIn("::view-transition-old(root)", transition)
        self.assertIn("::view-transition-new(root)", transition)
        self.assertIn("animation: infomancer-page-reveal .1s ease-out both", transition)

    def test_library_surface_is_not_hidden_during_first_paint(self) -> None:
        self.assertNotIn("library-first-paint-fallback", self.css)
        surface = self.css.split(
            ".library-table,\n#cover-library,\n.library-view-toolbar {", 1
        )[1].split("}", 1)[0]
        self.assertIn("visibility: visible", surface)
        self.assertIn("animation: none", surface)


if __name__ == "__main__":
    unittest.main()

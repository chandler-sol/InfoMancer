from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SourceBrowserCloseIconTests(unittest.TestCase):
    def test_close_button_uses_explicit_svg_instead_of_font_glyph(self):
        template = (ROOT / "app" / "templates" / "_source_browser.html").read_text(encoding="utf-8")
        self.assertIn('class="source-browser-close-icon"', template)
        self.assertIn('aria-label="Close folder browser"', template)
        self.assertNotIn('>×</button>', template)

    def test_close_icon_geometry_is_owned_by_source_browser_styles(self):
        css = (ROOT / "app" / "static" / "sources.css").read_text(encoding="utf-8")
        self.assertIn('.source-browser-close-icon {', css)
        self.assertIn('stroke:currentColor', css)
        self.assertIn('flex:0 0 42px', css)


if __name__ == "__main__":
    unittest.main()

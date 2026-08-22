from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LibraryMatchDisclosureTests(unittest.TestCase):
    def test_match_disclosure_uses_glyph_instead_of_border_chevron(self):
        css = (ROOT / "app/static/library-selection-toolbar.css").read_text(encoding="utf-8")
        marker = ".library-bulk-match-menu > summary::after"
        self.assertIn(marker, css)
        block = css.split(marker, 1)[1].split("}", 1)[0]

        self.assertIn('content: "⌄"', block)
        self.assertIn("margin-left: auto", block)
        self.assertIn("border: 0", block)
        self.assertNotIn("border-right", block)
        self.assertNotIn("border-bottom", block)


if __name__ == "__main__":
    unittest.main()

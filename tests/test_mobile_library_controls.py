from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def css_block(source: str, selector: str, *, start: int = 0) -> str:
    marker = f"{selector} {{"
    position = source.index(marker, start)
    return source[position + len(marker):source.index("}", position)]


class MobileLibraryControlTests(unittest.TestCase):
    def setUp(self):
        self.css = (ROOT / "app/static/library-controls-polish.css").read_text(encoding="utf-8")
        self.js = (ROOT / "app/static/library-controller.js").read_text(encoding="utf-8")

    def test_library_selects_leave_room_for_descenders(self):
        select = css_block(self.css, ".library-controls > select")
        nested_select = css_block(self.css, ".library-controls .more-filters-panel select")

        for block in (select, nested_select):
            self.assertIn("height: 44px", block)
            self.assertIn("padding: 8px 12px", block)
            self.assertIn("line-height: 1.25", block)

    def test_mobile_search_does_not_animate_layout_dimensions(self):
        mobile_start = self.css.index("@media (max-width: 760px)")
        search = css_block(self.css, ".library-filter-search", start=mobile_start)
        search_input = css_block(self.css, ".library-filter-search input", start=mobile_start)
        toggle = css_block(self.css, ".library-filter-search-toggle", start=mobile_start)

        self.assertIn("transition: none", search)
        self.assertNotIn("width", search)
        self.assertNotIn("flex-basis", search)
        self.assertIn("transform: none", search_input)
        self.assertIn("transition: opacity .08s ease", search_input)
        self.assertIn("transition: none", toggle)

    def test_mobile_search_focus_stays_inside_the_initiating_tap(self):
        handler = self.js.split("const setFilterSearchOpen = (open) => {", 1)[1].split(
            "const setParam =", 1
        )[0]

        self.assertIn("mobileControls.matches", handler)
        self.assertIn("input.focus({preventScroll: true})", handler)
        self.assertLess(
            handler.index("input.focus({preventScroll: true})"),
            handler.index("window.setTimeout"),
        )


if __name__ == "__main__":
    unittest.main()

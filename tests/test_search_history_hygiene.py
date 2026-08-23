from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
TEMPLATES = ROOT / "app" / "templates"


class SearchHistoryHygieneContractTests(unittest.TestCase):
    def test_committed_search_marker_is_removed_before_live_library_requests(self) -> None:
        bootstrap = (STATIC / "app-shell-bootstrap.js").read_text(encoding="utf-8")
        self.assertIn("currentUrl.searchParams.has('record_search')", bootstrap)
        self.assertIn("currentUrl.searchParams.delete('record_search')", bootstrap)
        self.assertIn("window.history.replaceState", bootstrap)

        # The committed global search still needs to ask the server to save once.
        base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        self.assertIn('name="record_search" value="1"', base)

    def test_live_library_search_uses_the_cleaned_browser_url(self) -> None:
        controller = (STATIC / "library-controller.js").read_text(encoding="utf-8")
        self.assertIn("const url = new URL(window.location.href);", controller)
        self.assertIn("headers: {'X-InfoMancer-Partial': 'library'}", controller)


if __name__ == "__main__":
    unittest.main()

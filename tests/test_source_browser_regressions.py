from pathlib import Path
import unittest
from unittest import mock

from app import source_browser


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
TEMPLATES = ROOT / "app" / "templates"


class SourceBrowserRegressionTests(unittest.TestCase):
    def test_windows_browse_normalization_does_not_resolve_network_drive(self):
        blocked = OSError(1272, "Guest access is blocked")
        with mock.patch.object(Path, "resolve", side_effect=blocked) as resolver:
            result = source_browser._windows_browse_path("X:/")
        resolver.assert_not_called()
        self.assertTrue(str(result))

    def test_source_browser_close_is_svg_and_blocks_pseudo_marks(self):
        partial = (TEMPLATES / "_source_browser.html").read_text(encoding="utf-8")
        local = (STATIC / "sources.css").read_text(encoding="utf-8")
        shared = (STATIC / "dialog-controls-polish.css").read_text(encoding="utf-8")

        self.assertIn('class="source-browser-close-icon"', partial)
        self.assertIn('d="M6 6L18 18M18 6L6 18"', partial)
        self.assertIn(".source-browser-close::before,.source-browser-close::after", local)
        self.assertIn("content:none!important", local)
        self.assertNotIn(".source-browser-close::before", shared)
        self.assertNotIn(".source-browser-close::after", shared)


if __name__ == "__main__":
    unittest.main()

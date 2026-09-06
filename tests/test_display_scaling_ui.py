import unittest
from pathlib import Path


class DisplayScalingUiTests(unittest.TestCase):
    def test_keyboard_zoom_is_persistent_and_bounded(self):
        script = Path("app/static/password-visibility.js").read_text(encoding="utf-8")

        self.assertIn('ZOOM_STORAGE_KEY = "infomancer-ui-zoom"', script)
        self.assertIn("const ZOOM_MIN = 80", script)
        self.assertIn("const ZOOM_MAX = 160", script)
        self.assertIn("const ZOOM_STEP = 10", script)
        self.assertIn('key === "+" || key === "="', script)
        self.assertIn('key === "-"', script)
        self.assertIn('const zoomReset = key === "0"', script)
        self.assertIn("event.preventDefault()", script)
        self.assertIn("document.documentElement.style.zoom", script)
        self.assertIn("document.documentElement.dataset.uiZoom", script)

    def test_sidebar_width_accounts_for_zoomed_effective_viewport(self):
        script = Path("app/static/password-visibility.js").read_text(encoding="utf-8")

        self.assertIn("SIDEBAR_VIEWPORT_RATIO = .32", script)
        self.assertIn("window.innerWidth / zoomFactor", script)
        self.assertIn("Math.min(SIDEBAR_MAX, viewportMax)", script)
        self.assertIn("clampSidebarWidth(true)", script)

    def test_small_text_and_fixed_popovers_have_accessibility_overrides(self):
        stylesheet = Path("app/static/modern.css").read_text(encoding="utf-8")

        self.assertIn("--microcopy-size: .75rem", stylesheet)
        self.assertIn(".task-card-copy small", stylesheet)
        self.assertIn(".source-recommendation", stylesheet)
        self.assertIn("width: min(330px, calc(100vw - 24px))", stylesheet)
        self.assertIn("width: min(310px, calc(100vw - 32px))", stylesheet)
        self.assertIn("width: min(340px, calc(100vw - 32px))", stylesheet)
        self.assertIn("@media (max-width: 980px) and (min-width: 761px)", stylesheet)
        self.assertIn(".ui-zoom-status", stylesheet)


if __name__ == "__main__":
    unittest.main()

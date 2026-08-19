from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


class MobileInspectorRegressionTests(unittest.TestCase):
    def test_mobile_inspector_uses_deterministic_header_to_bottom_track(self):
        styles = (STATIC / "library-selection-polish.css").read_text(encoding="utf-8")
        self.assertIn("top: 68px !important", styles)
        self.assertIn("top: 62px !important", styles)
        self.assertIn("bottom: 0", styles)
        self.assertIn("max-height: none !important", styles)
        self.assertIn("overscroll-behavior: contain", styles)
        self.assertIn("body.workspace-inspector-open {\n    overflow: hidden", styles)
        self.assertIn(".workspace-inspector-close {\n    width: 44px", styles)

    def test_title_click_resets_persistent_inspector_scroll(self):
        source = (STATIC / "library-inspector-lifecycle.js").read_text(encoding="utf-8")
        self.assertIn("document.addEventListener('pointerdown'", source)
        self.assertIn("panel.scrollTop = 0", source)
        self.assertIn("panel.scrollLeft = 0", source)

    def test_consolidated_workspace_css_satisfies_legacy_core_guard(self):
        loader = (STATIC / "workspace.js").read_text(encoding="utf-8")
        self.assertIn("consolidatedWorkspacePolish", loader)
        self.assertIn("/static/review.css", loader)
        self.assertIn("dataset.workspaceDetailPolish = \"1\"", loader)
        self.assertFalse((STATIC / "workspace-detail-polish.css").exists())


if __name__ == "__main__":
    unittest.main()

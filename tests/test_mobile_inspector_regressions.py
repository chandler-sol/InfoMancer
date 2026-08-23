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

    def test_retired_workspace_polish_loader_is_fully_removed(self):
        loader = (STATIC / "workspace.js").read_text(encoding="utf-8")
        core = (STATIC / "workspace-core.js").read_text(encoding="utf-8")
        self.assertFalse((STATIC / "workspace-detail-polish.css").exists())
        self.assertNotIn("workspace-detail-polish.css", loader)
        self.assertNotIn("workspace-detail-polish.css", core)
        self.assertNotIn("workspaceDetailPolish", loader)
        self.assertNotIn("workspaceDetailPolish", core)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class WorkspaceFoundationTests(unittest.TestCase):
    def test_08_alpha_version_and_workspace_assets_are_enabled(self):
        main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn('APP_VERSION = "0.8.0-alpha.1"', main)
        self.assertIn("path='workspace.css'", base)
        self.assertIn("path='workspace.js'", base)

    def test_workspace_navigation_keeps_core_domains_and_collapsible_destinations(self):
        script = (ROOT / "app" / "static" / "workspace.js").read_text(encoding="utf-8")
        styles = (ROOT / "app" / "static" / "workspace.css").read_text(encoding="utf-8")
        for label in ("Dashboard", "Library", "Review", "Sources", "Activity"):
            self.assertIn(f'"{label}"', script)
        for href in ("/movies", "/shows", "/collections", "/favorites", "/duplicates", "/bulk-match"):
            self.assertIn(f'"{href}"', script)
        self.assertIn('document.createElement("details")', script)
        self.assertIn("workspace-nav-section", styles)
        self.assertIn("site-menu-panel:not(.workspace-nav-ready)", styles)
        self.assertIn("0.8 α", script)

    def test_workspace_deprecates_home_layout_switcher(self):
        styles = (ROOT / "app" / "static" / "workspace.css").read_text(encoding="utf-8")
        self.assertIn(".home-layout-toggle", styles)
        self.assertIn("display: none !important", styles)

    def test_library_inspector_preserves_full_detail_navigation(self):
        script = (ROOT / "app" / "static" / "workspace.js").read_text(encoding="utf-8")
        self.assertIn("workspace-inspector", script)
        self.assertIn("Open full details", script)
        self.assertIn("dblclick", script)
        self.assertIn('event.key === "Escape"', script)
        self.assertIn('event.key === "Enter"', script)

    def test_detail_workspace_adds_local_people_previews(self):
        script = (ROOT / "app" / "static" / "workspace.js").read_text(encoding="utf-8")
        styles = (ROOT / "app" / "static" / "workspace.css").read_text(encoding="utf-8")
        self.assertIn("enhanceCreditHoverCards", script)
        self.assertIn("workspace-person-popover", script)
        self.assertIn("Search library for this person", script)
        self.assertIn("workspace-person-popover", styles)
        self.assertIn("media-dossier .detail-page-head", styles)


if __name__ == "__main__":
    unittest.main()

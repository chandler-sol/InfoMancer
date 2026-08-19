from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SettingsUiContractTests(unittest.TestCase):
    def test_sources_exposes_manual_path_without_deprecated_match_action(self):
        template = (ROOT / "app/templates/sources.html").read_text(encoding="utf-8")
        self.assertNotIn("Ready to match", template)
        self.assertNotIn('<details class="manual-source">', template)
        self.assertIn('<div class="manual-source">', template)
        self.assertIn("Enter a path manually", template)

    def test_system_jump_navigation_is_sticky_and_anchor_safe(self):
        css = (ROOT / "app/static/settings-polish.css").read_text(encoding="utf-8")
        self.assertIn("position: sticky", css)
        self.assertIn("scroll-behavior: smooth", css)
        self.assertIn("scroll-margin-top: 92px", css)
        for target in (
            "#storage", "#fingerprints", "#safety", "#backups", "#updates",
            "#media-information", "#logging", "#service",
        ):
            self.assertIn(target, css)

    def test_recovery_wrapper_cannot_bleed_to_shell_edges(self):
        css = (ROOT / "app/static/settings-polish.css").read_text(encoding="utf-8")
        self.assertIn("body .settings-shell", css)
        self.assertIn("margin-inline: 0 !important", css)
        self.assertIn("width: auto !important", css)


if __name__ == "__main__":
    unittest.main()

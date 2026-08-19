from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SettingsUiRegressionTests(unittest.TestCase):
    def test_system_scroll_uses_deliberate_ease_and_slightly_longer_duration(self):
        source = (ROOT / "app/static/settings-system-nav.js").read_text(encoding="utf-8")
        self.assertIn("easeInOutCubic", source)
        self.assertIn("Math.min(850, Math.max(430, 430 + Math.abs(distance) * .14))", source)

    def test_logging_level_uses_grouped_settings_selection_language(self):
        source = (ROOT / "app/static/settings-system-nav.css").read_text(encoding="utf-8")
        self.assertIn("body #logging .logging-options", source)
        self.assertIn("input:checked + span", source)
        self.assertIn("background: #162014", source)
        self.assertIn("border-radius: 9px", source)

    def test_collapsed_activity_badge_cannot_shift_primary_icon(self):
        source = (ROOT / "app/static/app-navigation.css").read_text(encoding="utf-8")
        self.assertIn("sidebar-collapsed .site-menu-panel a > .menu-count", source)
        self.assertIn("display:none", source)
        self.assertIn("site-menu:hover .site-menu-panel a > .menu-count", source)
        self.assertIn("display:grid", source)


if __name__ == "__main__":
    unittest.main()

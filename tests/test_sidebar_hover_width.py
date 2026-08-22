import unittest
from pathlib import Path


class SidebarHoverWidthTests(unittest.TestCase):
    def test_collapsed_hover_fanout_ignores_resized_sidebar_width(self):
        styles = Path("app/static/app-navigation.css").read_text(encoding="utf-8")

        self.assertIn("body.has-app-sidebar.sidebar-collapsed .site-menu:hover", styles)
        self.assertIn("width:224px;", styles)
        self.assertIn("left:209px;", styles)
        hover_block = styles.split("body.has-app-sidebar.sidebar-collapsed .site-menu:hover", 1)[1]
        self.assertNotIn("width:var(--app-sidebar-width", hover_block.split("/* Activity", 1)[0])


if __name__ == "__main__":
    unittest.main()

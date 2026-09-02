from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CollectionMenuVisibilityTests(unittest.TestCase):
    def read(self, path: str) -> str:
        return (ROOT / path).read_text(encoding="utf-8")

    def test_collection_menu_visibility_css_is_directly_loaded_by_picker(self) -> None:
        template = self.read("app/templates/collections.html")
        self.assertIn(
            "url_for('static', path='collection-menu-visibility.css')",
            template,
        )

    def test_shell_still_preloads_collection_menu_styles(self) -> None:
        bootstrap = self.read("app/static/app-shell-bootstrap.js")
        self.assertGreaterEqual(bootstrap.count("collection-menu-visibility.css"), 2)
        self.assertIn("if (path === '/collections')", bootstrap)
        self.assertIn("/^\\/collections\\/\\d+$/.test(path)", bootstrap)

    def test_picker_action_trigger_is_revealed_by_hover_or_focus(self) -> None:
        css = self.read("app/static/collection-menu-visibility.css")
        self.assertIn(".collection-picker-card:hover .collection-picker-card-actions", css)
        self.assertIn(".collection-picker-card:focus-within .collection-picker-card-actions", css)
        self.assertIn("visibility: hidden !important", css)
        self.assertIn("visibility: visible !important", css)
        self.assertIn("pointer-events: auto !important", css)
        self.assertIn(".collection-picker-menu > summary::before", css)

    def test_touch_layout_keeps_picker_action_trigger_visible(self) -> None:
        css = self.read("app/static/collection-menu-visibility.css")
        self.assertIn("@media (hover: none), (pointer: coarse)", css)
        self.assertIn(".collection-picker-card-actions", css)

    def test_collection_detail_trigger_does_not_require_full_cover_overlay(self) -> None:
        css = self.read("app/static/collection-menu-visibility.css")
        self.assertIn(".collection-cover-card .cover-card-actions", css)
        self.assertIn("background: transparent !important", css)
        self.assertIn(".collection-cover-card .cover-row-menu", css)
        self.assertIn("pointer-events: auto !important", css)

    def test_smart_collection_titles_receive_common_item_actions(self) -> None:
        template = self.read("app/templates/collection_detail.html")
        self.assertIn(
            "current_user.is_librarian and (item.item_type == 'title' or collection.collection_type == 'manual')",
            template,
        )
        self.assertIn("{% if collection.collection_type == 'manual' %}", template)
        self.assertIn("Manage Collections", template)


if __name__ == "__main__":
    unittest.main()

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

    def test_picker_action_trigger_uses_library_hover_and_focus_contract(self) -> None:
        template = self.read("app/templates/collections.html")
        library_css = self.read("app/static/library.css")
        collection_css = self.read("app/static/collection-menu-visibility.css")

        self.assertIn('class="cover-card collection-picker-card"', template)
        self.assertIn('class="cover-card-actions collection-picker-card-actions"', template)
        self.assertIn("cover-row-menu item-action-menu collection-picker-menu", template)
        self.assertIn(".cover-card:hover .cover-card-actions", library_css)
        self.assertIn(".cover-card:has(.cover-row-menu:focus-within) .cover-card-actions", library_css)
        self.assertIn(".cover-card:hover .cover-row-menu", library_css)
        self.assertNotIn(".collection-picker-card:hover .collection-picker-card-actions", collection_css)
        self.assertNotIn(".collection-picker-card:focus-within .collection-picker-card-actions", collection_css)

    def test_picker_artwork_uses_exact_library_cover_hover_animation(self) -> None:
        template = self.read("app/templates/collections.html")
        library_css = self.read("app/static/library.css")
        release_css = self.read("app/static/release-081-collections.css")

        self.assertIn('class="cover-art collection-art"', template)
        self.assertIn(".cover-card:hover .cover-art", library_css)
        self.assertIn("transform:translateY(-4px)", library_css)
        self.assertIn(".collection-card:hover .collection-art", library_css)
        self.assertIn(".collection-picker-card .collection-art", release_css)
        self.assertIn("aspect-ratio: 16 / 9", release_css)
        # The older generic Collection surface still has a -2px hover rule in
        # library.css. The picker compatibility rule must beat it while matching
        # Library's exact -4px movement and shadow without adding a focus-held state.
        self.assertIn(".collection-picker-card .collection-card:hover .collection-art", release_css)
        self.assertIn("transform: translateY(-4px)", release_css)
        self.assertIn("box-shadow: 0 15px 34px rgba(0, 0, 0, .42)", release_css)
        self.assertNotIn(".collection-picker-card:hover .collection-art", release_css)
        self.assertNotIn(".collection-picker-card:focus-within .collection-art", release_css)
        self.assertNotIn(".collection-picker-card .collection-art::after", release_css)

    def test_picker_hover_frame_moves_above_delayed_action_veil(self) -> None:
        release_css = self.read("app/static/release-081-collections.css")

        self.assertIn(".collection-picker-card::before", release_css)
        self.assertIn('content: ""', release_css)
        self.assertIn("z-index: 31", release_css)
        self.assertIn("border: 1px solid transparent", release_css)
        self.assertIn("pointer-events: none", release_css)
        self.assertIn("transition: transform .18s ease, border-color .18s ease", release_css)
        self.assertIn(".collection-picker-card:hover::before", release_css)
        self.assertIn("transform: translateY(-4px)", release_css)
        self.assertIn("border-color: var(--lime)", release_css)
        self.assertIn(".collection-picker-card .cover-card-actions", release_css)
        self.assertIn("z-index: 30", release_css)

    def test_touch_layout_reuses_library_actions_visible_contract(self) -> None:
        library_css = self.read("app/static/library.css")
        script = self.read("app/static/release-081-collections.js")

        self.assertIn(".cover-card.actions-visible .cover-card-actions", library_css)
        self.assertIn("(hover: none), (pointer: coarse)", script)
        self.assertIn("actions-visible", script)
        self.assertIn("coverLink.closest('.cover-card')", script)

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

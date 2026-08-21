from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReleaseUiGremlinContracts(unittest.TestCase):
    def test_closed_global_search_cannot_leave_focus_or_suggestions_behind(self):
        source = (ROOT / "app/static/app-navigation.js").read_text(encoding="utf-8")

        self.assertIn("const settleClosedSearch = () =>", source)
        self.assertIn("searchSuggestions.hidden = true", source)
        self.assertIn("document.activeElement === searchInput", source)
        self.assertIn("searchInput.blur()", source)
        self.assertIn("new MutationObserver(settleClosedSearch)", source)

    def test_notification_bell_exists_in_synchronous_first_paint_css(self):
        progress = (ROOT / "app/static/progress.css").read_text(encoding="utf-8")
        enhanced = (ROOT / "app/static/task-widget.css").read_text(encoding="utf-8")

        self.assertIn(".topbar .task-widget-toggle::before", progress)
        self.assertIn("mask: url", progress)
        self.assertIn(".topbar .task-widget-toggle::before", enhanced)

    def test_scheduled_jobs_do_not_count_as_notification_attention(self):
        source = (ROOT / "app/static/task-widget.js").read_text(encoding="utf-8")

        self.assertIn("const scheduled = event.detail?.scheduled || []", source)
        self.assertIn("if (!tasks.length && scheduled.length) queueMicrotask(render);", source)
        self.assertIn(
            'widget.classList.toggle("has-attention", !active.length && Boolean(recent.length) && !failed.length)',
            source,
        )

    def test_second_plain_title_click_can_close_inspector(self):
        source = (ROOT / "app/static/library-inspector-lifecycle.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("window.addEventListener('click'", source)
        self.assertIn("item.classList.contains('workspace-selected')", source)
        self.assertIn("workspace-inspector-open", source)
        self.assertIn("workspace-inspector-close", source)
        self.assertIn("event.ctrlKey", source)
        self.assertIn("event.shiftKey", source)

    def test_density_stays_with_view_controls_on_desktop(self):
        source = (ROOT / "app/static/library-density.css").read_text(encoding="utf-8")

        self.assertIn("@media (min-width: 681px)", source)
        self.assertIn("grid-template-columns: auto minmax(0, 1fr)", source)
        self.assertIn(".library-display-toolbar.has-letter-jump .library-view-toolbar", source)
        self.assertIn("justify-self: end", source)
        self.assertIn(".library-display-toolbar.has-letter-jump .library-view-controls", source)
        self.assertIn("justify-content: flex-end", source)

    def test_jump_control_explains_that_it_is_alphabet_navigation(self):
        source = (ROOT / "app/static/library-letter-jump.js").read_text(encoding="utf-8")

        self.assertIn("label.textContent = 'A–Z Jump'", source)
        self.assertIn("Jump directly to titles by their first character", source)
        self.assertIn("Jump to titles starting with", source)


if __name__ == "__main__":
    unittest.main()

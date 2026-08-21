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

    def test_closed_library_search_has_the_same_stale_focus_guard(self):
        source = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")

        self.assertIn("const settleClosedFilterSearch = () =>", source)
        self.assertIn("filterSearchSuggestions.hidden = true", source)
        self.assertIn("document.activeElement === filterSearchInput", source)
        self.assertIn("filterSearchInput.blur()", source)
        self.assertIn("new MutationObserver(settleClosedFilterSearch)", source)

    def test_notification_bell_exists_in_synchronous_first_paint_css(self):
        progress = (ROOT / "app/static/progress.css").read_text(encoding="utf-8")
        enhanced = (ROOT / "app/static/task-widget.css").read_text(encoding="utf-8")

        self.assertIn(".topbar .task-widget-toggle::before", progress)
        self.assertIn("mask: url", progress)
        self.assertIn(".topbar .task-widget-toggle::before", enhanced)

    def test_enhanced_task_center_has_one_dom_owner(self):
        source = (ROOT / "app/static/task-widget.js").read_text(encoding="utf-8")

        self.assertIn('const legacyWidget = document.getElementById("task-widget")', source)
        self.assertIn("const widget = legacyWidget.cloneNode(true)", source)
        self.assertIn("legacyWidget.replaceWith(widget)", source)
        self.assertIn("document.addEventListener(\"infomancer:tasks\"", source)
        self.assertNotIn("const scheduled = event.detail?.scheduled", source)

    def test_scheduled_fingerprints_are_not_reported_as_running_tasks(self):
        source = (ROOT / "app/routes/operations.py").read_text(encoding="utf-8")

        self.assertIn("tasks = []", source)
        self.assertIn("scheduled = []", source)
        self.assertIn('"id": "media-fingerprints-queued"', source)
        scheduled_block = source[source.index('"id": "media-fingerprints-queued"') - 300:]
        self.assertIn("scheduled.append({", scheduled_block[:500])
        self.assertIn('return {"tasks": tasks, "scheduled": scheduled}', source)

    def test_task_failure_polling_is_librarian_only(self):
        source = (ROOT / "app/static/task-widget.js").read_text(encoding="utf-8")

        self.assertIn('const canSeeFailures = document.body.classList.contains("role-librarian")', source)
        self.assertIn("if (!canSeeFailures)", source)
        self.assertIn("if (canSeeFailures) pollFailures()", source)

    def test_second_plain_title_click_is_owned_by_workspace_core(self):
        core = (ROOT / "app/static/workspace-core.js").read_text(encoding="utf-8")
        selection = (ROOT / "app/static/library-selection-polish.js").read_text(
            encoding="utf-8"
        )
        lifecycle = (ROOT / "app/static/library-inspector-lifecycle.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("if (String(titleId) === selectedTitleId)", core)
        self.assertIn("closeInspector({historyMode:", core)
        marker = "if (isSelected && isCurrent) {"
        self.assertIn(marker, selection)
        block = selection.split(marker, 1)[1].split("}", 1)[0]
        self.assertNotIn("preventDefault", block)
        self.assertNotIn("stopImmediatePropagation", block)
        self.assertNotIn("window.addEventListener('click'", lifecycle)

    def test_legacy_density_pixels_are_hidden_until_semantic_density_owns_slot(self):
        source = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")

        self.assertIn("coverSizeControl.style.visibility = 'hidden'", source)
        self.assertIn("coverSizeControl.setAttribute('aria-hidden', 'true')", source)
        self.assertIn("pending[0].then(() =>", source)
        self.assertIn("coverSizeControl?.style.removeProperty('visibility')", source)

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

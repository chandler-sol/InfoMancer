from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc\s*=)[^>]*>", re.IGNORECASE)


class ReleaseUiGremlinContracts(unittest.TestCase):
    def test_base_shell_has_no_inline_controller(self):
        source = (ROOT / "app/templates/base.html").read_text(encoding="utf-8")

        self.assertNotRegex(source, INLINE_SCRIPT)
        self.assertIn("app-shell-bootstrap.js", source)
        self.assertIn("app-shell.js", source)
        self.assertIn('data-csrf-token="{{ csrf_token }}"', source)

    def test_library_template_is_markup_only_and_server_renders_saved_view(self):
        source = (ROOT / "app/templates/library.html").read_text(encoding="utf-8")

        self.assertNotIn("<script", source)
        self.assertIn("initial_library_view", source)
        self.assertIn("request.cookies.get('infomancer_library_view')", source)
        self.assertIn("initial_library_view != 'covers'", source)
        self.assertIn("initial_library_view == 'covers'", source)
        self.assertNotIn("setFilterSearchOpen", source)
        self.assertNotIn("updateResults", source)
        self.assertNotIn("setLibraryView", source)

    def test_controller_loader_installs_one_library_state_owner_first(self):
        source = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")

        controller = source.index("await loadScript('library-controller.js')")
        density = source.index("'library-density.js'")
        surface = source.index("'library-surface-lazy.js'")
        selection = source.index("'library-selection-polish.js'")
        self.assertLess(controller, density)
        self.assertLess(controller, surface)
        self.assertLess(controller, selection)

    def test_navigation_controller_no_longer_owns_global_search(self):
        source = (ROOT / "app/static/app-navigation.js").read_text(encoding="utf-8")

        self.assertNotIn("global-search", source)
        self.assertNotIn("settleClosedSearch", source)
        self.assertIn("app-navigation-pending", source)
        self.assertIn("X-InfoMancer-Prefetch", source)

    def test_closed_global_search_cannot_leave_focus_or_suggestions_behind(self):
        source = (ROOT / "app/static/app-shell.js").read_text(encoding="utf-8")

        self.assertIn("const settleClosedSearch = () =>", source)
        self.assertIn("window.clearTimeout(searchFocusTimer)", source)
        self.assertIn("searchSuggestions.hidden = true", source)
        self.assertIn("searchSuggestionController?.abort()", source)
        self.assertIn("searchInput.blur()", source)
        self.assertIn("if (search.classList.contains('open')) searchInput.focus()", source)

    def test_dynamic_post_forms_receive_csrf_at_submit_boundary(self):
        source = (ROOT / "app/static/app-shell.js").read_text(encoding="utf-8")

        self.assertIn("const ensureCsrf = (form) =>", source)
        self.assertIn("form[method=\"post\" i]", source)
        self.assertIn("document.addEventListener('submit', (event) => ensureCsrf(event.target), true)", source)

    def test_closed_library_search_has_its_own_cancelable_focus_guard(self):
        source = (ROOT / "app/static/library-controller.js").read_text(encoding="utf-8")

        self.assertIn("const setFilterSearchOpen = (open) =>", source)
        self.assertIn("window.clearTimeout(focusTimer)", source)
        self.assertIn("librarySuggestions.hidden = true", source)
        self.assertIn("suggestionController?.abort()", source)
        self.assertIn("document.activeElement === input", source)
        self.assertIn("input.blur()", source)
        self.assertIn("if (filterSearch.classList.contains('open')) input.focus()", source)

    def test_expanded_library_search_has_readable_width_and_stable_first_paint(self):
        controls = (ROOT / "app/static/library-controls.css").read_text(encoding="utf-8")
        loader = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")

        self.assertIn("max-width: 420px", controls)
        self.assertIn("min-width: min(320px, 100%)", controls)
        self.assertIn("libraryControls.style.width = '100%'", loader)
        self.assertNotIn("libraryControls.style.width = 'fit-content'", loader)

    def test_selection_controller_batches_select_all_and_letter_changes(self):
        source = (ROOT / "app/static/library-controller.js").read_text(encoding="utf-8")

        self.assertIn("const setManySelected = (ids, checked) =>", source)
        self.assertIn("setManySelected(uniqueChoices().map((choice) => choice.value), target.checked)", source)
        self.assertIn("setManySelected(ids, target.checked)", source)
        self.assertNotIn("uniqueChoices().forEach((choice) => setTitleSelected", source)

    def test_library_surface_module_is_the_only_list_cover_owner(self):
        surface = (ROOT / "app/static/library-surface-lazy.js").read_text(encoding="utf-8")
        controller = (ROOT / "app/static/library-controller.js").read_text(encoding="utf-8")
        density = (ROOT / "app/static/library-density.js").read_text(encoding="utf-8")

        self.assertIn("const applyView = async (view", surface)
        self.assertIn("listSurface.hidden = covers", surface)
        self.assertIn("coverSurface.hidden = !covers", surface)
        self.assertIn("localStorage.setItem(STORAGE_KEY, view)", surface)
        self.assertNotIn("library-list-view", controller)
        self.assertNotIn("library-cover-view", controller)
        self.assertNotIn("library-list-view", density)
        self.assertNotIn("library-cover-view", density)

    def test_server_cookie_is_canonical_library_view_source(self):
        surface = (ROOT / "app/static/library-surface-lazy.js").read_text(encoding="utf-8")
        navigation = (ROOT / "app/static/app-navigation.js").read_text(encoding="utf-8")

        self.assertIn("let preferred = cookieView()", surface)
        self.assertIn("if (!preferred)", surface)
        self.assertIn("const cookieView = libraryViewCookie()", navigation)
        self.assertIn("if (cookieView)", navigation)
        self.assertIn("localStorage.setItem('infomancer-library-view', cookieView)", navigation)

    def test_notification_bell_exists_in_synchronous_first_paint_css(self):
        progress = (ROOT / "app/static/progress.css").read_text(encoding="utf-8")
        enhanced = (ROOT / "app/static/task-widget.css").read_text(encoding="utf-8")

        self.assertIn(".topbar .task-widget-toggle::before", progress)
        self.assertIn("mask: url", progress)
        self.assertIn(".topbar .task-widget-toggle::before", enhanced)

    def test_task_center_is_the_only_task_poll_and_dom_owner(self):
        source = (ROOT / "app/static/task-widget.js").read_text(encoding="utf-8")
        base = (ROOT / "app/templates/base.html").read_text(encoding="utf-8")

        self.assertIn("fetch('/api/tasks'", source)
        self.assertIn("new CustomEvent('infomancer:tasks'", source)
        self.assertNotIn("cloneNode", source)
        self.assertNotIn("replaceWith", source)
        self.assertNotIn("document.addEventListener('infomancer:tasks'", source)
        self.assertNotIn("/api/tasks", base)

    def test_task_tour_demo_explicitly_suspends_real_visual_owner(self):
        source = (ROOT / "app/static/task-widget.js").read_text(encoding="utf-8")
        loader = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")

        self.assertIn("let tourDemoActive = widget.dataset.tourDemo === '1'", source)
        self.assertIn("const syncTourDemoOwnership = () =>", source)
        self.assertIn("new MutationObserver(syncTourDemoOwnership).observe(widget", source)
        self.assertIn("if (tourDemoActive) return;", source)
        self.assertIn("loadScript('task-widget.js')", loader)
        self.assertNotIn("loadTaskWidgetWhenReady", loader)

    def test_scheduled_tasks_do_not_become_notification_attention(self):
        source = (ROOT / "app/static/task-widget.js").read_text(encoding="utf-8")

        self.assertIn("let scheduledSignature = null", source)
        self.assertIn("const scheduledChanged = nextScheduledSignature !== scheduledSignature", source)
        self.assertIn("if (signatureChanged || scheduledChanged) queueMicrotask(render)", source)
        attention = "widget.classList.toggle('has-attention', !active.length && Boolean(recent.length) && !failed.length)"
        self.assertIn(attention, source)
        self.assertNotIn("has-attention', Boolean(scheduled", source)

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

        self.assertIn("const canSeeFailures = document.body.classList.contains('role-librarian')", source)
        self.assertIn("if (!canSeeFailures)", source)
        self.assertIn("if (canSeeFailures) refreshFailures().finally(scheduleFailureRefresh)", source)

    def test_second_plain_title_click_is_owned_by_workspace_core(self):
        core = (ROOT / "app/static/workspace-core.js").read_text(encoding="utf-8")
        selection = (ROOT / "app/static/library-selection-polish.js").read_text(encoding="utf-8")
        lifecycle = (ROOT / "app/static/library-inspector-lifecycle.js").read_text(encoding="utf-8")

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

    def test_density_stays_with_view_controls_on_scope_row(self):
        css = (ROOT / "app/static/library-density.css").read_text(encoding="utf-8")
        script = (ROOT / "app/static/library-density.js").read_text(encoding="utf-8")

        self.assertIn(".catalog-tabs > .library-view-toolbar", css)
        self.assertIn("order: 100", css)
        self.assertIn("margin: 0 0 0 auto", css)
        self.assertIn(".catalog-tabs > .library-view-toolbar .library-view-controls", css)
        self.assertIn("justify-content: flex-end", css)
        self.assertIn("grid-template-columns: repeat(5, 34px)", css)
        self.assertIn("const desktopButtons = desktopSteps.map", script)
        self.assertIn("catalogTabs.append(viewToolbar)", script)
        self.assertNotIn("range.type = 'range'", script)
        self.assertNotIn("library-density-range", script)

    def test_alphabet_control_honestly_describes_current_filter_behavior(self):
        source = (ROOT / "app/static/library-letter-jump.js").read_text(encoding="utf-8")

        self.assertIn("label.textContent = 'Starts with'", source)
        self.assertIn("Show titles starting with a specific character", source)
        self.assertIn("heading.textContent = 'Show titles starting with'", source)
        self.assertNotIn("label.textContent = 'A–Z Jump'", source)


if __name__ == "__main__":
    unittest.main()

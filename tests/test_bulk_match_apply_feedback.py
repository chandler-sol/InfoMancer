from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BulkMatchApplyFeedbackTests(unittest.TestCase):
    def test_apply_shows_working_completion_and_error_feedback(self):
        script = (ROOT / "app/static/bulk-match-apply.js").read_text(encoding="utf-8")
        self.assertIn("Applying ${count} selected ${noun}", script)
        self.assertIn("button.disabled = true", script)
        self.assertIn("You can keep using InfoMancer while this finishes.", script)
        self.assertIn("fetch(reviewForm.action", script)
        self.assertIn("await responseDetail(response)", script)
        self.assertIn("HTTP ${response.status}", script)
        self.assertIn("Activity/Logs", script)
        self.assertIn("resetApplyState()", script)
        self.assertIn("button.disabled = false", script)
        self.assertNotIn("track.className = 'task-track'", script)

    def test_apply_sends_csrf_header_and_avoids_webview_keepalive(self):
        script = (ROOT / "app/static/bulk-match-apply.js").read_text(encoding="utf-8")
        self.assertIn("input[name=\"csrf_token\"]", script)
        self.assertIn("'X-CSRF-Token': token", script)
        self.assertIn("'X-Requested-With': 'InfoMancerAsync'", script)
        self.assertIn("Accept: 'application/json'", script)
        self.assertIn("new AbortController()", script)
        self.assertIn("signal: controller.signal", script)
        self.assertIn("error?.name === 'AbortError'", script)
        self.assertNotIn("keepalive", script.split("fetch(reviewForm.action", 1)[1])

    def test_bulk_apply_controller_has_one_canonical_loader(self):
        for template_name in ("bulk_movie_match.html", "bulk_tv_match.html"):
            template = (ROOT / f"app/templates/{template_name}").read_text(encoding="utf-8")
            self.assertEqual(template.count("bulk-match-apply.js"), 1, template_name)
            self.assertIn("bulk-match-feedback.js", template)
            self.assertLess(
                template.index("bulk-match-apply.js"),
                template.index("bulk-match-feedback.js"),
            )
        bootstrap = (ROOT / "app/static/app-shell-bootstrap.js").read_text(encoding="utf-8")
        self.assertNotIn("/static/bulk-match-apply.js", bootstrap)
        script = (ROOT / "app/static/bulk-match-apply.js").read_text(encoding="utf-8")
        self.assertIn("window.__infomancerBulkMatchApplyLoaded", script)
        self.assertIn("document.addEventListener('submit', runApply, true)", script)

    def test_successful_apply_updates_review_in_place(self):
        script = (ROOT / "app/static/bulk-match-apply.js").read_text(encoding="utf-8")
        self.assertIn("await response.json()", script)
        self.assertIn("applied_title_ids", script)
        self.assertIn("checkbox.closest('tr')?.remove()", script)
        self.assertIn("infomancer:bulk-match-applied", script)
        self.assertIn("updateContinueLinks()", script)
        self.assertIn("showEmptyPageState()", script)
        self.assertIn("Continue review", script)
        self.assertIn("contentType.includes('application/json')", script)
        compatibility = script.index("if (!contentType.includes('application/json'))")
        apply_in_place = script.index("const payload = await response.json()")
        self.assertLess(compatibility, apply_in_place)
        self.assertNotIn("window.location.assign", script[apply_in_place:])

    def test_apply_coordinates_with_progressive_hydration(self):
        apply_script = (ROOT / "app/static/bulk-match-apply.js").read_text(encoding="utf-8")
        feedback = (ROOT / "app/static/bulk-match-feedback.js").read_text(encoding="utf-8")
        self.assertIn("infomancer:bulk-apply-started", apply_script)
        self.assertIn("infomancer:bulk-apply-finished", apply_script)
        self.assertIn("const applyRunning = () =>", feedback)
        self.assertIn("if (applyRunning())", feedback)
        self.assertIn("progressiveAbortController?.abort()", feedback)
        self.assertIn("pendingAnalysisReload", feedback)
        self.assertIn("infomancer:bulk-apply-finished", feedback)
        self.assertIn("refreshProgressiveMatches(queued)", feedback)

    def test_bulk_review_posters_are_deferred_from_interaction_path(self):
        feedback = (ROOT / "app/static/bulk-match-feedback.js").read_text(encoding="utf-8")
        self.assertIn("const deferPoster = (poster) =>", feedback)
        self.assertIn("poster.loading = 'lazy'", feedback)
        self.assertIn("poster.decoding = 'async'", feedback)
        self.assertIn("poster.fetchPriority = 'low'", feedback)
        self.assertIn("img.poster-thumb", feedback)

    def test_bulk_progress_copy_does_not_repeat_row_explainer(self):
        feedback = (ROOT / "app/static/bulk-match-feedback.js").read_text(encoding="utf-8")
        self.assertNotIn("Matches will appear in their rows as they are found", feedback)
        self.assertNotIn("Matches will appear here as they are found", feedback)
        self.assertIn("progressCopy.textContent = current > 0 ? detail : 'Preparing TVDB searches…'", feedback)

    def test_bulk_review_has_one_click_clear_selection(self):
        script = (ROOT / "app/static/bulk-match-apply.js").read_text(encoding="utf-8")
        self.assertIn("data-bulk-clear-selection", script)
        self.assertIn("clear.textContent = 'Clear selection'", script)
        self.assertIn("input[name=\"matches\"]:checked", script)
        self.assertIn("checkbox.checked = false", script)
        self.assertIn("showStatus('Selection cleared.')", script)

    def test_hardened_apply_routes_are_registered_before_review(self):
        routes = (ROOT / "app/routes/__init__.py").read_text(encoding="utf-8")
        handler = (ROOT / "app/routes/bulk_match_apply.py").read_text(encoding="utf-8")
        self.assertLess(
            routes.index("build_bulk_match_apply_router"),
            routes.index("build_review_router"),
        )
        self.assertIn('@librarian_post("/movies/bulk-match")', handler)
        self.assertIn('@librarian_post("/shows/bulk-match")', handler)
        self.assertIn("except Exception as exc", handler)
        self.assertIn("First error:", handler)
        self.assertIn("Bulk match apply finished", handler)
        self.assertIn("JSONResponse", handler)
        self.assertIn('request.headers.get("x-requested-with") == "InfoMancerAsync"', handler)
        self.assertIn('"applied_title_ids"', handler)

    def test_bulk_feedback_stays_visible_from_bottom_of_long_review(self):
        script = (ROOT / "app/static/bulk-match-feedback.js").read_text(encoding="utf-8")
        self.assertIn("const makeFeedbackSticky = (node) =>", script)
        self.assertIn("node.style.position = 'sticky'", script)
        self.assertIn("node.style.top = '80px'", script)
        self.assertIn("makeFeedbackSticky(progress)", script)
        self.assertIn("makeFeedbackSticky(status)", script)
        self.assertNotIn("scrollIntoView", script)

    def test_selected_bulk_apply_returns_to_selected_review_scope_for_native_fallback(self):
        routes = (ROOT / "app/routes/bulk_match_apply.py").read_text(encoding="utf-8")
        self.assertIn("?review=true&selected=true", routes)

        movie_template = (ROOT / "app/templates/bulk_movie_match.html").read_text(encoding="utf-8")
        tv_template = (ROOT / "app/templates/bulk_tv_match.html").read_text(encoding="utf-8")
        self.assertIn('name="selected_scope" value="1"', movie_template)
        self.assertIn('name="selected_scope" value="1"', tv_template)

    def test_manual_match_round_trip_preserves_checkbox_choices(self):
        script = (ROOT / "app/static/bulk-match-feedback.js").read_text(encoding="utf-8")
        self.assertIn("infomancer:bulk-match-selection", script)
        self.assertIn("window.sessionStorage.setItem(selectionMemoryKey", script)
        self.assertIn("window.sessionStorage.getItem(selectionMemoryKey)", script)
        self.assertIn("link.classList.contains('possible-match-link')", script)
        self.assertIn("rememberReviewSelection();", script)
        self.assertIn("reviewForm.querySelectorAll('input[name=\"matches\"]').forEach(restoreRememberedCheckbox)", script)
        self.assertIn("Object.prototype.hasOwnProperty.call(memory, titleId)", script)
        self.assertIn("restoreRememberedCheckbox(checkbox);", script)
        self.assertIn("let clearSelectionOnPageHide = false", script)
        self.assertIn("clearSelectionOnPageHide = true", script)
        self.assertIn("const clearReviewSelection = () =>", script)
        self.assertIn("window.sessionStorage.removeItem(selectionMemoryKey)", script)
        self.assertIn("window.addEventListener('pagehide', () =>", script)
        self.assertIn("if (clearSelectionOnPageHide)", script)
        self.assertIn("clearReviewSelection();", script)
        self.assertIn("rememberReviewSelection();", script)

    def test_search_return_only_happens_after_selected_review_is_empty(self):
        script = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")
        self.assertIn("node.textContent.trim().startsWith('No selected unmatched')", script)
        self.assertIn("pending && /^Matched\\s+\\d+/.test(message) && empty", script)


if __name__ == "__main__":
    unittest.main()

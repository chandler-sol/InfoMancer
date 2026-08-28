from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BulkMatchApplyFeedbackTests(unittest.TestCase):
    def test_apply_shows_working_and_completion_feedback(self):
        script = (ROOT / "app/static/bulk-match-feedback.js").read_text(encoding="utf-8")
        self.assertIn("Applying ${count} selected ${noun}", script)
        self.assertIn("track.className = 'task-track'", script)
        self.assertIn("button.disabled = true", script)
        self.assertIn("/^Matched\\s+\\d+/i.test(completionMessage)", script)
        self.assertIn("You can keep using InfoMancer while this finishes.", script)
        self.assertIn("fetch(reviewForm.action", script)
        self.assertIn("resetApplyState()", script)
        self.assertIn("button.disabled = false", script)

    def test_bulk_feedback_stays_visible_from_bottom_of_long_review(self):
        script = (ROOT / "app/static/bulk-match-feedback.js").read_text(encoding="utf-8")
        self.assertIn("const makeFeedbackSticky = (node) =>", script)
        self.assertIn("node.style.position = 'sticky'", script)
        self.assertIn("node.style.top = '80px'", script)
        self.assertIn("makeFeedbackSticky(progress)", script)
        self.assertIn("makeFeedbackSticky(status)", script)
        self.assertNotIn("scrollIntoView", script)

    def test_selected_bulk_apply_returns_to_selected_review_scope(self):
        routes = (ROOT / "app/routes/review.py").read_text(encoding="utf-8")
        self.assertIn(
            'destination = "/movies/bulk-match?review=true&selected=true" if selected_scope else "/movies/bulk-match?review=true"',
            routes,
        )
        self.assertIn(
            'destination = "/shows/bulk-match?review=true&selected=true" if selected_scope else "/shows/bulk-match?review=true"',
            routes,
        )

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

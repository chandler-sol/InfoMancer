from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BulkMatchApplyFeedbackTests(unittest.TestCase):
    def test_apply_shows_working_and_completion_feedback(self):
        script = (ROOT / "app/static/bulk-match-feedback.js").read_text(encoding="utf-8")
        self.assertIn("Applying ${count} selected ${noun}", script)
        self.assertIn("track.className = 'task-track'", script)
        self.assertIn("/^Matched\\s+\\d+/i.test(completionMessage)", script)
        self.assertIn("Unselected or unresolved items will remain here for review.", script)

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

    def test_search_return_only_happens_after_selected_review_is_empty(self):
        script = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")
        self.assertIn("node.textContent.trim().startsWith('No selected unmatched')", script)
        self.assertIn("pending && /^Matched\\s+\\d+/.test(message) && empty", script)


if __name__ == "__main__":
    unittest.main()

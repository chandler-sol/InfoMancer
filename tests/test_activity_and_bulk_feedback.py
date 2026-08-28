from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ActivityAndBulkFeedbackTests(unittest.TestCase):
    def test_activity_mark_all_control_matches_header_action_height(self):
        template = (ROOT / "app/templates/activity.html").read_text(encoding="utf-8")
        css = (ROOT / "app/static/activity.css").read_text(encoding="utf-8")

        self.assertIn("activity-page-actions", template)
        self.assertIn("activity.css", template)
        self.assertIn(".activity-page-actions > form > .button", css)
        self.assertIn("min-height: 40px", css)

    def test_bulk_match_pages_use_canonical_task_events_instead_of_private_pollers(self):
        movie = (ROOT / "app/templates/bulk_movie_match.html").read_text(encoding="utf-8")
        tv = (ROOT / "app/templates/bulk_tv_match.html").read_text(encoding="utf-8")
        feedback = (ROOT / "app/static/bulk-match-feedback.js").read_text(encoding="utf-8")

        for source in (movie, tv):
            self.assertIn("data-bulk-match-controller", source)
            self.assertIn("bulk-match-feedback.js", source)
            self.assertIn("Preparing TVDB searches", source)
            self.assertIn("data-bulk-apply-status", source)
            self.assertIn("data-bulk-apply-button", source)
        self.assertNotIn("/api/movie-match-analysis", movie)
        self.assertNotIn("/api/tv-match-analysis", tv)
        self.assertIn("document.addEventListener('infomancer:tasks'", feedback)
        self.assertIn("Matches ready. Loading the review", feedback)

    def test_bulk_apply_uses_async_request_so_shell_stays_interactive(self):
        feedback = (ROOT / "app/static/bulk-match-feedback.js").read_text(encoding="utf-8")

        self.assertIn("Applying ${count} selected ${noun}", feedback)
        self.assertIn("reviewForm.setAttribute('aria-busy', 'true')", feedback)
        self.assertIn("fetch(reviewForm.action", feedback)
        self.assertIn("body: new FormData(reviewForm)", feedback)
        self.assertIn("credentials: 'same-origin'", feedback)
        self.assertIn("keepalive: true", feedback)
        self.assertIn("window.location.assign(response.url || completeUrl)", feedback)
        self.assertIn("resetApplyState()", feedback)
        self.assertNotIn("HTMLFormElement.prototype.submit.call(reviewForm)", feedback)


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BulkMovieMatchProgressTests(unittest.TestCase):
    def test_progress_api_reads_saved_suggestions_for_active_job(self):
        route = (ROOT / "app" / "routes" / "bulk_match_progress.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"/api/movies/bulk-match/progress"', route)
        self.assertIn("Depends(require_librarian)", route)
        self.assertIn('job.get("title_ids")', route)
        self.assertIn("movie_match_suggestions", route)
        self.assertIn('"processed": int(job.get("processed") or 0)', route)
        self.assertNotIn("candidate_json\":", route)

    def test_bulk_movie_rows_expose_progressive_render_targets(self):
        template = (ROOT / "app" / "templates" / "bulk_movie_match.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('data-bulk-match-progress-url="/api/movies/bulk-match/progress"', template)
        self.assertIn('data-bulk-movie-id="{{ movie.id }}"', template)
        self.assertIn("data-bulk-suggestion-cell", template)
        self.assertIn("data-bulk-confidence-cell", template)
        self.assertIn("Suggested TVDB result", template)

    def test_feedback_fetches_only_when_progress_advances(self):
        script = (ROOT / "app" / "static" / "bulk-match-feedback.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("refreshProgressiveMatches", script)
        self.assertIn("requested <= lastProgressiveProcessed", script)
        self.assertIn("fetch(progressUrl", script)
        self.assertIn("items.forEach(renderProgressiveItem)", script)
        self.assertIn("textContent", script)
        self.assertNotIn("innerHTML", script)

    def test_progress_router_is_registered(self):
        routes = (ROOT / "app" / "routes" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("build_bulk_match_progress_router", routes)
        self.assertLess(
            routes.index("build_bulk_match_progress_router,"),
            routes.index("build_review_router,"),
        )


if __name__ == "__main__":
    unittest.main()

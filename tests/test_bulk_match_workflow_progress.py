import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BulkMatchWorkflowProgressTests(unittest.TestCase):
    def test_apply_route_tracks_real_per_item_progress(self):
        source = (ROOT / "app/routes/bulk_match_apply.py").read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn("_APPLY_JOBS", source)
        self.assertIn("_APPLY_JOBS_LOCK", source)
        self.assertIn('"/api/movies/bulk-match/apply-progress"', source)
        self.assertIn('"/api/shows/bulk-match/apply-progress"', source)
        self.assertIn('apply_job_id: str = Form("")', source)
        self.assertIn("for value in matches:", source)
        self.assertIn('status="running", processed=processed, total=total', source)
        self.assertIn('status="complete", processed=total, total=total', source)

    def test_apply_browser_polls_progress_instead_of_animating_fake_work(self):
        source = (ROOT / "app/static/bulk-match-apply.js").read_text(encoding="utf-8")
        self.assertIn("startApplyProgressPolling", source)
        self.assertIn("formData.set('apply_job_id', jobId)", source)
        self.assertIn("/api/movies/bulk-match/apply-progress", source)
        self.assertIn("/api/shows/bulk-match/apply-progress", source)
        self.assertIn("processed / total * 100", source)
        self.assertIn("Applying metadata for", source)
        self.assertNotIn("track.append(document.createElement('i'))", source)

    def test_selected_review_keeps_one_phase_aware_progress_card(self):
        movie = (ROOT / "app/templates/bulk_movie_match.html").read_text(encoding="utf-8")
        tv = (ROOT / "app/templates/bulk_tv_match.html").read_text(encoding="utf-8")
        for template in (movie, tv):
            self.assertIn("bulk-workflow-progress", template)
            self.assertIn("data-bulk-match-progress-heading", template)
            self.assertIn("data-bulk-match-progress-copy", template)
            self.assertIn("data-bulk-match-progress-fill", template)
            self.assertIn("data-bulk-match-progress-phase", template)
            self.assertIn("Analysis complete.", template)

    def test_completed_analysis_card_is_determinate_after_reload(self):
        css = (ROOT / "app/static/bulk-match.css").read_text(encoding="utf-8")
        self.assertIn('data-bulk-match-progress-phase="ready"', css)
        self.assertIn("animation: none", css)
        self.assertIn("width: 100%", css)

    def test_late_analysis_events_cannot_overwrite_apply_phase(self):
        feedback = (ROOT / "app/static/bulk-match-feedback.js").read_text(encoding="utf-8")
        self.assertIn("const applyOwnsProgress = () =>", feedback)
        self.assertIn("['apply', 'complete', 'error'].includes(progressPhase())", feedback)
        self.assertIn("if (applyRunning() || applyOwnsProgress()) return 0", feedback)
        self.assertIn("if (applyOwnsProgress()) return", feedback)
        self.assertIn("if (!applyOwnsProgress())", feedback)


if __name__ == "__main__":
    unittest.main()

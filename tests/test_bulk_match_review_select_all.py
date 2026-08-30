import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BulkMatchReviewSelectAllTests(unittest.TestCase):
    def test_movie_review_exposes_select_all_control(self):
        template = (ROOT / "app" / "templates" / "bulk_movie_match.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="review-select-all-movies"', template)
        self.assertIn('id="review-select-all-label"', template)
        self.assertIn('class="batch-select-all"', template)
        self.assertIn('setReviewSelection(!matches.every(choice => choice.checked))', template)

    def test_review_select_all_tracks_partial_and_dynamic_rows(self):
        template = (ROOT / "app" / "templates" / "bulk_movie_match.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('reviewSelectAll.classList.toggle("is-partial", partiallySelected)', template)
        self.assertIn('reviewSelectAll.classList.toggle("is-selected", allSelected)', template)
        self.assertIn('reviewObserver.observe(reviewBody, { childList: true, subtree: true })', template)
        self.assertIn('button.addEventListener("click", () => setReviewSelection(false))', template)
        self.assertIn('new Event("change", { bubbles: true })', template)


if __name__ == "__main__":
    unittest.main()

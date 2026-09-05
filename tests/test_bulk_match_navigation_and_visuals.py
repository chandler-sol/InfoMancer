from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BulkMatchNavigationAndVisualTests(unittest.TestCase):
    def test_bulk_match_links_preserve_exact_review_location(self):
        movie = (ROOT / "app/templates/bulk_movie_match.html").read_text(encoding="utf-8")
        tv = (ROOT / "app/templates/bulk_tv_match.html").read_text(encoding="utf-8")
        detail = (ROOT / "app/templates/tvdb.html").read_text(encoding="utf-8")

        self.assertIn('url.searchParams.set("return_to", window.location.pathname + window.location.search)', movie)
        self.assertIn("url.searchParams.set('return_to', window.location.pathname + window.location.search)", tv)
        self.assertIn("bulk_return.startswith('/movies/bulk-match')", detail)
        self.assertIn("bulk_return.startswith('/shows/bulk-match')", detail)
        self.assertIn("Bulk movie matching", detail)
        self.assertIn("Bulk TV matching", detail)

    def test_review_queue_has_clear_selection_control(self):
        movie = (ROOT / "app/templates/bulk_movie_match.html").read_text(encoding="utf-8")
        tv = (ROOT / "app/templates/bulk_tv_match.html").read_text(encoding="utf-8")

        for template in (movie, tv):
            self.assertIn("data-bulk-clear-selection", template)
            self.assertIn(".match-check", template)
            self.assertIn("checked = false", template)

    def test_bulk_match_review_is_unpaginated_and_applies_full_selection(self):
        routes = (ROOT / "app/routes/__init__.py").read_text(encoding="utf-8")
        review = (ROOT / "app/routes/bulk_match_review.py").read_text(encoding="utf-8")
        apply = (ROOT / "app/routes/bulk_match_apply.py").read_text(encoding="utf-8")
        movie = (ROOT / "app/templates/bulk_movie_match.html").read_text(encoding="utf-8")
        tv = (ROOT / "app/templates/bulk_tv_match.html").read_text(encoding="utf-8")

        self.assertLess(
            routes.index("build_bulk_match_review_router"),
            routes.index("build_review_router"),
        )
        self.assertNotIn("LIMIT 50", review)
        self.assertNotIn("OFFSET", review)
        self.assertNotIn("matches[:50]", apply)
        self.assertIn("for value in matches:", apply)
        self.assertIn('"requested": len(matches)', apply)
        for template in (movie, tv):
            self.assertNotIn("Previous 50", template)
            self.assertNotIn("Next 50", template)
            self.assertNotIn("offset=", template)
            self.assertNotIn("Choose another batch", template)

    def test_bulk_match_styles_add_zebra_hover_and_larger_posters(self):
        css = (ROOT / "app/static/bulk-match.css").read_text(encoding="utf-8")

        self.assertIn("tbody tr:nth-child(even)", css)
        self.assertIn("tbody tr:hover", css)
        self.assertIn("box-shadow: inset 3px 0 0", css)
        self.assertIn("width: 48px", css)
        self.assertIn("height: 72px", css)

    def test_manual_tvdb_layout_has_dedicated_alignment_styles(self):
        template = (ROOT / "app/templates/tvdb.html").read_text(encoding="utf-8")
        css = (ROOT / "app/static/tvdb-match.css").read_text(encoding="utf-8")

        self.assertIn("manual-tvdb-heading", template)
        self.assertIn("manual-tvdb-help", template)
        self.assertIn("manual-tvdb-form", template)
        self.assertIn('grid-template-areas:', css)
        self.assertIn('"help form"', css)


if __name__ == "__main__":
    unittest.main()

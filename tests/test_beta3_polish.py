from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BetaThreePolishTests(unittest.TestCase):
    def test_packaged_https_uses_bundled_certifi_store(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        package_init = (ROOT / "app/__init__.py").read_text(encoding="utf-8")
        self.assertIn("certifi==2026.7.22", requirements)
        self.assertIn('os.environ["SSL_CERT_FILE"] = certifi.where()', package_init)
        self.assertIn("Path(_configured_ca).is_file()", package_init)

    def test_activity_explains_ssl_failure_and_offers_retry(self):
        template = (ROOT / "app/templates/activity.html").read_text(encoding="utf-8")
        self.assertIn("CERTIFICATE_VERIFY_FAILED", template)
        self.assertIn("Technical details", template)
        self.assertIn("InfoMancer could not verify the provider's security certificate", template)
        self.assertIn('action="/imdb-genres/sync"', template)
        self.assertIn("Retry metadata", template)

    def test_bulk_matching_distinguishes_provider_failures_from_no_result(self):
        movie = (ROOT / "app/templates/bulk_movie_match.html").read_text(encoding="utf-8")
        television = (ROOT / "app/templates/bulk_tv_match.html").read_text(encoding="utf-8")
        feedback = (ROOT / "app/static/bulk-match-feedback.js").read_text(encoding="utf-8")
        for source in (movie, television, feedback):
            self.assertIn("TVDB is not configured", source)
            self.assertIn("TVDB credentials were rejected", source)
            self.assertIn("TVDB could not be reached", source)
            self.assertIn("/settings/metadata", source)
        self.assertIn("No result", movie)
        self.assertIn("Try manual search", movie)

    def test_long_poster_titles_use_fixed_three_line_slot(self):
        css = (ROOT / "app/static/display-accessibility.css").read_text(encoding="utf-8")
        script = (ROOT / "app/static/password-visibility.js").read_text(encoding="utf-8")
        self.assertIn(".cover-card-link > strong", css)
        self.assertIn(".home-recent-link > strong", css)
        self.assertIn("-webkit-line-clamp: 3", css)
        self.assertIn("min-height: 3.9em", css)
        self.assertIn("titleNode.title = fullTitle", script)


if __name__ == "__main__":
    unittest.main()

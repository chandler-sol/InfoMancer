from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FORM_RE = re.compile(r"<form\b[^>]*method=[\"']post[\"'][^>]*>.*?</form>", re.I | re.S)


class BulkMatchCsrfTests(unittest.TestCase):
    def test_bulk_match_post_forms_include_csrf_token(self):
        for template_name in ("bulk_movie_match.html", "bulk_tv_match.html"):
            template = (ROOT / f"app/templates/{template_name}").read_text(encoding="utf-8")
            forms = FORM_RE.findall(template)
            self.assertTrue(forms, template_name)
            for form in forms:
                self.assertIn('name="csrf_token"', form, f"Missing CSRF token in {template_name}: {form[:160]}")

    def test_async_apply_promotes_form_token_to_csrf_header(self):
        script = (ROOT / "app/static/bulk-match-apply.js").read_text(encoding="utf-8")
        self.assertIn('reviewForm.querySelector(\'input[name="csrf_token"]\')?.value', script)
        self.assertIn("'X-CSRF-Token': token", script)
        self.assertIn("body: new FormData(reviewForm)", script)


if __name__ == "__main__":
    unittest.main()

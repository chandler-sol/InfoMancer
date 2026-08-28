from pathlib import Path
import re
import unittest

from app.routes.bulk_match_apply import build_router
from app.routes.context import RouteContext


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

    def test_bulk_apply_request_is_framework_request_not_query_parameter(self):
        router, _ = build_router(RouteContext({}))
        for path in ("/movies/bulk-match", "/shows/bulk-match"):
            with self.subTest(path=path):
                route = next(item for item in router.routes if item.path == path)
                query_names = {field.name for field in route.dependant.query_params}
                body_names = {field.name for field in route.dependant.body_params}
                self.assertNotIn("request", query_names)
                self.assertIn("matches", body_names)
                self.assertIn("selected_scope", body_names)

    def test_validation_errors_are_rendered_as_text_not_object_object(self):
        script = (ROOT / "app/static/bulk-match-apply.js").read_text(encoding="utf-8")
        self.assertIn("const formatJsonDetail = (detail) =>", script)
        self.assertIn("item.loc", script)
        self.assertIn("item.msg || item.message || 'Invalid request'", script)
        self.assertNotIn("String(payload?.detail || payload?.message || '')", script)


if __name__ == "__main__":
    unittest.main()

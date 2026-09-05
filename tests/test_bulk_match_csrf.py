from pathlib import Path
import json
import re
from types import SimpleNamespace
import unittest

from app.routes.bulk_match_apply import build_router
from app.routes.context import RouteContext


ROOT = Path(__file__).resolve().parents[1]
FORM_RE = re.compile(r"<form\b[^>]*method=[\"']post[\"'][^>]*>.*?</form>", re.I | re.S)


class _FakeConnection:
    def __init__(self, executions):
        self.executions = executions

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, sql, parameters=()):
        self.executions.append((sql, tuple(parameters)))
        return self


class _FakeDb:
    def __init__(self):
        self.executions = []

    def connect(self):
        return _FakeConnection(self.executions)


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
        self.assertIn("const formData = new FormData(reviewForm)", script)
        self.assertIn("formData.set('apply_job_id', jobId)", script)
        self.assertIn("body: formData", script)
        self.assertIn("'X-Requested-With': 'InfoMancerAsync'", script)
        self.assertIn("Accept: 'application/json'", script)

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
                self.assertIn("apply_job_id", body_names)

    def test_async_apply_route_returns_compact_result_contract(self):
        route_source = (ROOT / "app/routes/bulk_match_apply.py").read_text(encoding="utf-8")
        self.assertIn('request.headers.get("x-requested-with") == "InfoMancerAsync"', route_source)
        self.assertIn("return JSONResponse({", route_source)
        self.assertIn('"applied_title_ids"', route_source)
        self.assertIn('"failures"', route_source)
        self.assertIn('"redirect_url"', route_source)
        self.assertIn("return redirect(destination, message)", route_source)

    def test_async_apply_handler_reports_exact_successful_title_ids(self):
        db = _FakeDb()
        stored = []
        events = []

        def record_event(*args, **kwargs):
            events.append((args, kwargs))

        namespace = {
            "db": db,
            "record_event": record_event,
            "redirect": lambda path, message="": (path, message),
            "store_movie_match": lambda title_id, provider_id: stored.append(("movie", title_id, provider_id)),
            "store_tv_match": lambda title_id, provider_id: stored.append(("tv", title_id, provider_id)),
        }
        _, handlers = build_router(RouteContext(namespace))
        request = SimpleNamespace(
            headers={"x-requested-with": "InfoMancerAsync"},
            state=SimpleNamespace(user=SimpleNamespace(id=17)),
        )

        response = handlers["bulk_movie_match_apply"](
            request,
            ["11:901", "12:902"],
            "",
            "",
        )
        payload = json.loads(response.body)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["applied_title_ids"], [11, 12])
        self.assertEqual(payload["applied"], 2)
        self.assertEqual(payload["failed"], 0)
        self.assertEqual(stored, [("movie", 11, 901), ("movie", 12, 902)])
        self.assertEqual(len(db.executions), 2)
        self.assertTrue(any("Bulk match apply finished" in args[1] for args, _ in events))

    def test_validation_errors_are_rendered_as_text_not_object_object(self):
        script = (ROOT / "app/static/bulk-match-apply.js").read_text(encoding="utf-8")
        self.assertIn("const formatJsonDetail = (detail) =>", script)
        self.assertIn("item.loc", script)
        self.assertIn("item.msg || item.message || 'Invalid request'", script)
        self.assertNotIn("String(payload?.detail || payload?.message || '')", script)


if __name__ == "__main__":
    unittest.main()

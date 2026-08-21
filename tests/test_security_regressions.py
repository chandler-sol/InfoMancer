import json
import unittest
from types import SimpleNamespace

from starlette.requests import Request

import app.main as main
from app.access import require_librarian
from app.request_security import MISSING_CSRF_TOKEN, csrf_submission
from app.routes.security_hardening import (
    _deployment_secret_warning,
    _harden_template_source,
    _nonce,
    _safe_diagnostic_event,
    _strip_member_export_paths,
)


def request_with(*, body: bytes = b"", content_type: str = "") -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    headers = []
    if content_type:
        headers.append((b"content-type", content_type.encode("latin-1")))
        headers.append((b"content-length", str(len(body)).encode("ascii")))
    return Request({
        "type": "http", "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": "/", "raw_path": b"/", "query_string": b"",
        "headers": headers, "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8787), "state": {},
    }, receive)


class CsrfRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_and_blank_csrf_submissions_fail_closed(self):
        missing, untouched = await csrf_submission(request_with())
        self.assertEqual(missing, MISSING_CSRF_TOKEN)
        self.assertIsNone(untouched)

        blank, body = await csrf_submission(request_with(
            body=b"csrf_token=",
            content_type="application/x-www-form-urlencoded",
        ))
        self.assertEqual(blank, MISSING_CSRF_TOKEN)
        self.assertEqual(body, b"csrf_token=")


class SecurityHardeningUnitTests(unittest.TestCase):
    def test_member_exports_keep_schema_but_remove_physical_paths(self):
        rows = [{
            "title": "Example", "source": "Movies",
            "source_path": "/srv/media/movies",
            "file_path": "/srv/media/movies/Example.mkv",
        }]
        member = _strip_member_export_paths(rows, is_librarian=False)
        self.assertEqual(member[0]["source_path"], "")
        self.assertEqual(member[0]["file_path"], "")
        self.assertEqual(member[0]["title"], "Example")
        librarian = _strip_member_export_paths(rows, is_librarian=True)
        self.assertEqual(librarian[0]["file_path"], rows[0]["file_path"])

    def test_remote_installation_without_application_secret_gets_warning(self):
        local = SimpleNamespace(
            sandbox=False, application_secret="", auth_mode="local",
            public_url="", trusted_hosts=(),
        )
        self.assertEqual(_deployment_secret_warning(local), "")
        remote = SimpleNamespace(
            sandbox=False, application_secret="", auth_mode="local",
            public_url="https://media.example.test", trusted_hosts=(),
        )
        self.assertIn("INFOMANCER_SECRET", _deployment_secret_warning(remote))
        cloudflare = SimpleNamespace(
            sandbox=False, application_secret="", auth_mode="cloudflare",
            public_url="", trusted_hosts=(),
        )
        self.assertIn("INFOMANCER_SECRET", _deployment_secret_warning(cloudflare))
        hardened = SimpleNamespace(
            sandbox=False, application_secret="configured-secret", auth_mode="cloudflare",
            public_url="https://media.example.test", trusted_hosts=(),
        )
        self.assertEqual(_deployment_secret_warning(hardened), "")

    def test_template_hardening_nonces_inline_blocks_and_adds_strict_csp(self):
        source = (
            "<!doctype html><html><head></head><body>"
            "<style>.x{display:block}</style>"
            "<script>window.example=true;</script>"
            "</body></html>"
        )
        hardened = _harden_template_source("example.html", source)
        self.assertIn('http-equiv="Content-Security-Policy"', hardened)
        self.assertIn("script-src 'self' 'nonce-{{ csp_nonce(request) }}'", hardened)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", hardened)
        self.assertIn('<script nonce="{{ csp_nonce(request) }}">', hardened)
        self.assertIn('<style nonce="{{ csp_nonce(request) }}">', hardened)
        self.assertIn("script-src-attr 'none'", hardened)

    def test_csp_nonce_is_stable_within_request_and_unique_between_requests(self):
        first = request_with()
        second = request_with()
        first_nonce = _nonce(first)
        self.assertTrue(first_nonce)
        self.assertEqual(_nonce(first), first_nonce)
        self.assertNotEqual(_nonce(second), first_nonce)

    def test_diagnostic_event_omits_raw_text_and_redacts_sensitive_context(self):
        raw = {
            "id": 7, "created_at": "2026-08-21T20:00:00+00:00",
            "level": "warning", "category": "media",
            "message": "Private Movie broken.mkv could not be read",
            "detail": "/srv/media/Private Movie/broken.mkv from 192.168.1.10",
            "user_name": "Private User",
            "context_json": json.dumps({
                "operation": "inspect", "status": "failed", "count": 2,
                "path": "/srv/media/Private Movie/broken.mkv",
                "ip_address": "192.168.1.10", "title": "Private Movie",
                "nested": {"filename": "broken.mkv", "attempt": 3},
            }),
        }
        safe = _safe_diagnostic_event(raw)
        self.assertNotIn("message", safe)
        self.assertNotIn("detail", safe)
        self.assertNotIn("user_name", safe)
        self.assertEqual(safe["context"]["operation"], "inspect")
        self.assertEqual(safe["context"]["status"], "failed")
        self.assertEqual(safe["context"]["count"], 2)
        self.assertEqual(safe["context"]["path"], "[redacted]")
        self.assertEqual(safe["context"]["ip_address"], "[redacted]")
        self.assertEqual(safe["context"]["title"], "[redacted]")
        self.assertEqual(safe["context"]["nested"]["filename"], "[redacted]")
        self.assertEqual(safe["context"]["nested"]["attempt"], 3)


class SensitiveReadAuthorizationTests(unittest.TestCase):
    def test_sensitive_get_namespaces_require_librarian(self):
        exact = {
            "/sources", "/duplicates", "/media-info/failures",
            "/api/task-failures", "/api/source-browser", "/api/source-preview",
        }
        prefixes = (
            "/settings", "/admin/", "/maintenance/", "/logs", "/api/logs",
            "/operations",
        )
        failures = []
        for route in main.app.routes:
            if "GET" not in (getattr(route, "methods", set()) or set()):
                continue
            path = getattr(route, "path", "")
            if path not in exact and not path.startswith(prefixes):
                continue
            dependencies = [item.call for item in route.dependant.dependencies]
            if require_librarian not in dependencies:
                failures.append(path)
        self.assertEqual(
            failures, [],
            "Sensitive GET routes missing an explicit Librarian dependency: "
            f"{sorted(set(failures))}",
        )


if __name__ == "__main__":
    unittest.main()

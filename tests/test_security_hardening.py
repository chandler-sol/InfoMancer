from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from starlette.requests import Request

from app.auth import request_ip, secure_cookie_for
from app.bootstrap import BootstrapTokenManager
from app.config import Settings
from app.request_security import (
    RequestBodyTooLarge, browser_request_is_same_origin, constant_time_equal,
    csrf_submission, host_is_allowed, should_issue_session_cookie,
)


def settings_for(path: Path, *, auth_mode: str = "local") -> Settings:
    return Settings(
        database=path / "test.db", tvdb_api_key="", tvdb_pin="",
        search_url_template="", media_browse_roots=(path,), auth_mode=auth_mode,
        session_days=14, cookie_secure="auto", cloudflare_team_domain="",
        cloudflare_audience="",
    )


def request_with(
    *, headers: dict[str, str] | None = None, client: str = "10.0.0.2",
    claims: dict | None = None, scheme: str = "http", body: bytes = b"",
) -> Request:
    encoded_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http", "http_version": "1.1", "method": "POST",
        "scheme": scheme, "path": "/", "raw_path": b"/", "query_string": b"",
        "headers": encoded_headers, "client": (client, 12345),
        "server": ("testserver", 80), "state": {},
    }
    request = Request(scope, receive)
    if claims is not None:
        request.state.external_claims = claims
    return request


class RequestSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_urlencoded_csrf_body_is_bounded_and_replayable(self):
        body = b"csrf_token=expected&value=ok"
        request = request_with(
            headers={
                "content-type": "application/x-www-form-urlencoded",
                "content-length": str(len(body)),
            },
            body=body,
        )
        token, buffered = await csrf_submission(request)
        self.assertEqual(token, "expected")
        self.assertEqual(buffered, body)

    async def test_large_urlencoded_form_is_rejected_before_buffering(self):
        request = request_with(
            headers={
                "content-type": "application/x-www-form-urlencoded",
                "content-length": str(3 * 1024 * 1024),
            },
        )
        with self.assertRaises(RequestBodyTooLarge):
            await csrf_submission(request)

    async def test_multipart_with_csrf_header_is_not_consumed(self):
        request = request_with(
            headers={
                "content-type": "multipart/form-data; boundary=example",
                "x-csrf-token": "header-token",
            },
            body=b"this body must stay unread",
        )
        token, buffered = await csrf_submission(request)
        self.assertEqual(token, "header-token")
        self.assertIsNone(buffered)
        message = await request.receive()
        self.assertEqual(message["body"], b"this body must stay unread")


class BootstrapTokenTests(unittest.TestCase):
    def test_generated_token_is_required_and_removed_after_setup(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bootstrap-token"
            manager = BootstrapTokenManager(path)
            token = manager.token()
            self.assertTrue(path.is_file())
            self.assertTrue(manager.verify(token))
            self.assertFalse(manager.verify("wrong-token"))
            manager.clear()
            self.assertFalse(path.exists())

    def test_non_ascii_token_is_rejected_without_type_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = BootstrapTokenManager(
                Path(temporary) / "bootstrap-token", "configured-secret"
            )
            self.assertFalse(manager.verify("é"))
            self.assertFalse(constant_time_equal("é", "configured-secret"))

    def test_configured_token_does_not_create_a_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bootstrap-token"
            manager = BootstrapTokenManager(path, "configured-secret")
            self.assertTrue(manager.verify("configured-secret"))
            self.assertFalse(path.exists())


class ForwardedHeaderTests(unittest.TestCase):
    def test_local_auth_ignores_forged_forwarded_headers(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = settings_for(Path(temporary), auth_mode="local")
            request = request_with(
                headers={
                    "cf-connecting-ip": "203.0.113.50",
                    "x-forwarded-for": "203.0.113.51",
                    "x-forwarded-proto": "https",
                },
                client="10.10.10.10",
            )
            self.assertEqual(request_ip(request, settings), "10.10.10.10")
            self.assertFalse(secure_cookie_for(request, settings))

    def test_https_public_url_does_not_break_plain_lan_cookie(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = settings_for(Path(temporary), auth_mode="local")
            settings = Settings(**{
                **settings.__dict__,
                "public_url": "https://media.example.test",
            })
            lan = request_with(headers={"host": "127.0.0.1:8787"}, scheme="http")
            public = request_with(headers={"host": "media.example.test"}, scheme="http")
            self.assertFalse(secure_cookie_for(lan, settings))
            self.assertTrue(secure_cookie_for(public, settings))

    def test_verified_cloudflare_request_can_use_cloudflare_headers(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = settings_for(Path(temporary), auth_mode="cloudflare")
            request = request_with(
                headers={
                    "cf-connecting-ip": "203.0.113.50",
                    "x-forwarded-proto": "https",
                },
                client="172.20.0.4", claims={"sub": "verified-user"},
            )
            self.assertEqual(request_ip(request, settings), "203.0.113.50")
            self.assertTrue(secure_cookie_for(request, settings))

    def test_local_auth_can_explicitly_trust_private_cloudflare_proxy(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = settings_for(Path(temporary), auth_mode="local")
            settings = Settings(**{
                **settings.__dict__,
                "public_url": "https://media.example.test",
                "trusted_hosts": ("media.example.test",),
                "trust_cloudflare_proxy": True,
            })
            request = request_with(
                headers={
                    "host": "media.example.test",
                    "cf-connecting-ip": "203.0.113.60",
                    "x-forwarded-proto": "https",
                },
                client="172.20.0.4",
            )
            self.assertEqual(request_ip(request, settings), "203.0.113.60")
            self.assertTrue(secure_cookie_for(request, settings))
            self.assertTrue(host_is_allowed(request, settings))

    def test_disabled_auth_rejects_untrusted_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = settings_for(Path(temporary), auth_mode="disabled")
            self.assertFalse(host_is_allowed(
                request_with(headers={"host": "attacker.example"}), settings
            ))
            self.assertTrue(host_is_allowed(
                request_with(headers={"host": "127.0.0.1:8787"}), settings
            ))
            self.assertFalse(host_is_allowed(
                request_with(headers={"host": "testserver"}), settings
            ))

    def test_disabled_mode_rejects_cross_site_browser_origin(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = settings_for(Path(temporary), auth_mode="disabled")
            cross_site = request_with(headers={
                "host": "127.0.0.1:8787",
                "origin": "https://attacker.example",
                "sec-fetch-site": "cross-site",
            })
            same_origin = request_with(headers={
                "host": "127.0.0.1:8787",
                "origin": "http://127.0.0.1:8787",
                "sec-fetch-site": "same-origin",
            })
            self.assertFalse(browser_request_is_same_origin(cross_site, settings))
            self.assertTrue(browser_request_is_same_origin(same_origin, settings))

    def test_invalid_cloudflare_ip_falls_back_to_socket_peer(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = settings_for(Path(temporary), auth_mode="cloudflare")
            request = request_with(
                headers={"cf-connecting-ip": "not-an-ip"},
                client="172.20.0.4", claims={"sub": "verified-user"},
            )
            self.assertEqual(request_ip(request, settings), "172.20.0.4")


class SessionIssuanceTests(unittest.TestCase):
    def test_cookie_less_api_requests_do_not_issue_database_sessions(self):
        self.assertFalse(should_issue_session_cookie("/api/tasks"))
        self.assertFalse(should_issue_session_cookie("/api/dashboard-metrics"))
        self.assertTrue(should_issue_session_cookie("/movies"))


class ContainerHardeningTests(unittest.TestCase):
    def test_docker_process_is_non_root_and_proxy_headers_are_disabled(self):
        dockerfile = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text(
            encoding="utf-8"
        )
        self.assertIn("USER infomancer", dockerfile)
        self.assertIn('"--no-proxy-headers"', dockerfile)
        self.assertNotIn('"--forwarded-allow-ips", "*"', dockerfile)
        self.assertIn("ARG INFOMANCER_UID=1000", dockerfile)
        self.assertIn("ARG INFOMANCER_GID=1000", dockerfile)
        self.assertIn('test "${INFOMANCER_UID}" != "0"', dockerfile)
        self.assertIn('test "${INFOMANCER_GID}" != "0"', dockerfile)
        compose = (Path(__file__).resolve().parent.parent / "compose.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("INFOMANCER_UID: ${INFOMANCER_UID:-1000}", compose)
        self.assertIn("INFOMANCER_GID: ${INFOMANCER_GID:-1000}", compose)


if __name__ == "__main__":
    unittest.main()

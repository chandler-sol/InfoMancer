from __future__ import annotations

import os
import re
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("INFOMANCER_AUTH_MODE", "disabled")

import app.main as main
from app.app_settings import AppSettings
from app.auth import AuthService
from app.db import Database
from app.engagement import EngagementService
from app.event_log import EventLog


class _HttpDuplicateProbe:
    def __init__(self) -> None:
        self.verify_calls: list[tuple[int, int, int]] = []
        self.decision_calls: list[tuple[int, int, str, int]] = []
        self.verify_called = threading.Event()

    def verify(self, left: int, right: int, user_id: int) -> str:
        self.verify_calls.append((left, right, user_id))
        self.verify_called.set()
        return "exact"

    def decide(self, left: int, right: int, action: str, user_id: int) -> bool:
        self.decision_calls.append((left, right, action, user_id))
        return True


class R501DuplicateHttpBindingTests(unittest.TestCase):
    """Exercise the focused duplicate routes through the assembled production app."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        settings = replace(
            main.settings,
            database=self.base / "r5-http.db",
            auth_mode="local",
            cookie_secure="false",
            sandbox=True,
            media_browse_roots=(self.base,),
        )
        database = Database(settings.database)
        database.initialize()

        self.original = (
            main.db,
            main.settings,
            main.auth_service,
            main.app_settings,
            main.engagement,
            main.event_log,
            main.duplicates,
            main.duplicate_verify_job,
            main.duplicate_verify_lock,
        )
        main.db = database
        main.settings = settings
        main.auth_service = AuthService(database, settings)
        main.app_settings = AppSettings(database, settings.search_url_template)
        main.engagement = EngagementService(database)
        main.event_log = EventLog(database)
        main.engagement.seed_official()
        self.duplicates = _HttpDuplicateProbe()
        main.duplicates = self.duplicates
        main.duplicate_verify_job = {"status": "idle"}
        main.duplicate_verify_lock = threading.Lock()
        self.database = database
        self.user = main.auth_service.create_user(
            "r5-librarian",
            "r5@example.com",
            "R5 Librarian",
            "x",
            role="librarian",
        )

        self.client = TestClient(main.app, follow_redirects=False)
        login = self.client.get("/login")
        token = re.search(
            r'name="preauth_token" value="([^"]+)', login.text
        ).group(1)
        signed_in = self.client.post(
            "/login",
            data={
                "preauth_token": token,
                "identity": "r5-librarian",
                "password": "x",
                "next": "/",
            },
        )
        self.assertEqual(signed_in.status_code, 303)
        session = main.auth_service.session_from_token(
            self.client.cookies["infomancer_session"]
        )
        self.assertIsNotNone(session)
        self.csrf = session.csrf_token

    def tearDown(self) -> None:
        self._wait_for_worker_tail()
        self.client.close()
        (
            main.db,
            main.settings,
            main.auth_service,
            main.app_settings,
            main.engagement,
            main.event_log,
            main.duplicates,
            main.duplicate_verify_job,
            main.duplicate_verify_lock,
        ) = self.original
        self.temporary.cleanup()

    def _wait_for_worker_tail(self) -> None:
        deadline = time.time() + 3
        while time.time() < deadline:
            with main.duplicate_verify_lock:
                status = main.duplicate_verify_job.get("status")
            if status not in {"starting", "running"}:
                return
            time.sleep(0.01)
        self.fail(f"duplicate worker did not finish: {main.duplicate_verify_job!r}")

    def test_single_verification_binds_request_through_real_http(self) -> None:
        response = self.client.post(
            "/duplicates/1/2/verify",
            data={"csrf_token": self.csrf},
        )
        self.assertEqual(response.status_code, 303, response.text)
        self.assertNotIn('"loc":["query","request"]', response.text)
        self.assertTrue(self.duplicates.verify_called.wait(timeout=2))
        self._wait_for_worker_tail()
        self.assertEqual(
            self.duplicates.verify_calls,
            [(1, 2, self.user.id)],
        )

    def test_bulk_verification_binds_request_through_real_http(self) -> None:
        response = self.client.post(
            "/duplicates/bulk-action",
            data={
                "csrf_token": self.csrf,
                "action": "verify",
                "pairs": ["3:4"],
            },
        )
        self.assertEqual(response.status_code, 303, response.text)
        self.assertNotIn('"loc":["query","request"]', response.text)
        self.assertTrue(self.duplicates.verify_called.wait(timeout=2))
        self._wait_for_worker_tail()
        self.assertEqual(
            self.duplicates.verify_calls,
            [(3, 4, self.user.id)],
        )

    def test_bulk_decision_binds_request_through_real_http(self) -> None:
        response = self.client.post(
            "/duplicates/bulk-action",
            data={
                "csrf_token": self.csrf,
                "action": "ignored",
                "pairs": ["5:6"],
            },
        )
        self.assertEqual(response.status_code, 303, response.text)
        self.assertNotIn('"loc":["query","request"]', response.text)
        self.assertEqual(
            self.duplicates.decision_calls,
            [(5, 6, "ignored", self.user.id)],
        )

    def test_registered_duplicate_routes_bind_request_as_framework_request(self) -> None:
        expected = {
            "/duplicates/bulk-action",
            "/duplicates/{file_a_id}/{file_b_id}/verify",
        }
        found = {}
        for route in main.app.routes:
            if getattr(route, "path", None) in expected and "POST" in (
                getattr(route, "methods", set()) or set()
            ):
                found[route.path] = route
        self.assertEqual(set(found), expected)
        for path, route in found.items():
            self.assertEqual(
                route.dependant.request_param_name,
                "request",
                f"{path} did not bind request as FastAPI Request",
            )
            self.assertNotIn(
                "request",
                [param.name for param in route.dependant.query_params],
                f"{path} incorrectly exposed request as a query parameter",
            )


if __name__ == "__main__":
    unittest.main()

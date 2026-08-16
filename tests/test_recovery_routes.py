import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("INFOMANCER_AUTH_MODE", "disabled")

import app.main as main
from app.app_settings import AppSettings
from app.auth import AuthService, SESSION_COOKIE
from app.db import Database
from app.engagement import EngagementService
from app.event_log import EventLog
from app.recovery_package import RecoveryPackageService


class RecoveryRouteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        settings = replace(
            main.settings,
            database=Path(self.temporary.name) / "recovery-route.db",
            auth_mode="local",
            cookie_secure="false",
            sandbox=True,
            media_browse_roots=(Path(self.temporary.name),),
        )
        database = Database(settings.database)
        database.initialize()
        self.original = (
            main.db, main.settings, main.auth_service, main.app_settings,
            main.engagement, main.event_log,
        )
        self.addCleanup(self._restore_globals)
        main.db, main.settings = database, settings
        main.auth_service = AuthService(database, settings)
        main.app_settings = AppSettings(database, settings.search_url_template)
        main.engagement = EngagementService(database)
        main.event_log = EventLog(database)
        main.auth_service.create_user(
            "recovery-admin", "recovery@example.com", "Recovery Admin", "x", role="librarian",
        )
        with database.connect() as conn:
            conn.execute(
                "INSERT INTO app_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("recovery_route_sentinel", "package-data"),
            )
        self.package = RecoveryPackageService(database.path, "0.8-route-test").create()
        self.client = TestClient(main.app, follow_redirects=False)
        self.addCleanup(self.client.close)
        login = self.client.get("/login")
        import re
        preauth = re.search(r'name="preauth_token" value="([^"]+)', login.text).group(1)
        signed_in = self.client.post("/login", data={
            "preauth_token": preauth,
            "identity": "recovery-admin",
            "password": "x",
            "next": "/settings/recovery",
        })
        self.assertEqual(signed_in.status_code, 303)

    def _restore_globals(self):
        (
            main.db, main.settings, main.auth_service, main.app_settings,
            main.engagement, main.event_log,
        ) = self.original

    def csrf_token(self) -> str:
        raw = self.client.cookies.get(SESSION_COOKIE)
        self.assertTrue(raw)
        session = main.auth_service.session_from_token(raw)
        self.assertIsNotNone(session)
        return session.csrf_token

    def test_recovery_page_is_librarian_accessible(self):
        response = self.client.get("/settings/recovery")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Restore an InfoMancer installation", response.text)
        self.assertIn("recovery-upload-form", response.text)

    def test_authenticated_multipart_preview_uses_header_csrf_and_verifies_package(self):
        response = self.client.post(
            "/settings/recovery/preview",
            headers={"X-CSRF-Token": self.csrf_token()},
            files={
                "recovery_file": (
                    self.package.name,
                    self.package.read_bytes(),
                    "application/octet-stream",
                )
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Verified restore preview", response.text)
        self.assertIn("0.8-route-test", response.text)
        self.assertIn("Package verified", response.text)
        staged = list((main.db.path.parent / "restore-staging").glob("*.infomancer-backup"))
        self.assertEqual(len(staged), 1)

    def test_authenticated_multipart_preview_without_header_is_rejected(self):
        response = self.client.post(
            "/settings/recovery/preview",
            files={
                "recovery_file": (
                    self.package.name,
                    self.package.read_bytes(),
                    "application/octet-stream",
                )
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("Request verification failed", response.text)


if __name__ == "__main__":
    unittest.main()

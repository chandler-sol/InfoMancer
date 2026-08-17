import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import app.main as main
from app.db import Database
from app.event_log import EventLog


class SecuritySurfaceTests(unittest.TestCase):
    def test_generated_fastapi_documentation_routes_are_disabled(self):
        routes = {getattr(route, "path", "") for route in main.app.routes}
        self.assertNotIn("/docs", routes)
        self.assertNotIn("/redoc", routes)
        self.assertNotIn("/openapi.json", routes)

    def test_multipart_helper_never_sends_csrf_to_another_origin(self):
        script = (Path(__file__).resolve().parents[1] / "app/static/multipart-submit.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("actionUrl.origin !== window.location.origin", script)
        self.assertIn("responseUrl.origin !== window.location.origin", script)
        self.assertIn('headers: {"X-CSRF-Token": csrfToken}', script)

    def test_lockout_notification_targets_first_active_librarian(self):
        original = main.db, main.app_settings, main.event_log
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "security.db")
            database.initialize()
            with database.connect() as conn:
                first_librarian = conn.execute(
                    """INSERT INTO users(username,display_name,role,password_hash)
                       VALUES ('firstadmin','First Admin','librarian','test')"""
                ).lastrowid
                conn.execute(
                    """INSERT INTO users(username,display_name,role,password_hash)
                       VALUES ('secondadmin','Second Admin','librarian','test')"""
                )
                member = conn.execute(
                    """INSERT INTO users(username,display_name,role,password_hash)
                       VALUES ('member','Member','member','test')"""
                ).lastrowid
            main.db = database
            main.app_settings = SimpleNamespace(get=lambda _key: "info")
            main.event_log = EventLog(database)
            try:
                main.record_security_event(
                    "Repeated sign-in attempts were blocked for Member.",
                    level="warning",
                    context={
                        "operation": "login_lockout",
                        "scope": "account_ip",
                        "ip_address": "192.0.2.55",
                    },
                    user_id=member, notify_librarian=True,
                )
                with database.connect() as conn:
                    rows = conn.execute(
                        """SELECT category,user_id,message FROM event_logs
                           ORDER BY id"""
                    ).fetchall()
                self.assertEqual([row["category"] for row in rows], [
                    "authentication", "library",
                ])
                self.assertEqual(rows[0]["user_id"], member)
                self.assertEqual(rows[1]["user_id"], first_librarian)
                activity = main.event_log.activity(first_librarian)
                self.assertEqual(len(activity), 1)
                self.assertEqual(activity[0]["href"], "/logs?category=authentication")
                self.assertTrue(activity[0]["unread"])
            finally:
                main.db, main.app_settings, main.event_log = original


if __name__ == "__main__":
    unittest.main()

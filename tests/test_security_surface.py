import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace

import app.main as main
from app.db import Database
from app.event_log import EventLog
from app.routes.account_avatar import PNG_SIGNATURE, _validate_canvas_png


def png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type)
    checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
    return (
        len(payload).to_bytes(4, "big")
        + chunk_type
        + payload
        + checksum.to_bytes(4, "big")
    )


def canvas_png(pixel_payload: bytes) -> bytes:
    ihdr = struct.pack(">IIBBBBB", 256, 256, 8, 6, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", zlib.compress(pixel_payload, level=9))
        + png_chunk(b"IEND", b"")
    )


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

    def test_title_detail_async_actions_preserve_csrf_and_same_origin_boundary(self):
        script = (Path(__file__).resolve().parents[1] / "app/static/title-detail-ux.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('headers.set("X-CSRF-Token", token)', script)
        self.assertIn("const sameOriginUrl =", script)
        self.assertIn("const requireSameOriginResponse =", script)
        self.assertIn("sameOriginUrl(response.url || window.location.href)", script)
        self.assertIn('csrfHeaders(form, {"X-InfoMancer-Async": "1"})', script)
        self.assertIn('csrfHeaders(form, {"X-Requested-With": "InfoMancerDialog"})', script)

    def test_avatar_png_validation_bounds_decompression_and_accepts_canvas_shape(self):
        expected_length = 256 * (1 + 256 * 4)
        valid = canvas_png(b"\x00" * expected_length)
        self.assertEqual(_validate_canvas_png(valid), "")

        oversized = canvas_png(b"\x00" * (expected_length + 1))
        self.assertIn("unexpected amount of pixel data", _validate_canvas_png(oversized))

        source = (Path(__file__).resolve().parents[1] / "app/routes/account_avatar.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("zlib.decompressobj()", source)
        self.assertIn("inflater.decompress(compressed, expected_length + 1)", source)

    def test_task_status_get_does_not_start_background_maintenance(self):
        operations = (Path(__file__).resolve().parents[1] / "app/routes/operations.py").read_text(
            encoding="utf-8"
        )
        start = operations.index('@router.get("/api/tasks")')
        end = operations.index('@librarian_get("/api/movie-match-analysis")')
        task_handler = operations[start:end]
        self.assertNotIn("maybe_start_scheduled_hashing", task_handler)
        self.assertNotIn("maybe_start_trash_cleanup", task_handler)
        self.assertNotIn("Â·", operations)

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

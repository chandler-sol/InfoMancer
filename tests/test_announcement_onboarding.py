from pathlib import Path
import tempfile
import unittest

from app.db import Database


class AnnouncementOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tempdir.name) / "infomancer.db")
        self.database.initialize()

    def tearDown(self):
        self.tempdir.cleanup()

    def _receipt_count(self) -> int:
        with self.database.connect() as conn:
            row = conn.execute("SELECT COUNT(*) count FROM announcement_receipts").fetchone()
            return int(row["count"])

    def test_historical_official_notice_is_seen_when_new_user_is_created(self):
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO announcements
                   (source,source_key,title,body,category,audience,starts_at)
                   VALUES ('official','old-release','Old release','Notes','update','all',
                           '2026-08-01 00:00:00')"""
            )
            conn.execute(
                """INSERT INTO users
                   (username,email,display_name,role,created_at)
                   VALUES ('new-user','new@example.test','New User','librarian',
                           '2026-08-23 12:00:00')"""
            )
        self.assertEqual(self._receipt_count(), 1)

    def test_historical_official_notice_is_seen_when_seeded_after_user(self):
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO users
                   (username,email,display_name,role,created_at)
                   VALUES ('new-user','new@example.test','New User','librarian',
                           '2026-08-23 12:00:00')"""
            )
            conn.execute(
                """INSERT INTO announcements
                   (source,source_key,title,body,category,audience,starts_at)
                   VALUES ('official','old-release','Old release','Notes','update','all',
                           '2026-08-01 00:00:00')"""
            )
        self.assertEqual(self._receipt_count(), 1)

    def test_newer_official_notice_remains_due_for_existing_user(self):
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO users
                   (username,email,display_name,role,created_at)
                   VALUES ('existing','existing@example.test','Existing User','librarian',
                           '2026-08-01 00:00:00')"""
            )
            conn.execute(
                """INSERT INTO announcements
                   (source,source_key,title,body,category,audience,starts_at)
                   VALUES ('official','new-release','New release','Notes','update','all',
                           '2026-08-23 00:00:00')"""
            )
        self.assertEqual(self._receipt_count(), 0)

    def test_installation_notice_is_not_silently_seen_for_new_user(self):
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO announcements
                   (source,title,body,category,audience,starts_at)
                   VALUES ('installation','Welcome','Read this','important','all',
                           '2026-08-01 00:00:00')"""
            )
            conn.execute(
                """INSERT INTO users
                   (username,email,display_name,role,created_at)
                   VALUES ('new-user','new@example.test','New User','librarian',
                           '2026-08-23 12:00:00')"""
            )
        self.assertEqual(self._receipt_count(), 0)

    def test_guided_setup_defers_popup_without_marking_it_seen(self):
        script = (Path(__file__).resolve().parents[1] / "app" / "static" / "engagement.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('window.location.pathname.startsWith("/getting-started/")', script)
        setup_guard = script.index('window.location.pathname.startsWith("/getting-started/")')
        remove_call = script.index("popup.remove();", setup_guard)
        return_call = script.index("return;", remove_call)
        seen_call = script.index("const seen =", return_call)
        self.assertLess(setup_guard, remove_call)
        self.assertLess(remove_call, return_call)
        self.assertLess(return_call, seen_call)


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.engagement import (
    OFFICIAL_ANNOUNCEMENTS, EngagementError, EngagementService,
)


class EngagementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "engagement.db")
        self.database.initialize()
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO users
                   (username,email,display_name,role,password_hash)
                   VALUES ('admin','admin@example.test','Admin','librarian','x')"""
            )
            conn.execute(
                """INSERT INTO users
                   (username,email,display_name,role,password_hash)
                   VALUES ('member','member@example.test','Member','member','x')"""
            )
        self.service = EngagementService(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def test_official_release_is_seeded_once_and_due_once(self):
        self.service.seed_official()
        self.service.seed_official()
        with self.database.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) count FROM announcements WHERE source='official'"
            ).fetchone()["count"]
        self.assertEqual(count, len(OFFICIAL_ANNOUNCEMENTS))
        for _ in OFFICIAL_ANNOUNCEMENTS:
            due = self.service.due(2, "member")
            self.assertIsNotNone(due)
            self.service.mark_seen(due["id"], 2, "member")
        self.assertIsNone(self.service.due(2, "member"))

    def test_since_0_4_release_notes_cover_current_safety_features(self):
        release = next(
            item for item in OFFICIAL_ANNOUNCEMENTS
            if item["source_key"] == "release-notes-since-0.4-2026-08-06"
        )
        self.assertIn("transparent category scores", release["body"])
        self.assertIn("SHA-256", release["body"])
        self.assertIn("never deletes media automatically", release["body"])

    def test_alpha_6_release_notes_document_installation_name_deprecation(self):
        release_notes = Path("docs/releases/0.6.0-alpha.1.md").read_text(encoding="utf-8")
        self.assertIn("Custom installation names are deprecated", release_notes)

    def test_member_announcement_audience_and_recurrence(self):
        announcement_id = self.service.create(
            "Server maintenance", "The library will be briefly unavailable.",
            "important", "members", "2020-01-01 00:00:00",
            "2099-01-01 00:00:00", 1, 1,
        )
        self.assertEqual(self.service.due(2, "member")["id"], announcement_id)
        self.assertIsNone(self.service.due(1, "librarian"))
        self.service.mark_seen(announcement_id, 2, "member")
        self.assertIsNone(self.service.due(2, "member"))
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE announcement_receipts SET last_seen_at='2020-01-02 00:00:00'
                   WHERE announcement_id=? AND user_id=2""",
                (announcement_id,),
            )
        self.assertEqual(self.service.due(2, "member")["id"], announcement_id)

    def test_mark_seen_rejects_wrong_audience_and_future_announcement(self):
        librarian_only = self.service.create(
            "Librarian note", "For librarians only.", "information", "librarians",
            "2020-01-01 00:00:00", "2099-01-01 00:00:00", None, 1,
        )
        with self.assertRaisesRegex(EngagementError, "not available"):
            self.service.mark_seen(librarian_only, 2, "member")
        self.service.mark_seen(librarian_only, 1, "librarian")

        future = self.service.create(
            "Future note", "Not visible yet.", "information", "all",
            "2098-01-01 00:00:00", "2099-01-01 00:00:00", None, 1,
        )
        with self.assertRaisesRegex(EngagementError, "not available"):
            self.service.mark_seen(future, 2, "member")

    def test_recurring_requires_end_and_official_cannot_be_disabled(self):
        with self.assertRaisesRegex(EngagementError, "need an end date"):
            self.service.create(
                "Reminder", "Remember this", "information", "all",
                "2020-01-01 00:00:00", None, 7, 1,
            )
        self.service.seed_official()
        with self.database.connect() as conn:
            official_id = conn.execute(
                "SELECT id FROM announcements WHERE source='official'"
            ).fetchone()["id"]
        with self.assertRaisesRegex(EngagementError, "cannot be disabled"):
            self.service.deactivate(official_id)

    def test_tour_state_can_be_dismissed_or_completed(self):
        self.assertTrue(self.service.tour_pending(2))
        self.service.set_tour_state(2, completed=False)
        self.assertFalse(self.service.tour_pending(2))
        self.service.set_tour_state(2, completed=True)
        self.assertFalse(self.service.tour_pending(2))

    def test_librarian_setup_choice_and_progress_are_persisted(self):
        self.assertTrue(self.service.setup_choice_pending(1, "librarian"))
        self.assertFalse(self.service.setup_choice_pending(2, "member"))
        self.service.begin_setup(1, "guided")
        self.assertFalse(self.service.setup_choice_pending(1, "librarian"))
        self.assertEqual(self.service.setup_state(1)["current_step"], "general")
        self.service.set_setup_step(1, "sources")
        self.assertEqual(self.service.setup_state(1)["current_step"], "sources")
        self.service.complete_setup(1)
        self.assertIsNotNone(self.service.setup_state(1)["completed_at"])

    def test_manual_setup_choice_is_immediately_complete(self):
        self.service.begin_setup(1, "manual")
        state = self.service.setup_state(1)
        self.assertEqual(state["mode"], "manual")
        self.assertIsNotNone(state["completed_at"])


if __name__ == "__main__":
    unittest.main()

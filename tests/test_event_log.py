import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.event_log import EventLog


class EventLogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "logs.db")
        self.database.initialize()
        self.logs = EventLog(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def test_structured_log_filters_and_hides_secret_context(self):
        self.logs.write(
            "metadata", "Lookup paused because the provider rate limit was reached.",
            level="warning", detail="HTTP 429", context={
                "title_id": 12, "api_key": "never-store-this",
            },
        )
        rows = self.logs.query(level="warning", category="metadata")
        self.assertEqual(len(rows), 1)
        self.assertIn("rate limit", rows[0]["message"])
        self.assertNotIn("never-store-this", rows[0]["context_json"])

    def test_new_schema_supports_media_and_personal_organization(self):
        with self.database.connect() as conn:
            file_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(files)")
            }
            tables = {
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertIn("runtime_seconds", file_columns)
        self.assertIn("dynamic_range", file_columns)
        self.assertIn("edition_name", file_columns)
        self.assertIn("version_name", file_columns)
        self.assertIn("version_preferred", file_columns)
        self.assertTrue(
            {
                "user_title_state", "user_tags", "title_tags", "event_logs",
                "user_search_history",
            } <= tables
        )

    def test_activity_reuses_events_with_per_account_read_state_and_links(self):
        with self.database.connect() as conn:
            user_id = conn.execute(
                """INSERT INTO users(username,display_name,role,password_hash)
                   VALUES ('reader','Reader','member','test')"""
            ).lastrowid
        self.logs.write(
            "mie", "A new finding needs review.",
            context={"finding_id": 42}, user_id=user_id,
        )
        activity = self.logs.activity(user_id)
        self.assertEqual(activity[0]["href"], "/library-health#finding-42")
        self.assertTrue(activity[0]["unread"])
        self.assertEqual(self.logs.unread_count(user_id), 1)
        self.assertEqual(self.logs.mark_read(user_id, [activity[0]["id"]]), 1)
        self.assertEqual(self.logs.unread_count(user_id), 0)


if __name__ == "__main__":
    unittest.main()

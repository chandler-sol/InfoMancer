from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.routes.release_081_announcements import (
    LEGACY_PACKAGED_OFFICIAL_KEYS,
    remove_legacy_packaged_announcements,
)


ROOT = Path(__file__).resolve().parents[1]


class Release081AnnouncementCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()

    def tearDown(self):
        self.temporary.cleanup()

    def test_legacy_packaged_notices_are_removed_without_touching_real_messages(self):
        legacy_key = LEGACY_PACKAGED_OFFICIAL_KEYS[0]
        with self.database.connect() as conn:
            user_id = int(conn.execute(
                """INSERT INTO users(username,display_name,role,password_hash,created_at)
                   VALUES ('librarian','Librarian','librarian','test','2020-01-01 00:00:00')"""
            ).lastrowid)
            legacy_id = int(conn.execute(
                """INSERT INTO announcements
                   (source,source_key,title,body,category,audience,starts_at)
                   VALUES ('official',?,'Old packaged release','Legacy','update','all',
                           '2026-08-01 00:00:00')""",
                (legacy_key,),
            ).lastrowid)
            future_id = int(conn.execute(
                """INSERT INTO announcements
                   (source,source_key,title,body,category,audience,starts_at)
                   VALUES ('official','future-official','Future release','Keep','update','all',
                           '2026-09-15 00:00:00')"""
            ).lastrowid)
            installation_id = int(conn.execute(
                """INSERT INTO announcements
                   (source,title,body,category,audience,starts_at,created_by)
                   VALUES ('installation','Local message','Keep','information','all',
                           '2026-08-01 00:00:00',?)""",
                (user_id,),
            ).lastrowid)
            conn.execute(
                "INSERT INTO announcement_receipts(announcement_id,user_id) VALUES (?,?)",
                (legacy_id, user_id),
            )

        removed = remove_legacy_packaged_announcements(self.database)
        self.assertEqual(removed, 1)
        with self.database.connect() as conn:
            remaining = {
                int(row["id"]): (row["source"], row["source_key"], row["title"])
                for row in conn.execute(
                    "SELECT id,source,source_key,title FROM announcements ORDER BY id"
                ).fetchall()
            }
            receipts = int(conn.execute(
                "SELECT COUNT(*) count FROM announcement_receipts WHERE announcement_id=?",
                (legacy_id,),
            ).fetchone()["count"])
        self.assertNotIn(legacy_id, remaining)
        self.assertIn(future_id, remaining)
        self.assertIn(installation_id, remaining)
        self.assertEqual(remaining[future_id][1], "future-official")
        self.assertEqual(remaining[installation_id][0], "installation")
        self.assertEqual(receipts, 0)

    def test_release_router_runs_cleanup_before_normal_domain_routes(self):
        routes = (ROOT / "app/routes/__init__.py").read_text(encoding="utf-8")
        self.assertIn("build_release_081_announcements_router", routes)
        self.assertLess(
            routes.index("build_release_081_announcements_router,"),
            routes.index("build_dashboard_router,"),
        )


class Release081SmartCollectionManagementTests(unittest.TestCase):
    def test_creator_explains_dynamic_membership_and_file_safety(self):
        collections = (ROOT / "app/templates/collections.html").read_text(encoding="utf-8")
        self.assertIn("Smart Collections fill themselves.", collections)
        self.assertIn("recalculates what belongs here as your catalog changes", collections)
        self.assertIn("Nothing is copied, moved, or deleted from your media library", collections)
        self.assertIn("edit or delete the Smart Collection later", collections)

    def test_smart_collection_detail_exposes_editor(self):
        detail = (ROOT / "app/templates/collection_detail.html").read_text(encoding="utf-8")
        self.assertIn("Edit Smart Collection", detail)
        self.assertIn('/collections/{{ collection.id }}/smart/edit', detail)
        self.assertIn("This Smart Collection currently has no matches", detail)
        self.assertNotIn("This cannot be undone.", detail)

    def test_smart_collection_editor_can_save_and_delete_safely(self):
        editor = (ROOT / "app/templates/smart_collection_edit.html").read_text(encoding="utf-8")
        self.assertIn('name="csrf_token" value="{{ csrf_token }}"', editor)
        self.assertIn('/collections/{{ collection.id }}/smart/edit', editor)
        self.assertIn("Delete Smart Collection", editor)
        self.assertIn('/collections/{{ collection.id }}/delete', editor)
        self.assertIn("media files remain untouched", editor)
        self.assertIn("offer an Undo on the Collections page", editor)

    def test_smart_collection_management_has_release_layout_hooks(self):
        css = (ROOT / "app/static/release-081-collections.css").read_text(encoding="utf-8")
        self.assertIn(".smart-collection-summary-copy", css)
        self.assertIn(".smart-collection-explainer", css)
        self.assertIn(".smart-collection-editor-form", css)
        self.assertIn(".smart-collection-danger", css)


if __name__ == "__main__":
    unittest.main()

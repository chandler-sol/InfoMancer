from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

os.environ.setdefault("INFOMANCER_AUTH_MODE", "disabled")

import app.main as main
from app.db import Database
from app.request_security import LOCAL_CSRF_COOKIE


ROOT = Path(__file__).resolve().parents[1]


class Release081SmartCollectionRouteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            self.collection_id = int(conn.execute(
                """INSERT INTO collections(name,description,collection_type,filter_json)
                   VALUES ('Recent 4K','Dynamic test collection','smart','{}')"""
            ).lastrowid)

        self.original_database = main.db
        main.db = self.database
        self.client = TestClient(main.app)
        self.client.get("/")
        csrf_token = self.client.cookies.get(LOCAL_CSRF_COOKIE)
        self.assertTrue(csrf_token)
        self.client.headers.update({"X-CSRF-Token": csrf_token})

    def tearDown(self):
        main.db = self.original_database
        self.temporary.cleanup()

    def test_smart_editor_receives_real_request_object_not_query_parameter(self):
        response = self.client.get(f"/collections/{self.collection_id}/smart/edit")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Edit Smart Collection", response.text)
        self.assertNotIn('"loc":["query","request"]', response.text)

    def test_collection_delete_and_undo_routes_receive_real_request_object(self):
        deleted = self.client.post(
            f"/collections/{self.collection_id}/delete",
            follow_redirects=False,
        )
        self.assertEqual(deleted.status_code, 303)
        parsed = urlparse(deleted.headers["location"])
        self.assertEqual(parsed.path, "/collections")
        operation_id = int(parse_qs(parsed.query)["undo_collection"][0])
        with self.database.connect() as conn:
            self.assertIsNone(conn.execute(
                "SELECT id FROM collections WHERE id=?", (self.collection_id,)
            ).fetchone())

        restored = self.client.post(
            f"/collections/deletions/{operation_id}/undo",
            follow_redirects=False,
        )
        self.assertEqual(restored.status_code, 303)
        self.assertEqual(urlparse(restored.headers["location"]).path, f"/collections/{self.collection_id}")
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT collection_type,filter_json FROM collections WHERE id=?",
                (self.collection_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["collection_type"], "smart")
        self.assertEqual(row["filter_json"], "{}")


class Release081CollectionPickerContractTests(unittest.TestCase):
    def test_picker_cards_reuse_library_cover_hover_action_menu(self):
        template = (ROOT / "app/templates/collections.html").read_text(encoding="utf-8")
        library_css = (ROOT / "app/static/library.css").read_text(encoding="utf-8")
        collection_css = (ROOT / "app/static/release-081-collections.css").read_text(encoding="utf-8")
        script = (ROOT / "app/static/release-081-collections.js").read_text(encoding="utf-8")

        self.assertIn('class="cover-card collection-picker-card"', template)
        self.assertIn('class="cover-card-link collection-card collection-picker-card-link"', template)
        self.assertIn('class="cover-card-actions collection-picker-card-actions"', template)
        self.assertIn("cover-row-menu item-action-menu collection-picker-menu", template)
        self.assertIn("Edit Smart Collection", template)
        self.assertIn("?action=add", template)
        self.assertIn("?action=edit", template)
        self.assertIn("?action=reorder", template)
        self.assertIn('/collections/{{ collection.id }}/delete', template)
        self.assertIn(".cover-card:hover .cover-card-actions", library_css)
        self.assertIn(".cover-card:hover .cover-row-menu", library_css)
        self.assertNotIn(".collection-picker-card:hover .collection-picker-card-actions", collection_css)
        self.assertIn("closePickerMenus", script)
        self.assertNotIn("library-hover-match", script)

    def test_picker_deep_links_reuse_existing_manual_collection_ui(self):
        script = (ROOT / "app/static/release-081-collection-polish.js").read_text(encoding="utf-8")
        self.assertIn('add: \'[data-collection-dialog-open="collection-add-dialog"]\'', script)
        self.assertIn('edit: \'[data-collection-dialog-open="collection-edit-dialog"]\'', script)
        self.assertIn("reorder: '[data-collection-reorder-toggle]'", script)
        self.assertIn("window.addEventListener('load'", script)

    def test_release_route_modules_import_request_at_module_scope(self):
        stabilization = (ROOT / "app/routes/release_081_stabilization.py").read_text(encoding="utf-8")
        deletion = (ROOT / "app/routes/release_081_collection_undo.py").read_text(encoding="utf-8")
        self.assertIn("from fastapi import APIRouter, Depends, Request", stabilization)
        self.assertIn("from fastapi import APIRouter, Depends, Request", deletion)
        self.assertNotIn('Request = ctx.get("Request")', stabilization)
        self.assertNotIn('Request = ctx.get("Request")', deletion)


if __name__ == "__main__":
    unittest.main()

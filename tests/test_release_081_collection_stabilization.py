from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import Request

from app.db import Database
from app.operation_history import OperationHistoryError
from app.routes.context import RouteContext
from app.routes.release_081_collection_undo import build_router as build_collection_undo_router
from app.routes.release_081_stabilization import network_safe_require_inside


class Release081CollectionUndoTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            self.user_id = int(conn.execute(
                """INSERT INTO users(username,display_name,role,password_hash)
                   VALUES ('librarian','Librarian','librarian','test')"""
            ).lastrowid)
            self.root_id = int(conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES (?,?,?)",
                (str(Path(self.temporary.name) / "tv"), "tv", "TV"),
            ).lastrowid)
            self.title_id = int(conn.execute(
                """INSERT INTO titles(root_id,kind,title,folder_path)
                   VALUES (?,?,?,?)""",
                (
                    self.root_id,
                    "tv",
                    "Example Show",
                    str(Path(self.temporary.name) / "tv" / "Example Show"),
                ),
            ).lastrowid)
            self.episode_id = int(conn.execute(
                """INSERT INTO expected_episodes(title_id,tvdb_episode_id,season,episode,name)
                   VALUES (?,?,?,?,?)""",
                (self.title_id, 1001, 1, 1, "Pilot"),
            ).lastrowid)

        events = []

        def redirect(path: str, message: str = ""):
            return {"path": path, "message": message}

        namespace = {
            "Request": Request,
            "db": self.database,
            "record_event": lambda *args, **kwargs: events.append((args, kwargs)),
            "redirect": redirect,
        }
        _, self.handlers = build_collection_undo_router(RouteContext(namespace))
        self.request = SimpleNamespace(
            state=SimpleNamespace(user=SimpleNamespace(id=self.user_id))
        )
        self.events = events

    def tearDown(self):
        self.temporary.cleanup()

    def test_smart_collection_delete_round_trip_restores_rules_and_identity(self):
        filters = '{"favorite":"yes","resolution":"2160"}'
        with self.database.connect() as conn:
            collection_id = int(conn.execute(
                """INSERT INTO collections(
                     name,description,artwork_filename,created_by,collection_type,filter_json
                   ) VALUES (?,?,?,?,?,?)""",
                (
                    "Favorite 4K",
                    "Auto-updating favorites",
                    "abc123.webp",
                    self.user_id,
                    "smart",
                    filters,
                ),
            ).lastrowid)

        deleted = self.handlers["delete_collection_with_undo"](self.request, collection_id)
        self.assertIn("undo_collection=", deleted["path"])
        operation_id = int(deleted["path"].split("undo_collection=", 1)[1])
        with self.database.connect() as conn:
            self.assertIsNone(conn.execute(
                "SELECT id FROM collections WHERE id=?", (collection_id,)
            ).fetchone())
            operation = conn.execute(
                "SELECT status,operation_type,undo_kind FROM operation_history WHERE id=?",
                (operation_id,),
            ).fetchone()
        self.assertEqual(operation["status"], "completed")
        self.assertEqual(operation["operation_type"], "collection_delete")
        self.assertIsNone(operation["undo_kind"])

        restored = self.handlers["undo_collection_delete"](self.request, operation_id)
        self.assertEqual(restored["path"], f"/collections/{collection_id}")
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT id,name,description,artwork_filename,created_by,
                          collection_type,filter_json
                   FROM collections WHERE id=?""",
                (collection_id,),
            ).fetchone()
            status = conn.execute(
                "SELECT status,undone_by FROM operation_history WHERE id=?",
                (operation_id,),
            ).fetchone()
        self.assertEqual(dict(row), {
            "id": collection_id,
            "name": "Favorite 4K",
            "description": "Auto-updating favorites",
            "artwork_filename": "abc123.webp",
            "created_by": self.user_id,
            "collection_type": "smart",
            "filter_json": filters,
        })
        self.assertEqual(status["status"], "undone")
        self.assertEqual(status["undone_by"], self.user_id)

    def test_manual_collection_delete_round_trip_restores_ordered_memberships(self):
        with self.database.connect() as conn:
            collection_id = int(conn.execute(
                "INSERT INTO collections(name,created_by) VALUES (?,?)",
                ("Timeline", self.user_id),
            ).lastrowid)
            conn.execute(
                """INSERT INTO collection_titles(collection_id,title_id,position)
                   VALUES (?,?,?)""",
                (collection_id, self.title_id, 4),
            )
            conn.execute(
                """INSERT INTO collection_episodes(collection_id,expected_episode_id,position)
                   VALUES (?,?,?)""",
                (collection_id, self.episode_id, 7),
            )

        deleted = self.handlers["delete_collection_with_undo"](self.request, collection_id)
        operation_id = int(deleted["path"].split("undo_collection=", 1)[1])
        self.handlers["undo_collection_delete"](self.request, operation_id)

        with self.database.connect() as conn:
            title = conn.execute(
                "SELECT title_id,position FROM collection_titles WHERE collection_id=?",
                (collection_id,),
            ).fetchone()
            episode = conn.execute(
                """SELECT expected_episode_id,position FROM collection_episodes
                   WHERE collection_id=?""",
                (collection_id,),
            ).fetchone()
        self.assertEqual((title["title_id"], title["position"]), (self.title_id, 4))
        self.assertEqual((episode["expected_episode_id"], episode["position"]), (self.episode_id, 7))


class Release081MappedDriveUndoTests(unittest.TestCase):
    def test_network_provider_realpath_failure_does_not_block_lexically_safe_path(self):
        with patch("pathlib.Path.resolve", side_effect=OSError("network provider refused realpath")):
            network_safe_require_inside(
                Path(r"X:\Plex\Example Show\episode.mkv"),
                Path(r"X:\Plex"),
            )

    def test_lexical_escape_is_rejected_before_optional_realpath(self):
        with patch("pathlib.Path.resolve", side_effect=OSError("should not matter")):
            with self.assertRaisesRegex(OperationHistoryError, "outside its configured source"):
                network_safe_require_inside(
                    Path(r"X:\Other\episode.mkv"),
                    Path(r"X:\Plex"),
                )


class Release081CollectionUiContractTests(unittest.TestCase):
    def test_smart_collection_layout_and_delete_undo_assets_are_loaded(self):
        root = Path(__file__).resolve().parents[1]
        collections = (root / "app/templates/collections.html").read_text(encoding="utf-8")
        css = (root / "app/static/release-081-collections.css").read_text(encoding="utf-8")
        undo_js = (root / "app/static/release-081-collections.js").read_text(encoding="utf-8")
        self.assertIn('class="panel smart-collection-create"', collections)
        self.assertNotIn('class="panel collection-create smart-collection-create"', collections)
        self.assertIn("grid-template-columns: repeat(3", css)
        self.assertIn("minmax(270px, 1fr)", css)
        self.assertIn("collection-undo-notice", css)
        self.assertIn("/collections/deletions/", undo_js)
        self.assertIn("undo_collection", undo_js)

    def test_collection_sort_polish_handles_spacing_and_overflow_without_markup_assumptions(self):
        root = Path(__file__).resolve().parents[1]
        css = (root / "app/static/release-081-collections.css").read_text(encoding="utf-8")
        script = (root / "app/static/release-081-collection-polish.js").read_text(encoding="utf-8")
        loader = (root / "app/static/workspace-ui.js").read_text(encoding="utf-8")
        self.assertIn("collection-sort-label", css)
        self.assertIn("collection-sort-overflow-scope", css)
        self.assertIn("Sort Titles", script)
        self.assertIn("release-081-collections.css", loader)
        self.assertIn("release-081-collection-polish.js", loader)

    def test_library_selection_exposes_bulk_add_to_collection(self):
        root = Path(__file__).resolve().parents[1]
        action = (root / "app/static/release-081-library-actions.js").read_text(encoding="utf-8")
        loader = (root / "app/static/workspace-ui.js").read_text(encoding="utf-8")
        picker = (root / "app/templates/bulk_title_collections.html").read_text(encoding="utf-8")
        self.assertIn("Add to Collection", action)
        self.assertIn("/titles/collections-bulk", action)
        self.assertIn("release-081-library-actions.js", loader)
        self.assertIn("Smart Collections remain rule-driven", picker)

    def test_release_routes_are_registered_before_operations_and_collections(self):
        root = Path(__file__).resolve().parents[1]
        routes = (root / "app/routes/__init__.py").read_text(encoding="utf-8")
        stabilization = routes.index("build_release_081_stabilization_router,")
        delete_undo = routes.index("build_release_081_collection_undo_router,")
        operations = routes.index("build_operations_router,")
        collections = routes.index("build_collections_router,")
        self.assertLess(stabilization, operations)
        self.assertLess(delete_undo, collections)


if __name__ == "__main__":
    unittest.main()

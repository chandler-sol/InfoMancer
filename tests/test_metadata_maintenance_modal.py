import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from app.db import Database


class MetadataMaintenanceModalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            root = conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES ('/movies','movie','Movies')"
            ).lastrowid
            self.stale_id = conn.execute(
                """INSERT INTO titles(root_id,kind,title,folder_path,imdb_id,tvdb_movie_id)
                   VALUES (?,'movie','Stale Movie','/movies/stale','tt0000001',77)""",
                (root,),
            ).lastrowid
            self.fresh_id = conn.execute(
                """INSERT INTO titles
                   (root_id,kind,title,folder_path,imdb_id,tvdb_movie_id,poster_url,metadata_refreshed_at)
                   VALUES (?,'movie','Fresh Movie','/movies/fresh','tt0000002',78,
                           'https://example.test/poster.jpg',CURRENT_TIMESTAMP)""",
                (root,),
            ).lastrowid
            conn.execute(
                """INSERT INTO title_credits
                   (title_id,imdb_person_id,person_name,role,billing_order)
                   VALUES (?,'nm0000001','Person One','actor',1)""",
                (self.fresh_id,),
            )
            conn.execute(
                """INSERT INTO metadata_refresh_queue(title_id,status,error)
                   VALUES (?,'failed','Provider unavailable')""",
                (self.stale_id,),
            )

        self.original_db = main.db
        main.db = self.database
        self.auth_patch = patch.object(
            main, "settings", replace(main.settings, auth_mode="disabled")
        )
        self.auth_patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        self.auth_patch.stop()
        main.db = self.original_db
        self.temporary.cleanup()

    def test_lazy_detail_api_filters_maintenance_scopes(self):
        stale = self.client.get("/api/metadata/maintenance", params={"scope": "stale"})
        fresh = self.client.get("/api/metadata/maintenance", params={"scope": "fresh"})
        artwork = self.client.get("/api/metadata/maintenance", params={"scope": "artwork"})
        credits = self.client.get("/api/metadata/maintenance", params={"scope": "credits"})
        failures = self.client.get("/api/metadata/maintenance", params={"scope": "failures"})

        self.assertEqual(stale.status_code, 200)
        self.assertEqual([item["id"] for item in stale.json()["items"]], [self.stale_id])
        self.assertEqual([item["id"] for item in fresh.json()["items"]], [self.fresh_id])
        self.assertEqual([item["id"] for item in artwork.json()["items"]], [self.stale_id])
        self.assertEqual([item["id"] for item in credits.json()["items"]], [self.stale_id])
        self.assertEqual([item["id"] for item in failures.json()["items"]], [self.stale_id])
        self.assertEqual(failures.json()["items"][0]["error"], "Provider unavailable")

    def test_detail_api_is_paginated_and_scope_is_bounded(self):
        page = self.client.get(
            "/api/metadata/maintenance",
            params={"scope": "stale", "limit": 1, "offset": 0},
        )
        fallback = self.client.get(
            "/api/metadata/maintenance", params={"scope": "not-a-scope"}
        )
        oversized = self.client.get(
            "/api/metadata/maintenance", params={"scope": "stale", "limit": 251}
        )

        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.json()["limit"], 1)
        self.assertEqual(page.json()["scope"], "stale")
        self.assertEqual(fallback.status_code, 200)
        self.assertEqual(fallback.json()["scope"], "stale")
        self.assertEqual(oversized.status_code, 422)

    def test_metadata_page_bootstraps_summary_first_modal_owner(self):
        bootstrap = (Path(__file__).resolve().parents[1] / "app/static/app-shell-bootstrap.js").read_text(encoding="utf-8")
        controller = (Path(__file__).resolve().parents[1] / "app/static/metadata-maintenance.js").read_text(encoding="utf-8")
        styles = (Path(__file__).resolve().parents[1] / "app/static/metadata-maintenance.css").read_text(encoding="utf-8")

        self.assertIn("metadata-maintenance-enhanced", bootstrap)
        self.assertIn("metadata-maintenance.css", bootstrap)
        self.assertIn("metadata-maintenance.js", bootstrap)
        self.assertIn("card.querySelector('.settings-table-wrap')?.remove()", controller)
        self.assertIn("View titles", controller)
        self.assertIn("Refresh all stale", controller)
        self.assertIn("/api/metadata/maintenance", controller)
        self.assertIn("/titles/${item.id}/imdb-refresh", controller)
        self.assertIn('form[action="/metadata/queue"]', styles)
        self.assertIn(".metadata-maintenance-dialog", styles)


if __name__ == "__main__":
    unittest.main()

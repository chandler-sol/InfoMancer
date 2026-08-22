import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from app.db import Database


class MetadataMaintenanceMatchedOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            root_id = conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES ('/movies','movie','Movies')"
            ).lastrowid
            self.matched_id = conn.execute(
                """INSERT INTO titles
                   (root_id,kind,title,folder_path,tvdb_movie_id,poster_url)
                   VALUES (?,'movie','Matched stale','/movies/matched',77,'')""",
                (root_id,),
            ).lastrowid
            self.unmatched_id = conn.execute(
                """INSERT INTO titles
                   (root_id,kind,title,folder_path,poster_url,metadata_refresh_error)
                   VALUES (?,'movie','Unmatched title','/movies/unmatched','',
                           'Old refresh error')""",
                (root_id,),
            ).lastrowid
            conn.execute(
                """INSERT INTO metadata_refresh_queue(title_id,status,error)
                   VALUES (?,'failed','Old refresh error')""",
                (self.unmatched_id,),
            )

        self.original_db = main.db
        main.db = self.database
        self.auth_patch = patch.object(
            main, "settings", replace(main.settings, auth_mode="disabled")
        )
        self.auth_patch.start()
        self.client = TestClient(main.app)
        self.client.get("/")
        csrf = self.client.cookies.get("infomancer_local_csrf")
        self.assertTrue(csrf)
        self.client.headers.update({"X-CSRF-Token": csrf})

    def tearDown(self):
        self.client.close()
        self.auth_patch.stop()
        main.db = self.original_db
        self.temporary.cleanup()

    def test_unmatched_titles_are_not_metadata_maintenance_states(self):
        for scope in ("stale", "artwork", "credits", "failures"):
            response = self.client.get(
                "/api/metadata/maintenance",
                params={"scope": scope, "limit": 100},
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            ids = {item["id"] for item in payload["items"]}
            self.assertNotIn(self.unmatched_id, ids, scope)

        stale = self.client.get(
            "/api/metadata/maintenance",
            params={"scope": "stale", "limit": 100},
        ).json()
        self.assertEqual(stale["total"], 1)
        self.assertEqual([item["id"] for item in stale["items"]], [self.matched_id])

    def test_bulk_stale_refresh_only_queues_matched_titles(self):
        captured = {}

        def fake_queue(title_ids, user_id, label):
            captured["title_ids"] = list(title_ids)
            captured["user_id"] = user_id
            captured["label"] = label
            return "Metadata refresh queued for 1 title(s)."

        with patch.object(main, "queue_metadata_refresh", side_effect=fake_queue):
            response = self.client.post(
                "/api/metadata/maintenance/bulk-refresh?scope=stale",
                headers={"Accept": "application/json", "X-InfoMancer-Async": "1"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["started"])
        self.assertEqual(captured["title_ids"], [self.matched_id])
        self.assertNotIn(self.unmatched_id, captured["title_ids"])

    def test_metadata_controller_lingers_success_and_syncs_matched_totals(self):
        root = Path(__file__).resolve().parents[1]
        controller = (root / "app/static/metadata-maintenance.js").read_text(
            encoding="utf-8"
        )
        route = (root / "app/routes/metadata_maintenance.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("const SUCCESS_LINGER_MS = 1250", controller)
        self.assertIn("await sleep(SUCCESS_LINGER_MS)", controller)
        self.assertIn("syncMetricTotals();", controller)
        self.assertIn("/api/metadata/maintenance/bulk-refresh", controller)
        self.assertIn("MATCHED_PREDICATE", route)
        self.assertIn("tvdb_movie_id IS NOT NULL", route)
        self.assertIn("tvdb_id IS NOT NULL", route)


if __name__ == "__main__":
    unittest.main()

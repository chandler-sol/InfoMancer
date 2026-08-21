import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from app.db import Database


class MetadataMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            root = conn.execute("INSERT INTO roots(path,kind,label) VALUES ('/movies','movie','Movies')").lastrowid
            self.title_id = conn.execute(
                "INSERT INTO titles(root_id,kind,title,folder_path,imdb_id) VALUES (?,'movie','Refresh Me','/movies/refresh','tt1')",
                (root,),
            ).lastrowid
        self.original_db = main.db
        main.db = self.database
        self.auth_patch = patch.object(main, "settings", replace(main.settings, auth_mode="disabled"))
        self.auth_patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        self.auth_patch.stop()
        with main.imdb_genre_lock:
            main.imdb_genre_job.clear()
        main.db = self.original_db
        self.temporary.cleanup()

    def test_selected_titles_enter_durable_incremental_queue(self):
        with patch.object(main, "start_scoped_imdb_sync", return_value=None) as starter:
            response = self.client.post(
                "/metadata/queue",
                data={"selected": str(self.title_id), "return_to": "/library"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        starter.assert_called_once()
        with self.database.connect() as conn:
            queued = conn.execute(
                "SELECT status FROM metadata_refresh_queue WHERE title_id=?", (self.title_id,)
            ).fetchone()
        self.assertEqual(queued["status"], "queued")

    def test_metadata_dashboard_reports_freshness_and_failures(self):
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO metadata_refresh_queue(title_id,status,error) VALUES (?,'failed','Provider unavailable')",
                (self.title_id,),
            )
        page = self.client.get("/settings/metadata")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Metadata maintenance", page.text)
        self.assertIn("Provider unavailable", page.text)

    def test_title_scoped_refresh_marks_shared_worker_as_local_ui(self):
        def fake_queue(title_ids, _user_id, label):
            with main.imdb_genre_lock:
                main.imdb_genre_job.clear()
                main.imdb_genre_job.update({
                    "status": "starting",
                    "title_ids": list(title_ids),
                    "scope_label": label,
                })
            return "Metadata refresh queued."

        with patch.object(main, "queue_metadata_refresh", side_effect=fake_queue):
            response = self.client.post(
                f"/titles/{self.title_id}/imdb-refresh",
                headers={"Accept": "application/json", "X-InfoMancer-Async": "1"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ui_scope"], "local")
        with main.imdb_genre_lock:
            self.assertEqual(main.imdb_genre_job.get("ui_scope"), "local")
            self.assertEqual(main.imdb_genre_job.get("ui_title_id"), self.title_id)

    def test_local_metadata_failure_is_not_a_global_task_notification(self):
        with main.imdb_genre_lock:
            main.imdb_genre_job.clear()
            main.imdb_genre_job.update({
                "status": "failed",
                "error": "Provider unavailable",
                "ui_scope": "local",
                "ui_title_id": self.title_id,
            })
        response = self.client.get("/api/task-failures")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("imdb-metadata", {item["id"] for item in response.json()["failures"]})

    def test_metadata_controller_keeps_scope_and_refresh_state_across_view_switches(self):
        root = Path(__file__).resolve().parents[1]
        controller = (root / "app/static/metadata-maintenance.js").read_text(encoding="utf-8")
        styles = (root / "app/static/metadata-maintenance.css").read_text(encoding="utf-8")
        task_widget = (root / "app/static/task-widget.js").read_text(encoding="utf-8")
        modern = (root / "app/static/modern.css").read_text(encoding="utf-8")

        self.assertIn("const scopeCache = new Map", controller)
        self.assertIn("const refreshJobs = new Map()", controller)
        self.assertIn("prefetchOtherScopes", controller)
        self.assertIn("Progress will stay with this title even if you switch maintenance views.", controller)
        self.assertNotIn("if (!row.isConnected) return", controller)
        self.assertNotIn("metric.title =", controller)
        self.assertIn("metadata-maintenance-inline-task", controller)
        self.assertIn("metadata-refresh-state", controller)
        self.assertIn("metricDescriptions", controller)
        self.assertIn("Refreshed within the last 30 days.", controller)

        self.assertIn("height:min(78vh,760px)", styles)
        self.assertIn(".metadata-maintenance-list{min-height:0;overflow:auto", styles)
        self.assertIn("metadata-maintenance-inline-task.working", styles)
        self.assertIn("metadata-maintenance-metric>strong", styles)

        self.assertIn("const localOnlyTask =", task_widget)
        self.assertIn("Refreshing metadata for ", task_widget)

        self.assertIn('dialog button[aria-label^="Close"]', modern)
        self.assertIn('content: "×"', modern)
        self.assertIn("place-items: center !important", modern)


if __name__ == "__main__":
    unittest.main()

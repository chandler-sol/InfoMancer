import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from app.db import Database


class ImmediateThread:
    def __init__(self, *, target, args=(), kwargs=None, daemon=None):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.daemon = daemon

    def start(self):
        self.target(*self.args, **self.kwargs)


class FakeTVDB:
    api_key = "test-key"
    pin = ""

    def movie(self, movie_id: int):
        if movie_id != 77:
            raise AssertionError(f"unexpected TVDB movie id {movie_id}")
        return {
            "id": 77,
            "name": "Refresh Me Updated",
            "_default_name": "Refresh Me Updated",
            "_english_translation": {
                "name": "Refresh Me Updated",
                "overview": "Updated overview",
            },
            "overview": "Updated overview",
            "year": "2006",
            "image": "https://art.example/poster.jpg",
            "genres": [{"name": "Action"}, {"name": "Drama"}],
            "remoteIds": [
                {"sourceName": "IMDB", "id": "tt0123456"},
                {"sourceName": "TheMovieDB.com", "id": "1234"},
            ],
            "status": {"name": "Released"},
            "characters": [
                {"peopleId": 10, "personName": "Actor One", "peopleType": "Actor", "sort": 1},
                {"peopleId": 20, "personName": "Director One", "peopleType": "Director", "sort": 1},
                {"peopleId": 30, "personName": "Writer One", "peopleType": "Writer", "sort": 1},
            ],
        }


class MetadataMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            root = conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES ('/movies','movie','Movies')"
            ).lastrowid
            self.title_id = conn.execute(
                """INSERT INTO titles(root_id,kind,title,folder_path,imdb_id)
                   VALUES (?,'movie','Refresh Me','/movies/refresh','tt1')""",
                (root,),
            ).lastrowid
        self.original_db = main.db
        self.original_tvdb = main.tvdb
        main.db = self.database
        self.auth_patch = patch.object(
            main, "settings", replace(main.settings, auth_mode="disabled")
        )
        self.auth_patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        self.auth_patch.stop()
        with main.imdb_genre_lock:
            main.imdb_genre_job.clear()
        main.tvdb = self.original_tvdb
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
                """INSERT INTO metadata_refresh_queue(title_id,status,error)
                   VALUES (?,'failed','Provider unavailable')""",
                (self.title_id,),
            )
        page = self.client.get("/settings/metadata")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Metadata maintenance", page.text)
        self.assertIn("Provider unavailable", page.text)

    def test_title_scoped_refresh_uses_targeted_tvdb_not_bulk_imdb_queue(self):
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE titles SET tvdb_movie_id=77,poster_url='',overview='' WHERE id=?",
                (self.title_id,),
            )
        main.tvdb = FakeTVDB()

        with patch("app.routes.title_metadata_async.threading.Thread", ImmediateThread), \
             patch.object(main, "queue_metadata_refresh") as bulk_queue:
            response = self.client.post(
                f"/titles/{self.title_id}/imdb-refresh",
                headers={"Accept": "application/json", "X-InfoMancer-Async": "1"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["started"])
        self.assertEqual(response.json()["ui_scope"], "local")
        bulk_queue.assert_not_called()

        with self.database.connect() as conn:
            title = conn.execute(
                """SELECT metadata_title,metadata_title_language,metadata_year,
                          metadata_status,metadata_provider,metadata_refreshed_at,
                          metadata_refresh_error,poster_url,overview,genres,imdb_id,tmdb_id
                   FROM titles WHERE id=?""",
                (self.title_id,),
            ).fetchone()
            queue = conn.execute(
                "SELECT status,provider,error FROM metadata_refresh_queue WHERE title_id=?",
                (self.title_id,),
            ).fetchone()
            credits = conn.execute(
                """SELECT imdb_person_id,person_name,role FROM title_credits
                   WHERE title_id=? ORDER BY role,person_name""",
                (self.title_id,),
            ).fetchall()

        self.assertEqual(title["metadata_title"], "Refresh Me Updated")
        self.assertEqual(title["metadata_title_language"], "eng")
        self.assertEqual(title["metadata_year"], 2006)
        self.assertEqual(title["metadata_status"], "Released")
        self.assertEqual(title["metadata_provider"], "TVDB")
        self.assertTrue(title["metadata_refreshed_at"])
        self.assertEqual(title["metadata_refresh_error"], "")
        self.assertEqual(title["poster_url"], "https://art.example/poster.jpg")
        self.assertEqual(title["overview"], "Updated overview")
        self.assertEqual(title["genres"], "Action,Drama")
        self.assertEqual(title["imdb_id"], "tt0123456")
        self.assertEqual(title["tmdb_id"], "1234")
        self.assertEqual(queue["status"], "complete")
        self.assertEqual(queue["provider"], "TVDB")
        self.assertEqual(queue["error"], "")
        self.assertEqual(
            [(row["imdb_person_id"], row["person_name"], row["role"]) for row in credits],
            [
                ("tvdb:10", "Actor One", "actor"),
                ("tvdb:20", "Director One", "director"),
                ("tvdb:30", "Writer One", "writer"),
            ],
        )
        with main.imdb_genre_lock:
            self.assertEqual(main.imdb_genre_job.get("status"), "complete")
            self.assertEqual(main.imdb_genre_job.get("ui_scope"), "local")
            self.assertEqual(main.imdb_genre_job.get("ui_title_id"), self.title_id)

    def test_quick_refresh_requires_existing_tvdb_match(self):
        main.tvdb = FakeTVDB()
        response = self.client.post(
            f"/titles/{self.title_id}/imdb-refresh",
            headers={"Accept": "application/json", "X-InfoMancer-Async": "1"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("TVDB match", response.json()["detail"])

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
        self.assertNotIn(
            "imdb-metadata", {item["id"] for item in response.json()["failures"]}
        )

    def test_metadata_controller_keeps_scope_and_refresh_state_across_view_switches(self):
        root = Path(__file__).resolve().parents[1]
        controller = (root / "app/static/metadata-maintenance.js").read_text(encoding="utf-8")
        styles = (root / "app/static/metadata-maintenance.css").read_text(encoding="utf-8")
        task_widget = (root / "app/static/task-widget.js").read_text(encoding="utf-8")
        modern = (root / "app/static/modern.css").read_text(encoding="utf-8")
        title_route = (root / "app/routes/title_metadata_async.py").read_text(encoding="utf-8")

        self.assertIn("const scopeCache = new Map", controller)
        self.assertIn("const refreshJobs = new Map()", controller)
        self.assertIn("prefetchOtherScopes", controller)
        self.assertIn(
            "Progress will stay with this title even if you switch maintenance views.",
            controller,
        )
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

        self.assertIn("run_targeted_refresh", title_route)
        self.assertIn("tvdb.movie", title_route)
        self.assertIn("tvdb.series", title_route)
        self.assertNotIn("queue_metadata_refresh =", title_route)

        self.assertIn('dialog button[aria-label^="Close"]', modern)
        self.assertIn('content: "×"', modern)
        self.assertIn("place-items: center !important", modern)


if __name__ == "__main__":
    unittest.main()

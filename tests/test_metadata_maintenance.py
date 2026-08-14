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


if __name__ == "__main__":
    unittest.main()

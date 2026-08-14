import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from app.db import Database
from app.duplicates import DuplicateService
from app.editions import EditionVersionService, infer_edition, infer_version
from app.mie import MediaIntelligenceEngine


class EditionVersionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO users(id,username,display_name,role,password_hash)
                   VALUES (0,'disabled','Disabled','librarian','')"""
            )
            root_id = conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES ('/movies','movie','Movies')"
            ).lastrowid
            self.title_id = conn.execute(
                """INSERT INTO titles(root_id,kind,title,folder_path)
                   VALUES (?,'movie','Example Movie','/movies/example')""", (root_id,),
            ).lastrowid
            conn.executemany(
                """INSERT INTO files(
                     title_id,path,filename,extension,size_bytes,modified_at,
                     width,height,dynamic_range,seen_scan
                   ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [
                    (self.title_id, "/movies/example/extended-2160p-remux.mkv",
                     "Example.Movie.Extended.2160p.REMUX.mkv", ".mkv", 20, 1,
                     3840, 2160, "HDR10", "scan"),
                    (self.title_id, "/movies/example/theatrical-1080p.mkv",
                     "Example.Movie.Theatrical.1080p.mkv", ".mkv", 10, 1,
                     1920, 1080, "SDR", "scan"),
                ],
            )
            self.file_ids = [row["id"] for row in conn.execute("SELECT id FROM files ORDER BY id")]
        self.originals = main.db, main.duplicates, main.edition_versions, main.mie
        main.db = self.database
        main.duplicates = DuplicateService(self.database)
        main.edition_versions = EditionVersionService(self.database)
        main.mie = MediaIntelligenceEngine(self.database)
        self.settings_patch = patch.object(
            main, "settings", replace(main.settings, auth_mode="disabled")
        )
        self.event_patch = patch.object(main, "record_event")
        self.settings_patch.start()
        self.event_patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        self.event_patch.stop()
        self.settings_patch.stop()
        main.db, main.duplicates, main.edition_versions, main.mie = self.originals
        self.temporary.cleanup()

    def test_filename_and_media_details_produce_reviewable_suggestions(self):
        self.assertEqual(
            infer_edition("Movie.Directors.Cut.2160p.mkv"), "Director's Cut"
        )
        self.assertEqual(
            infer_version({
                "filename": "Movie.2160p.REMUX.mkv", "width": 3840,
                "height": 2160, "dynamic_range": "HDR10",
            }),
            "4K HDR10 REMUX",
        )
        page = self.client.get(f"/files/{self.file_ids[0]}/edition-version")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Extended Edition", page.text)
        self.assertIn("4K HDR10 REMUX", page.text)
        with self.database.connect() as conn:
            self.assertEqual(
                conn.execute("SELECT identity_confirmed FROM files WHERE id=?", (self.file_ids[0],)).fetchone()[0],
                0,
            )

    def test_movie_detail_exposes_edition_editor_for_each_file(self):
        page = self.client.get(f"/titles/{self.title_id}")

        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.text.count("movie-detail-menu item-action-menu"), 1)
        self.assertNotIn("movie-file-menu", page.text)
        self.assertIn('class="detail-technical-rail"', page.text)
        self.assertIn('class="dossier-on-disk"', page.text)
        self.assertIn("4K UHD", page.text)
        self.assertIn("HDR10", page.text)
        for file_id in self.file_ids:
            self.assertIn(f'/files/{file_id}/edition-version', page.text)

    def test_preview_and_typed_confirmation_precede_catalog_change(self):
        preview = self.client.post(
            f"/files/{self.file_ids[0]}/edition-version/preview",
            data={"edition_name": "Extended Edition", "version_name": "4K HDR", "preferred": "1"},
        )
        self.assertEqual(preview.status_code, 200)
        self.assertIn("Preview proposed changes", preview.text)
        with self.database.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT edition_name FROM files WHERE id=?", (self.file_ids[0],)
            ).fetchone()[0], "")

        rejected = self.client.post(
            f"/files/{self.file_ids[0]}/edition-version",
            data={"edition_name": "Extended Edition", "version_name": "4K HDR", "preferred": "1", "confirm": "NO"},
            follow_redirects=False,
        )
        self.assertEqual(rejected.status_code, 303)
        with self.database.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT identity_confirmed FROM files WHERE id=?", (self.file_ids[0],)
            ).fetchone()[0], 0)

    def test_confirmed_different_identities_become_intentional_alternatives(self):
        first, second = self.file_ids
        self.client.post(
            f"/files/{first}/edition-version",
            data={"edition_name": "Extended Edition", "version_name": "4K HDR", "preferred": "1", "confirm": "SAVE"},
        )
        self.client.post(
            f"/files/{second}/edition-version",
            data={"edition_name": "Theatrical Cut", "version_name": "1080p", "confirm": "SAVE"},
        )
        with self.database.connect() as conn:
            files = conn.execute(
                "SELECT id,version_preferred FROM files ORDER BY id"
            ).fetchall()
            review = conn.execute("SELECT * FROM duplicate_reviews").fetchone()
        self.assertEqual([row["version_preferred"] for row in files], [1, 0])
        self.assertEqual(review["decision"], "not_duplicate")
        self.assertEqual(review["review_source"], "edition_version")
        self.assertEqual(main.duplicates.candidates(), [])

        detail = self.client.get(f"/titles/{self.title_id}")
        self.assertIn("Extended Edition", detail.text)
        self.assertIn("Preferred", detail.text)
        self.assertIn("Edit Edition &amp; Version", detail.text)

        self.client.post(
            f"/files/{second}/edition-version",
            data={"edition_name": "Extended Edition", "version_name": "4K HDR", "confirm": "SAVE"},
        )
        with self.database.connect() as conn:
            restored = conn.execute("SELECT * FROM duplicate_reviews").fetchone()
        self.assertEqual(restored["decision"], "active")

    def test_manual_alternative_decision_remains_manually_owned(self):
        first, second = self.file_ids
        main.duplicates.decide(first, second, "not_duplicate", 0)
        self.client.post(
            f"/files/{first}/edition-version",
            data={"edition_name": "Extended Edition", "version_name": "4K", "confirm": "SAVE"},
        )
        self.client.post(
            f"/files/{second}/edition-version",
            data={"edition_name": "Theatrical Cut", "version_name": "1080p", "confirm": "SAVE"},
        )
        with self.database.connect() as conn:
            review = conn.execute("SELECT * FROM duplicate_reviews").fetchone()
        self.assertEqual(review["decision"], "not_duplicate")
        self.assertEqual(review["review_source"], "manual")

    def test_library_health_requests_identity_review_until_alternatives_are_confirmed(self):
        main.mie.analyze()
        self.assertIn(
            "media-identity-unreviewed",
            {finding["rule_key"] for finding in main.mie.findings()},
        )


if __name__ == "__main__":
    unittest.main()

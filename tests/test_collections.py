import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("INFOMANCER_AUTH_MODE", "disabled")

import app.main as main
from app.db import Database


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.database = Database(self.base / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            root_id = conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES ('/media','movie','Movies')"
            ).lastrowid
            self.first_id = conn.execute(
                """INSERT INTO titles(root_id,kind,title,year,folder_path,poster_url)
                   VALUES (?,?,?,?,?,?)""",
                (root_id, "movie", "First Movie", 2020, "/media/first.mkv",
                 "https://example.test/first.jpg"),
            ).lastrowid
            self.second_id = conn.execute(
                """INSERT INTO titles(root_id,kind,title,year,folder_path)
                   VALUES (?,?,?,?,?)""",
                (root_id, "movie", "Second Movie", 2021, "/media/second.mkv"),
            ).lastrowid
            tv_root_id = conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES ('/tv','tv','TV')"
            ).lastrowid
            self.show_id = conn.execute(
                """INSERT INTO titles(root_id,kind,title,year,folder_path)
                   VALUES (?,?,?,?,?)""",
                (tv_root_id, "tv", "Timeline Show", 2022, "/tv/timeline-show"),
            ).lastrowid
            self.file_id = conn.execute(
                """INSERT INTO files(
                     title_id,path,filename,extension,season,episode_start,episode_end,seen_scan
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    self.show_id, "/tv/timeline-show/S01E01-E02.mkv",
                    "S01E01-E02.mkv", ".mkv", 1, 1, 2, "test",
                ),
            ).lastrowid
            self.episode_ids = []
            for number, name in ((1, "The Beginning"), (2, "The Crossover")):
                self.episode_ids.append(conn.execute(
                    """INSERT INTO expected_episodes
                       (title_id,tvdb_episode_id,season,episode,name)
                       VALUES (?,?,?,?,?)""",
                    (self.show_id, 1000 + number, 1, number, name),
                ).lastrowid)
        self.original_database = main.db
        self.original_art_dir = main.COLLECTION_ART_DIR
        main.db = self.database
        main.COLLECTION_ART_DIR = self.base / "collection-art"
        main.COLLECTION_ART_DIR.mkdir()
        self.client = TestClient(main.app)

    def tearDown(self):
        main.db = self.original_database
        main.COLLECTION_ART_DIR = self.original_art_dir
        self.temporary.cleanup()

    def test_collection_stays_separate_and_preserves_manual_order(self):
        created = self.client.post(
            "/collections",
            data={"name": "Weekend Shelf", "description": "Shared picks"},
            follow_redirects=False,
        )
        self.assertEqual(created.status_code, 303)
        collection_id = int(created.headers["location"].split("/")[2].split("?")[0])
        for title_id in (self.first_id, self.second_id):
            response = self.client.post(
                f"/collections/{collection_id}/titles",
                data={"title_id": title_id},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)

        detail = self.client.get(f"/collections/{collection_id}")
        self.assertLess(detail.text.index("First Movie"), detail.text.index("Second Movie"))
        moved = self.client.post(
            f"/collections/{collection_id}/titles/{self.second_id}/move",
            data={"direction": "up"},
            follow_redirects=False,
        )
        self.assertEqual(moved.status_code, 303)
        reordered = self.client.get(f"/collections/{collection_id}")
        self.assertLess(reordered.text.index("Second Movie"), reordered.text.index("First Movie"))

        library = self.client.get("/library")
        self.assertNotIn("Weekend Shelf", library.text)
        self.assertIn("First Movie", library.text)

    def test_collection_accepts_image_artwork_and_rejects_unknown_files(self):
        created = self.client.post(
            "/collections", data={"name": "Artwork Test"}, follow_redirects=False
        )
        collection_id = int(created.headers["location"].split("/")[2].split("?")[0])
        png = b"\x89PNG\r\n\x1a\n" + b"test-image"
        saved = self.client.post(
            f"/collections/{collection_id}/edit",
            data={"name": "Artwork Test", "description": ""},
            files={"artwork": ("cover.png", png, "image/png")},
            follow_redirects=False,
        )
        self.assertEqual(saved.status_code, 303)
        with self.database.connect() as conn:
            filename = conn.execute(
                "SELECT artwork_filename FROM collections WHERE id=?",
                (collection_id,),
            ).fetchone()["artwork_filename"]
        self.assertTrue((main.COLLECTION_ART_DIR / filename).is_file())
        image_response = self.client.get(f"/collections/art/{filename}")
        self.assertEqual(image_response.status_code, 200)

        rejected = self.client.post(
            f"/collections/{collection_id}/edit",
            data={"name": "Artwork Test", "description": ""},
            files={"artwork": ("cover.svg", b"<svg></svg>", "image/svg+xml")},
            follow_redirects=False,
        )
        self.assertEqual(rejected.status_code, 303)
        self.assertIn("could+not+recognize", rejected.headers["location"])

    def test_collection_can_mix_movies_series_and_logical_episodes(self):
        created = self.client.post(
            "/collections", data={"name": "Story Timeline"}, follow_redirects=False
        )
        collection_id = int(created.headers["location"].split("/")[2].split("?")[0])
        self.client.post(
            f"/collections/{collection_id}/titles",
            data={"title_id": self.first_id},
            follow_redirects=False,
        )
        chooser = self.client.get(f"/files/{self.file_id}/collections")
        self.assertEqual(chooser.status_code, 200)
        self.assertIn("The Beginning", chooser.text)
        self.assertIn("The Crossover", chooser.text)
        saved = self.client.post(
            f"/files/{self.file_id}/collections",
            data={
                "assignments": [
                    f"{self.episode_ids[0]}:{collection_id}",
                    f"{self.episode_ids[1]}:{collection_id}",
                ]
            },
            follow_redirects=False,
        )
        self.assertEqual(saved.status_code, 303)
        detail = self.client.get(f"/collections/{collection_id}")
        self.assertLess(detail.text.index("First Movie"), detail.text.index("The Beginning"))
        self.assertLess(detail.text.index("The Beginning"), detail.text.index("The Crossover"))
        self.assertIn("S01E01", detail.text)
        moved = self.client.post(
            f"/collections/{collection_id}/episodes/{self.episode_ids[0]}/move",
            data={"direction": "up"},
            follow_redirects=False,
        )
        self.assertEqual(moved.status_code, 303)
        reordered = self.client.get(f"/collections/{collection_id}")
        self.assertLess(reordered.text.index("The Beginning"), reordered.text.index("First Movie"))

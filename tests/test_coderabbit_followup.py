from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.background import BackgroundCoordinator
from app.db import Database
from app.title_metadata import TitleMetadataService


class _HashSettings:
    def get(self, key: str) -> str:
        return {
            "hash_pause_for_activity": "0",
            "hash_io_intensity": "balanced",
        }.get(key, "")


class _FailingHashes:
    def hash_many(self, *_args, **_kwargs):
        raise RuntimeError("synthetic hashing failure")


class BackgroundFailureTests(unittest.TestCase):
    def test_hash_exception_publishes_terminal_error_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "catalog.db")
            database.initialize()
            events = []
            coordinator = BackgroundCoordinator(
                database, _HashSettings(), _FailingHashes(), object(),
                lambda *args, **kwargs: events.append((args, kwargs)),
            )
            coordinator.run_media_hashing([1], "test")
            self.assertEqual(coordinator.media_hash_job["status"], "error")
            self.assertEqual(coordinator.media_hash_job["current"], "")
            self.assertIn("synthetic hashing failure", coordinator.media_hash_job["error"])
            self.assertTrue(events)
            self.assertEqual(events[-1][1]["level"], "error")


class _TVDB:
    def series(self, _series_id):
        return {"id": 77, "name": "Recovered Provider Title", "overview": "Existing overview"}


class TitleMetadataFollowupTests(unittest.TestCase):
    def test_missing_provider_title_prevents_false_complete_short_circuit(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "catalog.db")
            database.initialize()
            with database.connect() as conn:
                root_id = conn.execute(
                    "INSERT INTO roots(path,kind,label) VALUES ('/tv','tv','TV')"
                ).lastrowid
                title_id = conn.execute(
                    """INSERT INTO titles(
                       root_id,kind,title,folder_path,tvdb_id,poster_url,imdb_id,
                       metadata_title,metadata_title_language,overview
                       ) VALUES (?,'tv','Example','/tv/example',77,'poster','tt0000077',
                                 '','eng','Existing overview')""",
                    (root_id,),
                ).lastrowid
            service = TitleMetadataService(
                database, _TVDB(),
                poster_from=lambda _series: "poster",
                plex_movie_ids=lambda _series: ("", "tt0000077"),
                localized_title=lambda _series, _current: ("Recovered Provider Title", "eng"),
                match_confidence=lambda *_args: {},
            )
            self.assertTrue(service.enrich(title_id))
            with database.connect() as conn:
                title = conn.execute(
                    "SELECT metadata_title FROM titles WHERE id=?", (title_id,)
                ).fetchone()
            self.assertEqual(title["metadata_title"], "Recovered Provider Title")


if __name__ == "__main__":
    unittest.main()

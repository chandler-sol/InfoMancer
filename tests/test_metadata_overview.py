from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.title_metadata import TitleMetadataService
from app.tvdb import TVDBClient


class MetadataOverviewTests(unittest.TestCase):
    def test_tvdb_movie_promotes_english_translation_overview(self):
        client = TVDBClient("test-key")

        def fake_get(path, params=None, *, allow_not_found=False):
            if path == "/movies/77/extended":
                return {"data": {"id": 77, "name": "Base title", "overview": ""}}
            if path == "/movies/77/translations/eng":
                return {
                    "data": {
                        "name": "English title",
                        "overview": "The translated synopsis.",
                    }
                }
            raise AssertionError(path)

        client._get = fake_get
        movie = client.movie(77)

        self.assertEqual(movie["name"], "English title")
        self.assertEqual(movie["overview"], "The translated synopsis.")
        self.assertEqual(movie["_default_name"], "Base title")

    def test_movie_enrichment_retries_without_year_and_uses_external_id(self):
        class FakeTVDB:
            def __init__(self):
                self.searches = []

            def search_movies(self, query, year=None):
                self.searches.append((query, year))
                if year is not None:
                    return []
                return [{"id": 77, "name": "Example Movie", "year": "2019"}]

            def movie(self, movie_id):
                self.asserted_movie_id = movie_id
                return {
                    "id": movie_id,
                    "overview": "Recovered from the provider record.",
                    "remoteIds": [
                        {"sourceName": "TheMovieDB.com", "id": "500"},
                    ],
                }

        def plex_movie_ids(record):
            tmdb_id = imdb_id = ""
            for remote in record.get("remoteIds") or []:
                source = str(remote.get("sourceName") or "").lower()
                remote_id = str(remote.get("id") or "")
                if "themoviedb" in source:
                    tmdb_id = remote_id
                if "imdb" in source:
                    imdb_id = remote_id
            return tmdb_id, imdb_id

        def confidence(_title, _year, _candidate):
            return {"exact_title": True, "exact_year": False}

        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            with database.connect() as conn:
                root_id = conn.execute(
                    "INSERT INTO roots(path,kind,label) VALUES (?,?,?)",
                    (str(Path(directory) / "movies"), "movie", "Movies"),
                ).lastrowid
                title_id = conn.execute(
                    """INSERT INTO titles(
                           root_id,kind,title,year,folder_path,metadata_title,
                           metadata_year,tmdb_id
                       ) VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        root_id, "movie", "Example Movie", 2020,
                        str(Path(directory) / "movies" / "Example Movie (2020)"),
                        "Example Movie", 2020, "500",
                    ),
                ).lastrowid

            fake = FakeTVDB()
            service = TitleMetadataService(
                database, fake,
                poster_from=lambda _record: "",
                plex_movie_ids=plex_movie_ids,
                localized_title=lambda _record, current=None: (current or "", "eng"),
                match_confidence=confidence,
            )

            self.assertTrue(service.enrich(title_id))
            self.assertEqual(
                fake.searches,
                [("Example Movie", 2020), ("Example Movie", None)],
            )
            with database.connect() as conn:
                title = conn.execute(
                    "SELECT overview,tvdb_movie_id FROM titles WHERE id=?",
                    (title_id,),
                ).fetchone()
            self.assertEqual(title["overview"], "Recovered from the provider record.")
            self.assertEqual(title["tvdb_movie_id"], 77)


if __name__ == "__main__":
    unittest.main()

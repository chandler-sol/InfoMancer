import gzip
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from app.db import Database
from app.imdb import sync_genres


class IMDbGenreTests(unittest.TestCase):
    def test_sync_can_refresh_one_title_without_replacing_other_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "catalog.db")
            database.initialize()
            with database.connect() as conn:
                root_id = conn.execute(
                    "INSERT INTO roots(path, kind, label) VALUES ('/movies', 'movie', 'Movies')"
                ).lastrowid
                selected_id = conn.execute(
                    """INSERT INTO titles
                       (root_id, kind, title, folder_path, imdb_id)
                       VALUES (?, 'movie', 'Selected', '/movies/Selected', 'tt0000001')""",
                    (root_id,),
                ).lastrowid
                untouched_id = conn.execute(
                    """INSERT INTO titles
                       (root_id, kind, title, folder_path, imdb_id, genres)
                       VALUES (?, 'movie', 'Untouched', '/movies/Untouched',
                               'tt0000002', 'Existing')""",
                    (root_id,),
                ).lastrowid
                conn.execute(
                    """INSERT INTO title_credits
                       (title_id, imdb_person_id, person_name, role, billing_order)
                       VALUES (?, 'nm-old', 'Existing Person', 'actor', 1)""",
                    (untouched_id,),
                )

            basics = (
                "tconst\ttitleType\tprimaryTitle\toriginalTitle\tisAdult\tstartYear\t"
                "endYear\truntimeMinutes\tgenres\n"
                "tt0000001\tmovie\tSelected\tSelected\t0\t2001\t\\N\t100\tDrama\n"
                "tt0000002\tmovie\tUntouched\tUntouched\t0\t2002\t\\N\t100\tComedy\n"
            )
            ratings = "tconst\taverageRating\tnumVotes\ntt0000001\t7.0\t100\n"
            crew = "tconst\tdirectors\twriters\ntt0000001\t\\N\t\\N\n"
            principals = (
                "tconst\tordering\tnconst\tcategory\tjob\tcharacters\n"
                "tt0000001\t1\tnm0000001\tactor\t\\N\t[\"Lead\"]\n"
            )
            names = (
                "nconst\tprimaryName\tbirthYear\tdeathYear\tprimaryProfession\tknownForTitles\n"
                "nm0000001\tSelected Lead\t\\N\t\\N\tactor\tt0000001\n"
            )
            responses = [
                BytesIO(gzip.compress(value.encode("utf-8")))
                for value in (basics, ratings, crew, principals, names)
            ]
            with patch("app.imdb.urlopen", side_effect=responses):
                sync_genres(database, title_ids=[selected_id])

            with database.connect() as conn:
                selected = conn.execute(
                    "SELECT genres FROM titles WHERE id=?", (selected_id,)
                ).fetchone()
                untouched = conn.execute(
                    "SELECT genres FROM titles WHERE id=?", (untouched_id,)
                ).fetchone()
                untouched_credit = conn.execute(
                    "SELECT person_name FROM title_credits WHERE title_id=?",
                    (untouched_id,),
                ).fetchone()
            self.assertEqual(selected["genres"], "Drama")
            self.assertEqual(untouched["genres"], "Existing")
            self.assertEqual(untouched_credit["person_name"], "Existing Person")

    def test_sync_keeps_only_genres_for_saved_imdb_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "catalog.db")
            database.initialize()
            with database.connect() as conn:
                root_id = conn.execute(
                    "INSERT INTO roots(path, kind, label) VALUES ('/tv', 'tv', 'TV')"
                ).lastrowid
                title_id = conn.execute(
                    """INSERT INTO titles
                       (root_id, kind, title, folder_path, imdb_id)
                       VALUES (?, 'tv', 'Example', '/tv/Example', 'tt0000002')""",
                    (root_id,),
                ).lastrowid
                expected_id = conn.execute(
                    """INSERT INTO expected_episodes
                       (title_id, tvdb_episode_id, season, episode, name)
                       VALUES (?, 100, 1, 1, 'Pilot')""",
                    (title_id,),
                ).lastrowid

            content = (
                "tconst\ttitleType\tprimaryTitle\toriginalTitle\tisAdult\tstartYear\t"
                "endYear\truntimeMinutes\tgenres\n"
                "tt0000001\tmovie\tOther\tOther\t0\t2000\t\\N\t90\tDrama\n"
                "tt0000002\ttvSeries\tExample\tExample\t0\t2001\t2004\t30\tComedy,Romance\n"
            )
            ratings = (
                "tconst\taverageRating\tnumVotes\n"
                "tt0000002\t8.3\t1234\n"
            )
            episodes = (
                "tconst\tparentTconst\tseasonNumber\tepisodeNumber\n"
                "tt0000100\ttt0000002\t1\t1\n"
            )
            crew = (
                "tconst\tdirectors\twriters\n"
                "tt0000002\t\\N\t\\N\n"
                "tt0000100\tnm0000001\tnm0000002\n"
            )
            principals = (
                "tconst\tordering\tnconst\tcategory\tjob\tcharacters\n"
                "tt0000002\t1\tnm0000003\tactor\t\\N\t[\"Lead\"]\n"
            )
            names = (
                "nconst\tprimaryName\tbirthYear\tdeathYear\tprimaryProfession\tknownForTitles\n"
                "nm0000001\tEpisode Director\t\\N\t\\N\tdirector\tt0000100\n"
                "nm0000002\tEpisode Writer\t\\N\t\\N\twriter\tt0000100\n"
                "nm0000003\tSeries Lead\t\\N\t\\N\tactor\tt0000002\n"
            )
            responses = [
                BytesIO(gzip.compress(value.encode("utf-8")))
                for value in (content, ratings, episodes, crew, principals, names)
            ]
            with patch("app.imdb.urlopen", side_effect=responses):
                result = sync_genres(database)

            self.assertEqual(result["requested"], 1)
            self.assertEqual(result["matched"], 1)
            self.assertEqual(result["ratings_matched"], 1)
            with database.connect() as conn:
                metadata = conn.execute(
                    """SELECT genres, imdb_title_type, imdb_rating, imdb_votes
                       FROM titles WHERE imdb_id='tt0000002'"""
                ).fetchone()
                title_credit = conn.execute(
                    "SELECT person_name, role FROM title_credits WHERE title_id=?",
                    (title_id,),
                ).fetchone()
                episode_credits = conn.execute(
                    """SELECT person_name, role FROM episode_credits
                       WHERE expected_episode_id=? ORDER BY role""",
                    (expected_id,),
                ).fetchall()
            self.assertEqual(metadata["genres"], "Comedy,Romance")
            self.assertEqual(metadata["imdb_title_type"], "tvSeries")
            self.assertEqual(metadata["imdb_rating"], 8.3)
            self.assertEqual(metadata["imdb_votes"], 1234)
            self.assertEqual((title_credit["person_name"], title_credit["role"]), ("Series Lead", "actor"))
            self.assertEqual(
                [(row["person_name"], row["role"]) for row in episode_credits],
                [("Episode Director", "director"), ("Episode Writer", "writer")],
            )

    def test_sync_stores_only_credits_for_matched_movies(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "catalog.db")
            database.initialize()
            with database.connect() as conn:
                root_id = conn.execute(
                    "INSERT INTO roots(path, kind, label) VALUES ('/movies', 'movie', 'Movies')"
                ).lastrowid
                title_id = conn.execute(
                    """INSERT INTO titles
                       (root_id, kind, title, folder_path, imdb_id)
                       VALUES (?, 'movie', 'Example Movie', '/movies/Example', 'tt0000002')""",
                    (root_id,),
                ).lastrowid

            basics = (
                "tconst\ttitleType\tprimaryTitle\toriginalTitle\tisAdult\tstartYear\t"
                "endYear\truntimeMinutes\tgenres\n"
                "tt0000002\tmovie\tExample Movie\tExample Movie\t0\t2001\t\\N\t100\tDrama\n"
            )
            ratings = "tconst\taverageRating\tnumVotes\ntt0000002\t7.1\t500\n"
            crew = (
                "tconst\tdirectors\twriters\n"
                "tt0000002\tnm0000001\tnm0000002,nm0000003\n"
            )
            principals = (
                "tconst\tordering\tnconst\tcategory\tjob\tcharacters\n"
                "tt0000002\t1\tnm0000004\tactor\t\\N\t[\"Lead\"]\n"
                "tt0000002\t2\tnm0000005\tactress\t\\N\t[\"Second\"]\n"
                "tt0000002\t3\tnm0000004\tactor\t\\N\t[\"Lead\"]\n"
            )
            names = (
                "nconst\tprimaryName\tbirthYear\tdeathYear\tprimaryProfession\tknownForTitles\n"
                "nm0000001\tDirector One\t\\N\t\\N\tdirector\ttt0000002\n"
                "nm0000002\tWriter One\t\\N\t\\N\twriter\ttt0000002\n"
                "nm0000003\tWriter Two\t\\N\t\\N\twriter\ttt0000002\n"
                "nm0000004\tLead Actor\t\\N\t\\N\tactor\ttt0000002\n"
                "nm0000005\tSecond Actor\t\\N\t\\N\tactress\ttt0000002\n"
            )
            responses = [
                BytesIO(gzip.compress(value.encode("utf-8")))
                for value in (basics, ratings, crew, principals, names)
            ]
            with patch("app.imdb.urlopen", side_effect=responses):
                result = sync_genres(database)

            self.assertEqual(result["credits_matched"], 1)
            with database.connect() as conn:
                credits = conn.execute(
                    """SELECT person_name, role, billing_order FROM title_credits
                       WHERE title_id=? ORDER BY role, billing_order""",
                    (title_id,),
                ).fetchall()
            self.assertEqual(
                [(row["person_name"], row["role"], row["billing_order"]) for row in credits],
                [
                    ("Lead Actor", "actor", 1),
                    ("Second Actor", "actor", 2),
                    ("Director One", "director", 1),
                    ("Writer One", "writer", 1),
                    ("Writer Two", "writer", 2),
                ],
            )


if __name__ == "__main__":
    unittest.main()

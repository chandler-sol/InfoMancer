import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("INFOMANCER_AUTH_MODE", "disabled")

import app.main as main
from app.db import Database
from app.main import (
    broader_movie_queries, broader_series_queries, localized_tvdb_title,
    match_confidence,
)


class MovieCreditViewTests(unittest.TestCase):
    def test_tvdb_title_localization_prefers_english_and_preserves_cached_title(self):
        translated = {
            "name": "今際の国のアリス",
            "_default_name": "今際の国のアリス",
            "_english_translation": {
                "language": "eng", "name": "Alice in Borderland",
                "aliases": ["Alice in Borderland"],
            },
            "aliases": ["Imawa no Kuni no Arisu"],
        }
        self.assertEqual(
            localized_tvdb_title(translated, "Existing title"),
            ("Alice in Borderland", "eng"),
        )
        untranslated = {
            "name": "今際の国のアリス",
            "_default_name": "今際の国のアリス",
            "_english_translation": {},
            "aliases": ["Alice in Borderland"],
        }
        self.assertEqual(
            localized_tvdb_title(untranslated, "Alice in Borderland"),
            ("Alice in Borderland", ""),
        )
        self.assertEqual(
            localized_tvdb_title(untranslated),
            ("今際の国のアリス", "default"),
        )
        self.assertEqual(
            localized_tvdb_title({
                "name": "", "_english_translation": {},
                "aliases": ["Alias fallback"],
            }),
            ("Alias fallback", "alias"),
        )

    def test_cover_picker_prioritizes_english_and_updates_without_rematching(self):
        class FakeTVDB:
            api_key = "test"

            def series(self, series_id):
                return {
                    "id": series_id,
                    "image": "https://art.example/default.jpg",
                    "artworks": [
                        {
                            "type": 2, "language": "spa", "score": 9,
                            "image": "https://art.example/spanish.jpg",
                        },
                        {
                            "type": 2, "language": "eng", "score": 5,
                            "image": "https://art.example/english.jpg",
                        },
                    ],
                }

        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "catalog.db")
            database.initialize()
            with database.connect() as conn:
                root_id = conn.execute(
                    "INSERT INTO roots(path, kind, label) VALUES ('/tv', 'tv', 'TV')"
                ).lastrowid
                title_id = conn.execute(
                    """INSERT INTO titles
                       (root_id, kind, title, folder_path, tvdb_id, poster_url)
                       VALUES (?, 'tv', 'Example Show', '/tv/Example Show', 100, ?)""",
                    (root_id, "https://art.example/spanish.jpg"),
                ).lastrowid

            original_database = main.db
            original_tvdb = main.tvdb
            main.db = database
            main.tvdb = FakeTVDB()
            try:
                client = TestClient(main.app)
                picker = client.get(f"/titles/{title_id}/cover")
                response = client.post(
                    f"/titles/{title_id}/cover",
                    data={"poster_url": "https://art.example/english.jpg"},
                    follow_redirects=False,
                )
            finally:
                main.db = original_database
                main.tvdb = original_tvdb

            self.assertEqual(picker.status_code, 200)
            self.assertLess(
                picker.text.index("https://art.example/english.jpg"),
                picker.text.index("https://art.example/spanish.jpg"),
            )
            self.assertIn("English", picker.text)
            self.assertEqual(response.status_code, 303)
            with database.connect() as conn:
                title = conn.execute(
                    "SELECT tvdb_id, poster_url FROM titles WHERE id=?", (title_id,)
                ).fetchone()
            self.assertEqual(title["tvdb_id"], 100)
            self.assertEqual(title["poster_url"], "https://art.example/english.jpg")

    def test_episode_rename_applies_only_selected_ready_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            season = base / "TV" / "Example Show" / "Season 01"
            season.mkdir(parents=True)
            first_source = season / "Example Show - S01E01 - Old One.mkv"
            second_source = season / "Example Show - S01E02 - Old Two.mkv"
            first_source.write_bytes(b"first")
            second_source.write_bytes(b"second")
            database = Database(base / "catalog.db")
            database.initialize()
            with database.connect() as conn:
                root_id = conn.execute(
                    "INSERT INTO roots(path, kind, label) VALUES (?, 'tv', 'TV')",
                    (str(base / "TV"),),
                ).lastrowid
                title_id = conn.execute(
                    """INSERT INTO titles
                       (root_id, kind, title, folder_path, tvdb_id, metadata_title)
                       VALUES (?, 'tv', 'Example Show', ?, 100, 'Example Show')""",
                    (root_id, str(season.parent)),
                ).lastrowid
                conn.executemany(
                    """INSERT INTO expected_episodes
                       (title_id, tvdb_episode_id, season, episode, name)
                       VALUES (?, ?, 1, ?, ?)""",
                    [(title_id, 101, 1, "Pilot"), (title_id, 102, 2, "Second")],
                )
                first_id = conn.execute(
                    """INSERT INTO files
                       (title_id, path, filename, extension, size_bytes,
                        season, episode_start, seen_scan)
                       VALUES (?, ?, ?, '.mkv', 5, 1, 1, 'test')""",
                    (title_id, str(first_source), first_source.name),
                ).lastrowid
                second_id = conn.execute(
                    """INSERT INTO files
                       (title_id, path, filename, extension, size_bytes,
                        season, episode_start, seen_scan)
                       VALUES (?, ?, ?, '.mkv', 6, 1, 2, 'test')""",
                    (title_id, str(second_source), second_source.name),
                ).lastrowid

            original_database = main.db
            main.db = database
            try:
                client = TestClient(main.app)
                preview = client.get(f"/titles/{title_id}/rename-episodes")
                response = client.post(
                    f"/titles/{title_id}/rename-episodes",
                    data={"selected_file_ids": str(first_id)},
                    follow_redirects=False,
                )
            finally:
                main.db = original_database

            self.assertIn(f'value="{first_id}"', preview.text)
            self.assertIn(f'value="{second_id}"', preview.text)
            self.assertEqual(response.status_code, 303)
            with database.connect() as conn:
                first = conn.execute("SELECT filename FROM files WHERE id=?", (first_id,)).fetchone()
                second = conn.execute("SELECT filename FROM files WHERE id=?", (second_id,)).fetchone()
            self.assertEqual(first["filename"], "Example Show - S01E01 - Pilot.mkv")
            self.assertEqual(second["filename"], second_source.name)
            self.assertTrue((season / first["filename"]).exists())
            self.assertTrue(second_source.exists())

    def test_manual_tvdb_series_link_resolves_and_loads_match(self):
        class FakeTVDB:
            def search_series(self, query):
                self.query = query
                return [{"id": "series-416491", "tvdb_id": "416491", "slug": "1923"}]

            def series(self, series_id):
                return {"id": series_id, "name": "1923", "firstAired": "2022-12-18"}

            def episodes(self, series_id):
                return [{"id": 1, "seasonNumber": 1, "number": 1, "name": "1923", "aired": "2022-12-18"}]

        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "catalog.db")
            database.initialize()
            with database.connect() as conn:
                root_id = conn.execute(
                    "INSERT INTO roots(path, kind, label) VALUES ('/tv', 'tv', 'TV')"
                ).lastrowid
                title_id = conn.execute(
                    """INSERT INTO titles (root_id, kind, title, folder_path)
                       VALUES (?, 'tv', '1923', '/tv/1923')""",
                    (root_id,),
                ).lastrowid

            original_database = main.db
            original_tvdb = main.tvdb
            fake_tvdb = FakeTVDB()
            main.db = database
            main.tvdb = fake_tvdb
            try:
                search_response = TestClient(main.app).get(
                    f"/titles/{title_id}/tvdb",
                    params={"q": "https://thetvdb.com/dereferrer/series/416491"},
                )
                response = TestClient(main.app).post(
                    f"/titles/{title_id}/tvdb-manual",
                    data={
                        "tvdb_reference": "https://thetvdb.com/series/1923",
                        "return_to": f"/titles/{title_id}/tvdb?q=1923",
                        "match_origin": "bulk-tv",
                    },
                    follow_redirects=False,
                )
                success_page = TestClient(main.app).get(response.headers["location"])
            finally:
                main.db = original_database
                main.tvdb = original_tvdb

            self.assertEqual(response.status_code, 303)
            self.assertEqual(search_response.status_code, 200)
            self.assertIn("Use this match", search_response.text)
            self.assertIn("Already know the TVDB page?", search_response.text)
            self.assertIn("Back to Bulk Match", success_page.text)
            self.assertIn('/shows/bulk-match?review=true', success_page.text)
            self.assertEqual(fake_tvdb.query, "1923")
            with database.connect() as conn:
                title = conn.execute("SELECT tvdb_id FROM titles WHERE id=?", (title_id,)).fetchone()
                episode_count = conn.execute(
                    "SELECT COUNT(*) FROM expected_episodes WHERE title_id=?", (title_id,)
                ).fetchone()[0]
            self.assertEqual(title["tvdb_id"], 416491)
            self.assertEqual(episode_count, 1)

    def test_broader_movie_queries_offer_safe_title_variants(self):
        variants = broader_movie_queries("A Very Harold and Kumar 3D Christmas")
        self.assertIn("A Very Harold & Kumar 3D Christmas", variants)
        self.assertIn("A Very Harold and Kumar Christmas", variants)

    def test_broader_series_queries_clean_folder_style_searches(self):
        lifecycle = broader_series_queries("The Show (2021 - Present)")
        punctuation = broader_series_queries("Law & Order: Special Victims Unit")
        self.assertIn("The Show", lifecycle)
        self.assertIn("Law and Order: Special Victims Unit", punctuation)
        self.assertIn("Law & Order", punctuation)

    def test_match_confidence_uses_title_and_release_year(self):
        exact = match_confidence("Astro Boy", 2009, {"name": "Astro Boy", "year": "2009"})
        wrong_year = match_confidence(
            "Astro Boy", 2009, {"name": "Astro Boy", "year": "1963"}
        )
        unrelated = match_confidence(
            "Astro Boy", 2009, {"name": "Unrelated Film", "year": "1980"}
        )
        self.assertEqual(exact["score"], 100)
        self.assertGreater(exact["score"], wrong_year["score"])
        self.assertGreater(wrong_year["score"], unrelated["score"])

    def test_movie_credits_render_and_filter_by_stable_person_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "catalog.db")
            database.initialize()
            with database.connect() as conn:
                root_id = conn.execute(
                    "INSERT INTO roots(path, kind, label) VALUES ('/movies', 'movie', 'Movies')"
                ).lastrowid
                movie_id = conn.execute(
                    """INSERT INTO titles
                       (root_id, kind, title, folder_path, imdb_id, tmdb_id)
                       VALUES (?, 'movie', 'Example Movie', '/movies/Example',
                               'tt0000001', '100')""",
                    (root_id,),
                ).lastrowid
                unmatched_id = conn.execute(
                    """INSERT INTO titles
                       (root_id, kind, title, folder_path, imdb_id)
                       VALUES (?, 'movie', 'Other Movie', '/movies/Other', NULL)""",
                    (root_id,),
                ).lastrowid
                tv_root_id = conn.execute(
                    "INSERT INTO roots(path, kind, label) VALUES ('/tv', 'tv', 'TV')"
                ).lastrowid
                show_id = conn.execute(
                    """INSERT INTO titles
                       (root_id, kind, title, folder_path, imdb_id, tvdb_id)
                       VALUES (?, 'tv', 'Example Show', '/tv/Example Show',
                               'tt0000002', 200)""",
                    (tv_root_id,),
                ).lastrowid
                conn.execute(
                    """INSERT INTO files
                       (title_id, path, filename, extension, size_bytes, seen_scan)
                       VALUES (?, '/movies/Example/movie.mkv', 'movie.mkv', '.mkv', 100, 'test')""",
                    (movie_id,),
                )
                conn.execute(
                    """INSERT INTO files
                       (title_id, path, filename, extension, size_bytes, seen_scan)
                       VALUES (?, '/movies/Example/Example Movie {tmdb-100}.mkv',
                               'Example Movie {tmdb-100}.mkv', '.mkv', 100, 'test')""",
                    (movie_id,),
                )
                credits = [
                    (movie_id, "nm0000001", "Director One", "director", 1),
                    (movie_id, "nm0000002", "Lead Actor", "actor", 1),
                    (movie_id, "nm0000003", "Second Actor", "actor", 2),
                    (movie_id, "nm0000004", "Third Actor", "actor", 3),
                    (movie_id, "nm0000005", "Fourth Actor", "actor", 4),
                    (movie_id, "nm0000006", "Writer One", "writer", 1),
                    (show_id, "nm0000002", "Lead Actor", "actor", 1),
                ]
                conn.executemany(
                    """INSERT INTO title_credits
                       (title_id, imdb_person_id, person_name, role, billing_order)
                       VALUES (?, ?, ?, ?, ?)""",
                    credits,
                )
                conn.execute(
                    """INSERT INTO movie_match_suggestions
                       (title_id, candidate_json, confidence_score,
                        confidence_label, result_count, exact)
                       VALUES (?, ?, 80, 'High', 1, 0)""",
                    (
                        unmatched_id,
                        json.dumps({
                            "id": 222, "tvdb_id": 222,
                            "name": "Wrong Movie", "year": "2017",
                        }),
                    ),
                )

            original_database = main.db
            main.db = database
            try:
                client = TestClient(main.app)
                detail = client.get(f"/titles/{movie_id}")
                filtered = client.get(
                    "/movies?person=nm0000002&credit_role=actor"
                )
                cross_library = client.get(
                    "/library?person=nm0000002&credit_role=actor"
                )
                unified_people_search = client.get("/library?q=Lead%20Actor")
                unified_movie_search = client.get("/movies?q=Lead%20Actor")
                fuzzy_people_search = client.get("/library?q=Led%20Actor")
                fuzzy_suggestions = client.get(
                    "/api/library-suggestions?q=Led%20Actor&kind=all"
                )
                movies = client.get("/movies")
                matched_movies = client.get("/movies?match=matched")
                unmatched_movies = client.get("/movies?match=unmatched")
                bulk = client.get("/movies/bulk-match")
                bulk_review = client.get("/movies/bulk-match?review=true")
                sources = client.get("/sources")
                unchanged_preview = client.get("/files/2/rename-movie")
            finally:
                main.db = original_database

            self.assertEqual(detail.status_code, 200)
            self.assertIn("Director One", detail.text)
            self.assertIn("Writer One", detail.text)
            self.assertIn("See more", detail.text)
            self.assertIn(
                'href="/library?q=Lead%20Actor"',
                detail.text,
            )
            self.assertIn("movie-file-menu", detail.text)
            self.assertIn("movie-detail-menu", detail.text)
            self.assertIn(f'action="/titles/{movie_id}/imdb-refresh"', detail.text)
            self.assertIn("https://www.imdb.com/title/tt0000001/", detail.text)
            self.assertEqual(filtered.status_code, 200)
            self.assertIn("Example Movie", filtered.text)
            self.assertNotIn("Example Show", filtered.text)
            self.assertNotIn("Other Movie", filtered.text)
            self.assertIn("Example Movie", cross_library.text)
            self.assertIn("Example Show", cross_library.text)
            self.assertIn("Example Movie", unified_people_search.text)
            self.assertIn("Example Show", unified_people_search.text)
            self.assertIn("Example Movie", unified_movie_search.text)
            self.assertNotIn("Example Show", unified_movie_search.text)
            self.assertIn("Example Movie", fuzzy_people_search.text)
            self.assertIn("Example Show", fuzzy_people_search.text)
            self.assertTrue(any(
                item["value"] == "Lead Actor"
                for item in fuzzy_suggestions.json()["suggestions"]
            ))
            self.assertIn(
                '/movies?person=nm0000002&amp;person_name=Lead+Actor&amp;credit_role=actor',
                cross_library.text,
            )
            self.assertIn(f'href="/titles/{movie_id}/tvdb">Fix Match</a>', movies.text)
            self.assertIn(
                f'href="/files/1/rename-movie">Preview Rename</a>', movies.text
            )
            self.assertIn(f'href="/titles/{unmatched_id}/tvdb">Match</a>', movies.text)
            self.assertIn(
                f'class="library-title-choice" type="checkbox" name="selected" value="{unmatched_id}"',
                movies.text,
            )
            self.assertIn(
                f'class="library-title-choice" type="checkbox" name="selected" value="{movie_id}"',
                movies.text,
            )
            self.assertIn('class="letter-title-choice"', movies.text)
            self.assertIn('data-letter="E"', movies.text)
            self.assertIn("Match selected movies", movies.text)
            self.assertIn("Example Movie", matched_movies.text)
            self.assertNotIn("Other Movie", matched_movies.text)
            self.assertIn("Other Movie", unmatched_movies.text)
            self.assertNotIn("Example Movie", unmatched_movies.text)
            self.assertIn('value="unmatched" selected', unmatched_movies.text)
            self.assertEqual(bulk.status_code, 200)
            self.assertIn("Analyze all unmatched", bulk.text)
            self.assertIn('id="select-all-movies"', bulk.text)
            self.assertIn('id="select-all-label">Select all', bulk.text)
            self.assertIn("Select remaining", bulk.text)
            self.assertIn('id="clear-all-movies"', bulk.text)
            self.assertIn('class="large-movie-batch"', bulk.text)
            self.assertIn("This may take a while", bulk.text)
            self.assertIn("remain visible in the task widget", bulk.text)
            self.assertIn('id="selection-actions" hidden', bulk.text)
            self.assertIn("Find another match", bulk_review.text)
            self.assertIn(
                f'/titles/{unmatched_id}/tvdb?q=Other%20Movie', bulk_review.text
            )
            self.assertIn(f'href="/movies?root={root_id}"', sources.text)
            self.assertIn("Metadata and maintenance have moved", sources.text)
            self.assertIn('href="/settings/metadata"', sources.text)
            self.assertIn('href="/settings/system"', sources.text)
            self.assertEqual(unchanged_preview.status_code, 200)
            self.assertIn("No changes needed", unchanged_preview.text)
            self.assertNotIn("Apply rename", unchanged_preview.text)

    def test_movie_match_analysis_appears_in_progress_widget(self):
        with main.movie_match_lock:
            previous = dict(main.movie_match_job)
            main.movie_match_job.clear()
            main.movie_match_job.update({
                "status": "running", "mode": "selected", "total": 125,
                "processed": 24, "matched": 19,
            })
        try:
            task = next(
                item for item in main.active_tasks()["tasks"]
                if item["id"] == "movie-match-analysis"
            )
            self.assertEqual(task["label"], "Analyzing selected movie matches")
            self.assertIn("24 of 125 checked", task["detail"])
            self.assertIn("19 suggestions found", task["detail"])
        finally:
            with main.movie_match_lock:
                main.movie_match_job.clear()
                main.movie_match_job.update(previous)

    def test_series_cast_and_episode_crew_render_separately(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "catalog.db")
            database.initialize()
            with database.connect() as conn:
                root_id = conn.execute(
                    "INSERT INTO roots(path, kind, label) VALUES ('/tv', 'tv', 'TV')"
                ).lastrowid
                title_id = conn.execute(
                    """INSERT INTO titles
                       (root_id, kind, title, folder_path, imdb_id, tvdb_id)
                       VALUES (?, 'tv', 'Example Show', '/tv/Example', 'tt0000001', 100)""",
                    (root_id,),
                ).lastrowid
                unmatched_show_id = conn.execute(
                    """INSERT INTO titles
                       (root_id, kind, title, folder_path)
                       VALUES (?, 'tv', 'Unmatched Show', '/tv/Unmatched')""",
                    (root_id,),
                ).lastrowid
                complete_show_id = conn.execute(
                    """INSERT INTO titles
                       (root_id, kind, title, folder_path, tvdb_id)
                       VALUES (?, 'tv', 'Complete Show', '/tv/Complete', 200)""",
                    (root_id,),
                ).lastrowid
                conn.execute(
                    """INSERT INTO expected_episodes
                       (title_id, tvdb_episode_id, season, episode, name, aired)
                       VALUES (?, 201, 1, 1, 'Complete Pilot', '2020-01-01')""",
                    (complete_show_id,),
                )
                conn.execute(
                    """INSERT INTO files
                       (title_id, path, filename, extension, size_bytes,
                        season, episode_start, seen_scan)
                       VALUES (?, '/tv/Complete/S01E01.mkv', 'S01E01.mkv',
                               '.mkv', 100, 1, 1, 'test')""",
                    (complete_show_id,),
                )
                episode_id = conn.execute(
                    """INSERT INTO expected_episodes
                       (title_id, tvdb_episode_id, season, episode, name, imdb_id)
                       VALUES (?, 101, 1, 1, 'Pilot', 'tt0000101')""",
                    (title_id,),
                ).lastrowid
                conn.execute(
                    """INSERT INTO expected_episodes
                       (title_id, tvdb_episode_id, season, episode, name, aired)
                       VALUES (?, 102, 1, 2, 'Missing Episode', '2020-01-01')""",
                    (title_id,),
                )
                file_id = conn.execute(
                    """INSERT INTO files
                       (title_id, path, filename, extension, size_bytes,
                        season, episode_start, seen_scan)
                       VALUES (?, '/tv/Example/S01E01.mkv', 'S01E01.mkv', '.mkv',
                               100, 1, 1, 'test')""",
                    (title_id,),
                ).lastrowid
                conn.execute(
                    """INSERT INTO title_credits
                       (title_id, imdb_person_id, person_name, role, billing_order)
                       VALUES (?, 'nm1', 'Series Lead', 'actor', 1)""",
                    (title_id,),
                )
                conn.executemany(
                    """INSERT INTO episode_credits
                       (expected_episode_id, imdb_person_id, person_name, role, billing_order)
                       VALUES (?, ?, ?, ?, 1)""",
                    [
                        (episode_id, "nm2", "Episode Director", "director"),
                        (episode_id, "nm3", "Episode Writer", "writer"),
                    ],
                )
            original_database = main.db
            main.db = database
            try:
                client = TestClient(main.app)
                response = client.get(f"/titles/{title_id}")
                shows = client.get("/shows")
                unmatched_shows = client.get("/shows?match=unmatched")
                missing_shows = client.get("/shows?gaps=missing")
                complete_shows = client.get("/shows?gaps=complete")
                revealed_missing = client.get(
                    f"/titles/{title_id}?show_missing=1"
                )
                rename_review = client.get(f"/titles/{title_id}/rename-episodes")
            finally:
                main.db = original_database
            self.assertEqual(response.status_code, 200)
            self.assertIn("Top billed:", response.text)
            self.assertIn("Series Lead", response.text)
            self.assertIn("Directed by", response.text)
            self.assertIn("Episode Director", response.text)
            self.assertIn("Written by", response.text)
            self.assertIn("Episode Writer", response.text)
            self.assertIn("episode-title-link", response.text)
            self.assertIn(f'action="/titles/{title_id}/imdb-refresh"', response.text)
            self.assertIn(f'action="/files/{file_id}/imdb-refresh"', response.text)
            self.assertIn('id="toggle-missing"', response.text)
            self.assertIn("Missing Episodes", response.text)
            self.assertIn('class="missing-episodes-count has-missing">2</span>', response.text)
            self.assertEqual(response.text.count('id="toggle-missing"'), 1)
            self.assertIn('name="selected_file_ids"', rename_review.text)
            self.assertIn('id="select-all-renames"', rename_review.text)
            self.assertIn("Rename selected episodes", rename_review.text)
            self.assertIn("Match selected TV shows", shows.text)
            self.assertIn('action="/shows/bulk-match/analyze"', shows.text)
            self.assertIn('class="letter-title-choice"', shows.text)
            self.assertIn('data-tooltip="Select all E TV series"', shows.text)
            self.assertIn("Unmatched Show", unmatched_shows.text)
            self.assertNotIn("Example Show", unmatched_shows.text)
            self.assertIn("Example Show", missing_shows.text)
            self.assertNotIn("Unmatched Show", missing_shows.text)
            self.assertIn("Complete Show", complete_shows.text)
            self.assertNotIn("Example Show", complete_shows.text)
            self.assertNotIn("Unmatched Show", complete_shows.text)
            self.assertIn(
                f'/titles/{title_id}?show_missing=1#missing-panel', shows.text
            )
            self.assertNotIn('id="missing-panel" hidden', revealed_missing.text)
            self.assertIn("Missing Episode", revealed_missing.text)
            self.assertIn(
                "https://ext.to/browse/?q=Example+Show+S01E02",
                revealed_missing.text,
            )
            self.assertIn(
                f'class="library-title-choice" type="checkbox" name="selected" value="{title_id}"',
                shows.text,
            )


if __name__ == "__main__":
    unittest.main()

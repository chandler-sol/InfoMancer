from __future__ import annotations

import sqlite3
import unittest

from app.media_identity.candidates import (
    MAX_FAST_CANDIDATES,
    _provider_candidate_ids,
    generate_episode_candidates,
)


class FastCandidateFallbackTests(unittest.TestCase):
    def test_long_season_keeps_claimed_episode_inside_bounded_fallback(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(
                "CREATE TABLE titles(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,tvdb_id INTEGER)"
            )
            conn.execute(
                """CREATE TABLE expected_episodes(
                     id INTEGER PRIMARY KEY,title_id INTEGER NOT NULL,
                     tvdb_episode_id INTEGER NOT NULL,season INTEGER NOT NULL,
                     episode INTEGER NOT NULL,name TEXT NOT NULL DEFAULT '',aired TEXT
                   )"""
            )
            conn.execute("INSERT INTO titles(id,kind,tvdb_id) VALUES (1,'tv',NULL)")
            conn.executemany(
                """INSERT INTO expected_episodes(
                     id,title_id,tvdb_episode_id,season,episode,name
                   ) VALUES (?,1,?,1,?,?)""",
                [
                    (episode, 1000 + episode, episode, f"Episode {episode}")
                    for episode in range(1, 121)
                ],
            )

            candidate_set = generate_episode_candidates(
                conn,
                title_id=1,
                season=1,
                episode_start=100,
            )
        finally:
            conn.close()

        self.assertEqual(len(candidate_set.candidates), MAX_FAST_CANDIDATES)
        self.assertEqual(candidate_set.candidates[0].identity.provider_item_id, "1100")
        self.assertIn(
            "claimed_coordinate",
            candidate_set.candidates[0].details["origins"],
        )




class FastProviderCandidateBoundTests(unittest.TestCase):
    def test_provider_id_selection_is_bounded_before_detail_mapping_query(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        try:
            conn.execute(
                "CREATE TABLE titles(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,tvdb_id INTEGER)"
            )
            conn.execute(
                """CREATE TABLE expected_episodes(
                     id INTEGER PRIMARY KEY,title_id INTEGER NOT NULL,
                     tvdb_episode_id INTEGER NOT NULL,season INTEGER NOT NULL,
                     episode INTEGER NOT NULL,name TEXT NOT NULL DEFAULT '',aired TEXT
                   )"""
            )
            conn.execute(
                """CREATE TABLE provider_episode_series_cache(
                     provider TEXT,provider_series_id TEXT,language TEXT,
                     source_signature TEXT
                   )"""
            )
            conn.execute(
                """CREATE TABLE provider_episode_identities(
                     provider TEXT,provider_series_id TEXT,provider_episode_id TEXT,
                     language TEXT,name TEXT,overview TEXT,aired TEXT,metadata_json TEXT
                   )"""
            )
            conn.execute(
                """CREATE TABLE provider_episode_mappings(
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     provider TEXT,provider_series_id TEXT,provider_episode_id TEXT,
                     language TEXT,order_namespace TEXT,order_name TEXT,
                     season INTEGER,episode INTEGER,absolute_number INTEGER,
                     coordinate_key TEXT
                   )"""
            )
            conn.execute("INSERT INTO titles(id,kind,tvdb_id) VALUES (1,'tv',4242)")
            conn.execute(
                """INSERT INTO provider_episode_series_cache(
                     provider,provider_series_id,language,source_signature
                   ) VALUES ('tvdb','4242','eng','provider-large')"""
            )
            conn.executemany(
                """INSERT INTO provider_episode_identities(
                     provider,provider_series_id,provider_episode_id,language,
                     name,overview,aired,metadata_json
                   ) VALUES ('tvdb','4242',?,'eng',?,?,?,'{}')""",
                [
                    (str(1000 + episode), f"Episode {episode}", f"Overview {episode}", "2026-01-01")
                    for episode in range(1, 201)
                ],
            )
            conn.executemany(
                """INSERT INTO provider_episode_mappings(
                     provider,provider_series_id,provider_episode_id,language,
                     order_namespace,order_name,season,episode,absolute_number,coordinate_key
                   ) VALUES ('tvdb','4242',?,'eng','default','Default',1,?,?,?)""",
                [
                    (str(1000 + episode), episode, episode, f"[1,{episode},{episode}]")
                    for episode in range(1, 201)
                ],
            )

            selected_ids = _provider_candidate_ids(
                conn,
                provider_series_id="4242",
                season=1,
                episode_start=100,
                episode_end=100,
                include_specials=False,
                language="eng",
            )
            self.assertEqual(len(selected_ids), MAX_FAST_CANDIDATES)
            self.assertEqual(selected_ids[0], "1100")

            statements.clear()
            candidate_set = generate_episode_candidates(
                conn,
                title_id=1,
                season=1,
                episode_start=100,
            )
        finally:
            conn.close()

        self.assertEqual(len(candidate_set.candidates), MAX_FAST_CANDIDATES)
        detail_queries = [
            statement
            for statement in statements
            if "FROM provider_episode_identities i" in statement
        ]
        self.assertEqual(len(detail_queries), 1)
        self.assertIn("i.provider_episode_id IN (", detail_queries[0])


if __name__ == "__main__":
    unittest.main()

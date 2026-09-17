from __future__ import annotations

import sqlite3
import unittest

from app.media_identity.candidates import MAX_FAST_CANDIDATES, generate_episode_candidates


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


if __name__ == "__main__":
    unittest.main()

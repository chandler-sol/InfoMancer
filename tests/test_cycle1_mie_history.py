from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.mie_history import MediaIntelligenceHistoryEngine


class Cycle1MIEHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO roots(
                     id,path,kind,label,health_status,last_scanned_at,last_checked_at,
                     last_seen_at,last_file_count,last_observed_file_count
                   ) VALUES (
                     1,'/media/movies','movie','Movies','healthy',CURRENT_TIMESTAMP,
                     CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,1,1
                   )"""
            )
            conn.execute(
                """INSERT INTO titles(
                     id,root_id,kind,title,year,folder_path,updated_at
                   ) VALUES (
                     1,1,'movie','Example',2024,'/media/movies/Example',CURRENT_TIMESTAMP
                   )"""
            )
            conn.execute(
                """INSERT INTO files(
                     id,title_id,path,filename,extension,size_bytes,modified_at,
                     media_info_at,media_info_error,seen_scan
                   ) VALUES (
                     1,1,'/media/movies/Example/movie.mkv','movie.mkv','.mkv',1000,
                     123.5,CURRENT_TIMESTAMP,'read failed','scan-1'
                   )"""
            )
        self.mie = MediaIntelligenceHistoryEngine(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def latest_run(self) -> dict:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM mie_analysis_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row)

    def latest_snapshot(self) -> dict:
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT * FROM mie_title_health_snapshots
                   WHERE title_id=1 ORDER BY run_id DESC LIMIT 1"""
            ).fetchone()
        return dict(row)

    def test_opened_resolved_reopened_and_title_snapshots_are_deterministic(self) -> None:
        first_count = self.mie.analyze()
        first = self.latest_run()
        first_snapshot = self.latest_snapshot()

        self.assertEqual(first_count, 3)
        self.assertEqual(first["active_findings"], 3)
        self.assertEqual(first["opened_findings"], 3)
        self.assertEqual(first["resolved_findings"], 0)
        self.assertEqual(first_snapshot["critical_count"], 1)
        self.assertEqual(first_snapshot["warning_count"], 2)
        self.assertEqual(first_snapshot["information_count"], 0)
        self.assertEqual(first_snapshot["score"], 64)

        self.mie.analyze()
        unchanged = self.latest_run()
        self.assertEqual(unchanged["opened_findings"], 0)
        self.assertEqual(unchanged["resolved_findings"], 0)
        self.assertEqual(self.latest_snapshot()["score"], 64)

        with self.database.connect() as conn:
            conn.execute(
                """UPDATE titles SET
                     metadata_title='Example',metadata_year=2024,tvdb_movie_id=101,
                     poster_url='https://example.invalid/poster.jpg',
                     metadata_refreshed_at=CURRENT_TIMESTAMP
                   WHERE id=1"""
            )
            conn.execute(
                """INSERT INTO title_credits(
                     title_id,imdb_person_id,person_name,role,billing_order
                   ) VALUES (1,'nm1','Example Person','actor',0)"""
            )
            conn.execute(
                """UPDATE files SET media_info_error=NULL,
                     media_info_at=CURRENT_TIMESTAMP WHERE id=1"""
            )

        fixed_count = self.mie.analyze()
        fixed = self.latest_run()
        fixed_snapshot = self.latest_snapshot()
        self.assertEqual(fixed_count, 0)
        self.assertEqual(fixed["active_findings"], 0)
        self.assertEqual(fixed["opened_findings"], 0)
        self.assertEqual(fixed["resolved_findings"], 3)
        self.assertEqual(fixed_snapshot["score"], 100)
        self.assertEqual(fixed_snapshot["critical_count"], 0)
        self.assertEqual(self.mie.titles_needing_attention(), [])

        with self.database.connect() as conn:
            conn.execute(
                "UPDATE files SET media_info_error='read failed again' WHERE id=1"
            )

        reopened_count = self.mie.analyze()
        reopened = self.latest_run()
        reopened_snapshot = self.latest_snapshot()
        self.assertEqual(reopened_count, 1)
        self.assertEqual(reopened["opened_findings"], 1)
        self.assertEqual(reopened["resolved_findings"], 0)
        self.assertEqual(reopened_snapshot["score"], 80)
        self.assertEqual(reopened_snapshot["critical_count"], 1)

        attention = self.mie.titles_needing_attention()
        self.assertEqual(len(attention), 1)
        self.assertEqual(attention[0]["title_id"], 1)
        self.assertEqual(attention[0]["score"], 80)

        history = self.mie.title_health_history(1)
        self.assertEqual([row["score"] for row in history[:4]], [80, 100, 64, 64])
        self.assertEqual(history[0]["opened_findings"], 1)
        self.assertEqual(history[1]["resolved_findings"], 3)

    def test_attention_uses_latest_complete_snapshot_run(self) -> None:
        self.mie.analyze()
        complete_snapshot = self.latest_snapshot()
        self.assertEqual(complete_snapshot["score"], 64)

        # A schema-17 writer can legally add an analysis run after a safe downgrade,
        # but it cannot know about Migration 18 title snapshots. Re-upgrading must not
        # make the attention query appear empty until the next 0.9 analysis finishes.
        with self.database.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO mie_analysis_runs(
                     analyzed_at,active_findings,suppressed_findings,overall_score
                   ) VALUES (CURRENT_TIMESTAMP,3,0,64)"""
            )
            bare_run_id = int(cursor.lastrowid)
        self.assertGreater(bare_run_id, int(complete_snapshot["run_id"]))

        attention = self.mie.titles_needing_attention()
        self.assertEqual(len(attention), 1)
        self.assertEqual(attention[0]["title_id"], 1)
        self.assertEqual(attention[0]["run_id"], complete_snapshot["run_id"])
        self.assertEqual(attention[0]["score"], 64)

    def test_snapshot_retention_follows_analysis_run_retention(self) -> None:
        for _ in range(55):
            self.mie.analyze()
        with self.database.connect() as conn:
            run_count = int(conn.execute(
                "SELECT COUNT(*) FROM mie_analysis_runs"
            ).fetchone()[0])
            snapshot_count = int(conn.execute(
                "SELECT COUNT(*) FROM mie_title_health_snapshots WHERE title_id=1"
            ).fetchone()[0])
            orphan_count = int(conn.execute(
                """SELECT COUNT(*) FROM mie_title_health_snapshots h
                   LEFT JOIN mie_analysis_runs r ON r.id=h.run_id
                   WHERE r.id IS NULL"""
            ).fetchone()[0])
        self.assertEqual(run_count, 50)
        self.assertEqual(snapshot_count, 50)
        self.assertEqual(orphan_count, 0)


if __name__ == "__main__":
    unittest.main()

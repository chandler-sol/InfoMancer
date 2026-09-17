from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.db import Database
from app.mie_history import MediaIntelligenceHistoryEngine


class Cycle1MIEReviewTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        templates = Path(__file__).resolve().parents[1] / "app" / "templates"
        cls.environment = Environment(
            loader=FileSystemLoader(templates),
            autoescape=select_autoescape(["html"]),
        )

    def render(self, summary: dict) -> str:
        return self.environment.get_template("_mie_attention.html").render(
            summary=summary,
        )

    def test_attention_panel_explains_score_trend_and_links_to_title(self) -> None:
        html = self.render({
            "last_analyzed_at": "2026-09-17 00:00:00",
            "attention_snapshot_run_id": 9,
            "attention_snapshot_current": True,
            "attention_titles": [{
                "title_id": 42,
                "title_name": "Example Movie",
                "kind": "movie",
                "score": 72,
                "critical_count": 1,
                "warning_count": 1,
                "information_count": 0,
                "trend": "worsening",
                "score_delta": -8,
                "recent_scores": [88, 80, 72],
            }],
        })

        self.assertIn("Titles needing attention", html)
        self.assertIn("Example Movie", html)
        self.assertIn("Movie · 1 critical · 1 warning · 0 information", html)
        self.assertIn("Down 8 points since the previous title snapshot", html)
        self.assertIn("Recent scores: 88 → 80 → 72", html)
        self.assertIn('href="/titles/42"', html)
        self.assertIn("does not change your media files", html)
        self.assertNotIn("newer analysis exists", html)

    def test_attention_panel_labels_stale_title_scores(self) -> None:
        html = self.render({
            "last_analyzed_at": "2026-09-17 01:00:00",
            "attention_snapshot_run_id": 9,
            "attention_snapshot_current": False,
            "attention_titles": [{
                "title_id": 42,
                "title_name": "Example Movie",
                "kind": "movie",
                "score": 80,
                "critical_count": 1,
                "warning_count": 0,
                "information_count": 0,
                "trend": "stable",
                "score_delta": 0,
                "recent_scores": [80, 80],
            }],
        })

        self.assertIn("A newer analysis exists than these title snapshots", html)
        self.assertIn("Refresh analysis before treating these title scores as current", html)

    def test_attention_panel_has_clear_empty_state_after_snapshot_analysis(self) -> None:
        html = self.render({
            "last_analyzed_at": "2026-09-17 00:00:00",
            "attention_snapshot_run_id": 9,
            "attention_snapshot_current": True,
            "attention_titles": [],
        })

        self.assertIn("Titles needing attention", html)
        self.assertIn("No title-level findings currently reduce a title health score", html)
        self.assertNotIn("has not been recorded yet", html)

    def test_attention_panel_asks_for_refresh_when_snapshot_is_stale(self) -> None:
        html = self.render({
            "last_analyzed_at": "2026-09-17 01:00:00",
            "attention_snapshot_run_id": 9,
            "attention_snapshot_current": False,
            "attention_titles": [],
        })

        self.assertIn("Title-level health history is from an earlier analysis", html)
        self.assertIn("Refresh analysis", html)
        self.assertNotIn("No title-level findings currently reduce", html)

    def test_attention_panel_asks_for_refresh_when_history_is_not_recorded(self) -> None:
        html = self.render({
            "last_analyzed_at": "2026-09-16 23:00:00",
            "attention_snapshot_run_id": None,
            "attention_snapshot_current": False,
            "attention_titles": [],
        })

        self.assertIn("Titles needing attention", html)
        self.assertIn("Title-level health history has not been recorded yet", html)
        self.assertIn("Refresh the analysis", html)
        self.assertNotIn("No title-level findings currently reduce", html)

    def test_attention_panel_is_absent_before_first_analysis(self) -> None:
        html = self.render({
            "last_analyzed_at": None,
            "attention_snapshot_run_id": None,
            "attention_snapshot_current": False,
            "attention_titles": [],
        })

        self.assertNotIn("Titles needing attention", html)


class Cycle1MIEReviewSnapshotStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        self.mie = MediaIntelligenceHistoryEngine(self.database)
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO roots(id,path,kind,label) VALUES (1,'/media','movie','Movies')"
            )
            conn.execute(
                """INSERT INTO titles(id,root_id,kind,title,folder_path)
                   VALUES (1,1,'movie','Example','/media/Example')"""
            )
            conn.execute(
                """INSERT INTO mie_analysis_state(id,last_analyzed_at,finding_count)
                   VALUES (1,CURRENT_TIMESTAMP,0)"""
            )
            cursor = conn.execute(
                """INSERT INTO mie_analysis_runs(
                     analyzed_at,active_findings,suppressed_findings,overall_score
                   ) VALUES (CURRENT_TIMESTAMP,0,0,100)"""
            )
            self.run_id = int(cursor.lastrowid)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_summary_distinguishes_missing_current_and_stale_title_snapshots(self) -> None:
        before = self.mie.summary()
        self.assertTrue(before["last_analyzed_at"])
        self.assertIsNone(before["attention_snapshot_run_id"])
        self.assertFalse(before["attention_snapshot_current"])
        self.assertEqual(before["attention_titles"], [])

        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO mie_title_health_snapshots(
                     run_id,title_id,score,critical_count,warning_count,information_count
                   ) VALUES (?,?,100,0,0,0)""",
                (self.run_id, 1),
            )

        current = self.mie.summary()
        self.assertEqual(current["attention_snapshot_run_id"], self.run_id)
        self.assertTrue(current["attention_snapshot_current"])
        self.assertEqual(current["attention_titles"], [])

        with self.database.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO mie_analysis_runs(
                     analyzed_at,active_findings,suppressed_findings,overall_score
                   ) VALUES (CURRENT_TIMESTAMP,0,0,100)"""
            )
            newer_run_id = int(cursor.lastrowid)
        self.assertGreater(newer_run_id, self.run_id)

        stale = self.mie.summary()
        self.assertEqual(stale["attention_snapshot_run_id"], self.run_id)
        self.assertFalse(stale["attention_snapshot_current"])
        self.assertEqual(stale["attention_titles"], [])


class Cycle1MIEReviewEmptyLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        self.mie = MediaIntelligenceHistoryEngine(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_successful_empty_library_analysis_is_current(self) -> None:
        self.assertEqual(self.mie.analyze(), 0)

        summary = self.mie.summary()
        self.assertTrue(summary["last_analyzed_at"])
        self.assertIsNotNone(summary["attention_snapshot_run_id"])
        self.assertTrue(summary["attention_snapshot_current"])
        self.assertEqual(summary["attention_titles"], [])

        with self.database.connect() as conn:
            snapshot_count = int(conn.execute(
                "SELECT COUNT(*) FROM mie_title_health_snapshots"
            ).fetchone()[0])
        self.assertEqual(snapshot_count, 0)


if __name__ == "__main__":
    unittest.main()

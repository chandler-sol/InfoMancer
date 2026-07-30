import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.mie import MediaIntelligenceEngine


class MediaIntelligenceEngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        self.engine = MediaIntelligenceEngine(self.database)
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO roots(id,path,kind,label,last_scanned_at)
                   VALUES (1,'/media/tv','tv','TV','2020-01-01 00:00:00')"""
            )
            conn.execute(
                """INSERT INTO titles(
                     id,root_id,kind,title,folder_path,tvdb_id,metadata_title
                   ) VALUES (1,1,'tv','Example Show','/media/tv/Example Show',
                             123,'Example Show')"""
            )
            conn.execute(
                """INSERT INTO files(
                     id,title_id,path,filename,extension,size_bytes,season,
                     episode_start,episode_end,media_info_error,seen_scan
                   ) VALUES (
                     1,1,'/media/tv/Example Show/Example Show S01E01.mkv',
                     'Example Show S01E01.mkv','.mkv',100,1,1,1,
                     'This MKV appears incomplete or damaged. Technical details: '
                     || 'FFprobe reported invalid data.','scan-1'
                   )"""
            )
            conn.executemany(
                """INSERT INTO expected_episodes(
                     id,title_id,tvdb_episode_id,season,episode,name,aired
                   ) VALUES (?,?,?,?,?,?,?)""",
                [
                    (1, 1, 1001, 1, 1, "Pilot", "2020-01-01"),
                    (2, 1, 1002, 1, 2, "Second", "2020-01-08"),
                ],
            )

    def tearDown(self):
        self.temporary.cleanup()

    def test_analysis_explains_existing_catalog_facts_without_changing_media(self):
        with self.database.connect() as conn:
            file_before = dict(conn.execute(
                "SELECT * FROM files WHERE id=1"
            ).fetchone())
            title_count_before = conn.execute(
                "SELECT COUNT(*) FROM titles"
            ).fetchone()[0]

        count = self.engine.analyze()
        findings = self.engine.findings()
        rules = {finding["rule_key"] for finding in findings}

        self.assertEqual(count, 3)
        self.assertEqual(
            rules, {"media-unreadable", "missing-episodes", "source-stale"},
        )
        unreadable = next(
            finding for finding in findings
            if finding["rule_key"] == "media-unreadable"
        )
        self.assertIn("incomplete or damaged", unreadable["explanation"])
        self.assertIn("restore or replace", unreadable["recommendation"])
        self.assertEqual(unreadable["severity"], "critical")

        with self.database.connect() as conn:
            self.assertEqual(
                dict(conn.execute("SELECT * FROM files WHERE id=1").fetchone()),
                file_before,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM titles").fetchone()[0],
                title_count_before,
            )

    def test_dismissed_findings_stay_dismissed_until_restored(self):
        self.engine.analyze()
        finding = next(
            item for item in self.engine.findings()
            if item["rule_key"] == "missing-episodes"
        )

        self.assertTrue(self.engine.dismiss(finding["id"], None))
        self.assertEqual(len(self.engine.findings(status="dismissed")), 1)
        self.engine.analyze()
        dismissed = self.engine.findings(status="dismissed")
        self.assertEqual([item["id"] for item in dismissed], [finding["id"]])

        self.assertTrue(self.engine.restore(finding["id"]))
        active_ids = {item["id"] for item in self.engine.findings()}
        self.assertIn(finding["id"], active_ids)

    def test_finding_resolves_after_catalog_fact_is_fixed(self):
        self.engine.analyze()
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE files
                   SET media_info_error='',media_info_at=CURRENT_TIMESTAMP
                   WHERE id=1"""
            )
            conn.execute(
                """INSERT INTO files(
                     id,title_id,path,filename,extension,size_bytes,season,
                     episode_start,episode_end,media_info_at,seen_scan
                   ) VALUES (
                     2,1,'/media/tv/Example Show/Example Show S01E02.mkv',
                     'Example Show S01E02.mkv','.mkv',100,1,2,2,
                     CURRENT_TIMESTAMP,'scan-2'
                   )"""
            )
            conn.execute(
                "UPDATE roots SET last_scanned_at=CURRENT_TIMESTAMP WHERE id=1"
            )

        self.assertEqual(self.engine.analyze(), 0)
        self.assertEqual(self.engine.findings(), [])
        self.assertEqual(len(self.engine.findings(status="resolved")), 3)


if __name__ == "__main__":
    unittest.main()

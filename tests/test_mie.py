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
                     id,root_id,kind,title,folder_path,tvdb_id,metadata_title,poster_url
                   ) VALUES (1,1,'tv','Example Show','/media/tv/Example Show',
                             123,'Example Show','https://example.test/poster.jpg')"""
            )
            conn.execute(
                """INSERT INTO title_credits(title_id,imdb_person_id,person_name,role)
                   VALUES (1,'nm1','Example Actor','actor')"""
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

    def test_library_quality_defaults_are_inherited_and_source_overrides_win(self):
        initial = self.engine.library_quality_defaults()
        self.assertFalse(initial["configured"])
        self.engine.save_library_quality_defaults(
            minimum_width="1920", minimum_height="1080", minimum_bitrate_mbps="8",
            preferred_video_codecs="HEVC, AV1", preferred_containers="MATROSKA, MP4",
            minimum_audio_channels="6", dynamic_range="any", detect_outliers=True,
        )
        defaults = self.engine.library_quality_defaults()
        self.assertTrue(defaults["configured"])
        self.assertEqual(defaults["minimum_width"], 1920)
        inherited = self.engine.quality_profiles()[0]
        self.assertTrue(inherited["inheriting"])
        self.assertFalse(inherited["configured"])
        self.assertEqual(inherited["minimum_width"], 1920)
        self.engine.save_quality_profile(1, minimum_width="1280", detect_outliers=False)
        override = self.engine.quality_profiles()[0]
        self.assertTrue(override["configured"])
        self.assertFalse(override["inheriting"])
        self.assertEqual(override["minimum_width"], 1280)
        self.engine.delete_quality_profile(1)
        inherited_again = self.engine.quality_profiles()[0]
        self.assertTrue(inherited_again["inheriting"])
        self.assertEqual(inherited_again["minimum_width"], 1920)

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

    def test_source_guard_status_replaces_stale_finding_with_actionable_health(self):
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE roots SET health_status='offline',last_checked_at=?,
                   last_seen_at=?,last_error=?,last_file_count=1 WHERE id=1""",
                ("2026-01-02T03:04:05+00:00", "2026-01-01T03:04:05+00:00",
                 "The network path was not found."),
            )
        self.engine.analyze()
        findings = self.engine.findings()
        rules = {finding["rule_key"] for finding in findings}
        self.assertIn("source-offline", rules)
        self.assertNotIn("source-stale", rules)
        offline = next(item for item in findings if item["rule_key"] == "source-offline")
        self.assertEqual(offline["severity"], "critical")
        self.assertEqual(offline["evidence"]["protected_catalog_files"], 1)
        self.assertIn("preserved", offline["explanation"])

    def test_metadata_health_flags_missing_artwork_and_credits(self):
        with self.database.connect() as conn:
            conn.execute("UPDATE titles SET poster_url=NULL WHERE id=1")
            conn.execute("DELETE FROM title_credits WHERE title_id=1")
        self.engine.analyze()
        rules = {finding["rule_key"] for finding in self.engine.findings()}
        self.assertIn("metadata-artwork-missing", rules)
        self.assertIn("metadata-credits-missing", rules)

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

    def test_low_identity_confidence_explains_available_evidence(self):
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO titles(id,root_id,kind,title,year,folder_path)
                   VALUES (2,1,'movie','Mystery Film',2022,'/media/tv/Mystery Film')"""
            )
            conn.execute(
                """INSERT INTO files(
                     id,title_id,path,filename,extension,size_bytes,seen_scan
                   ) VALUES (2,2,'/media/tv/Mystery Film/movie.mkv','movie.mkv',
                             '.mkv',100,'scan-1')"""
            )

        self.engine.analyze()
        finding = next(
            item for item in self.engine.findings()
            if item["rule_key"] == "identity-confidence-low"
        )
        self.assertEqual(finding["evidence"]["confidence_score"], "25/100")
        self.assertEqual(finding["evidence"]["provider_identifiers"], ["none"])
        self.assertIn("identity confidence", finding["summary"])

    def test_multi_episode_file_reports_the_detected_range(self):
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE files SET episode_end=2,
                   filename='Example Show S01E01-E02.mkv' WHERE id=1"""
            )

        self.engine.analyze()
        finding = next(
            item for item in self.engine.findings()
            if item["rule_key"] == "multi-episode-file"
        )
        self.assertEqual(
            finding["evidence"]["episode_range"], ["S01E01", "S01E02"]
        )
        self.assertIn("no change is needed", finding["recommendation"])

    def test_quality_profile_finds_threshold_and_consistency_outlier(self):
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE files SET media_info_error='',media_info_at='now',
                   width=1920,height=1080,video_codec='H264',audio_channels=6,
                   bitrate=8000000,container='MATROSKA',dynamic_range='SDR'
                   WHERE id=1"""
            )
            conn.executemany(
                """INSERT INTO files(
                     id,title_id,path,filename,extension,size_bytes,season,
                     episode_start,episode_end,media_info_at,width,height,
                     video_codec,audio_channels,bitrate,container,dynamic_range,seen_scan
                   ) VALUES (?,?,?,?,?,?,?,?,?,'now',?,?,?,?,?,?,?,'scan-2')""",
                [
                    (2, 1, '/media/tv/Example Show/e2.mkv', 'e2.mkv', '.mkv', 100,
                     1, 2, 2, 1920, 1080, 'H264', 6, 8000000, 'MATROSKA', 'SDR'),
                    (3, 1, '/media/tv/Example Show/e3.mkv', 'e3.mkv', '.mkv', 100,
                     1, 3, 3, 1280, 720, 'H264', 2, 3000000, 'MATROSKA', 'SDR'),
                ],
            )
        self.engine.save_quality_profile(
            1, minimum_height="1080", minimum_bitrate_mbps="5",
            preferred_video_codecs="h264", preferred_containers="matroska",
            minimum_audio_channels="6", dynamic_range="sdr",
            detect_outliers=True,
        )

        self.engine.analyze()
        findings = self.engine.findings(category="quality")
        by_rule = {item["rule_key"]: item for item in findings}
        self.assertIn("quality-preference", by_rule)
        self.assertIn("quality-consistency", by_rule)
        self.assertEqual(by_rule["quality-preference"]["file_id"], 3)
        profile = self.engine.quality_profiles()[0]
        self.assertTrue(profile["configured"])
        self.assertEqual(profile["minimum_bitrate_mbps"], 5.0)
        self.assertEqual(profile["preferred_video_codecs"], "H264")

    def test_quality_profile_validation_is_actionable(self):
        with self.assertRaisesRegex(ValueError, "whole number"):
            self.engine.save_quality_profile(1, minimum_height="high")
        with self.assertRaisesRegex(ValueError, "Separate multiple entries"):
            self.engine.save_quality_profile(1, preferred_video_codecs="H264, not valid!")

    def test_calibration_changes_thresholds_and_validates_score_penalties(self):
        self.engine.save_calibration(
            identity_warning_threshold="95", source_stale_hours="48",
            critical_weight="30", warning_weight="10", information_weight="1",
        )

        self.engine.analyze()
        identity = next(
            item for item in self.engine.findings()
            if item["rule_key"] == "identity-confidence-low"
        )
        self.assertEqual(identity["evidence"]["confidence_score"], "90/100")
        self.assertEqual(self.engine.calibration()["source_stale_hours"], 48)
        with self.assertRaisesRegex(ValueError, "descend"):
            self.engine.save_calibration(
                identity_warning_threshold="70", source_stale_hours="24",
                critical_weight="5", warning_weight="10", information_weight="2",
            )

    def test_analysis_records_transparent_category_scores_and_history(self):
        self.engine.analyze()

        scores = {item["category"]: item for item in self.engine.category_scores()}
        self.assertEqual(scores["health"]["score"], 80)
        self.assertEqual(scores["completeness"]["score"], 92)
        self.assertEqual(scores["freshness"]["score"], 98)
        self.assertEqual(self.engine.summary()["overall_score"], 95)
        history = self.engine.analysis_history()
        self.assertEqual(history[0]["active_findings"], 3)
        self.assertEqual(history[0]["suppressed_findings"], 0)

    def test_explicit_feedback_suppresses_matching_future_finding(self):
        self.engine.analyze()
        finding = next(
            item for item in self.engine.findings()
            if item["rule_key"] == "missing-episodes"
        )
        self.assertTrue(self.engine.dismiss(
            finding["id"], None, reason="expected", scope="title",
            note="Specials are stored separately.",
        ))

        self.assertEqual(self.engine.analyze(), 2)
        self.assertNotIn(
            "missing-episodes", {item["rule_key"] for item in self.engine.findings()},
        )
        feedback = self.engine.feedback()
        self.assertEqual(feedback[0]["scope"], "title")
        self.assertEqual(self.engine.summary()["suppressed"], 1)

        self.assertTrue(self.engine.delete_feedback(feedback[0]["id"]))
        self.assertEqual(self.engine.analyze(), 3)
        self.assertIn(
            "missing-episodes", {item["rule_key"] for item in self.engine.findings()},
        )

    def test_identity_report_explains_independent_evidence(self):
        report = self.engine.identity_report(1)
        self.assertIsNotNone(report)
        self.assertEqual(report["score"], 90)
        self.assertEqual(
            {item["key"] for item in report["breakdown"]},
            {"provider", "title", "year", "folder"},
        )
        self.assertIn("TVDB series", report["providers"])

    def test_storage_report_summarizes_sources_and_titles(self):
        report = self.engine.storage_report()
        self.assertEqual(report["by_source"][0]["files"], 1)
        self.assertEqual(report["by_source"][0]["bytes"], 100)
        self.assertEqual(report["largest_titles"][0]["title"], "Example Show")


if __name__ == "__main__":
    unittest.main()

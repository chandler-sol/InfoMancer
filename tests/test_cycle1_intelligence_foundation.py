from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import Database, SCHEMA
from app.media_integrity_state import MediaIntegrityResultService
from app.migrations import MIGRATIONS, assess_schema_downgrade
from app.stream_inventory import MediaStreamService


class Cycle1IntelligenceFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "catalog.db"
        self.database = Database(self.path)
        self.database.initialize()
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO roots(id,path,kind,label)
                   VALUES (1,'/media/movies','movie','Movies')"""
            )
            conn.execute(
                """INSERT INTO titles(id,root_id,kind,title,folder_path)
                   VALUES (1,1,'movie','Example','/media/movies/Example')"""
            )
            conn.execute(
                """INSERT INTO files(
                     id,title_id,path,filename,extension,size_bytes,modified_at,seen_scan
                   ) VALUES (1,1,'/media/movies/Example/movie.mkv','movie.mkv',
                             '.mkv',1000,123.5,'scan-1')"""
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_migration_18_creates_additive_intelligence_schema(self) -> None:
        with self.database.connect() as conn:
            run_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(mie_analysis_runs)")
            }
            tables = {
                row["name"] for row in conn.execute(
                    """SELECT name FROM sqlite_master
                       WHERE type='table' AND name IN (
                         'media_streams','media_integrity_results','mie_title_health_snapshots'
                       )"""
                )
            }
            ledger = conn.execute(
                """SELECT compatibility,minimum_reader_schema,
                          minimum_writer_schema,downgrade_policy
                   FROM schema_compatibility WHERE migration_version=18"""
            ).fetchone()
            downgrade = assess_schema_downgrade(conn, 17)

        self.assertTrue({"opened_findings", "resolved_findings"}.issubset(run_columns))
        self.assertEqual(
            tables,
            {"media_streams", "media_integrity_results", "mie_title_health_snapshots"},
        )
        self.assertIsNotNone(ledger)
        self.assertEqual(ledger["compatibility"], "additive")
        self.assertEqual(ledger["minimum_reader_schema"], 1)
        self.assertEqual(ledger["minimum_writer_schema"], 1)
        self.assertEqual(ledger["downgrade_policy"], "compatible")
        self.assertEqual(downgrade["status"], "safe_downgrade")
        self.assertEqual(downgrade["blocking_migrations"], [])

    def test_migration_18_failure_rolls_back_partial_schema_changes(self) -> None:
        broken_path = Path(self.temporary.name) / "broken-upgrade.db"
        conn = sqlite3.connect(broken_path)
        conn.executescript(SCHEMA)
        conn.execute(
            """CREATE TABLE schema_migrations(
                 version INTEGER PRIMARY KEY,
                 name TEXT NOT NULL,
                 applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        conn.executemany(
            "INSERT INTO schema_migrations(version,name) VALUES (?,?)",
            [(migration.version, migration.name) for migration in MIGRATIONS if migration.version < 18],
        )
        # Migration 18 will find this pre-existing malformed table, then fail when
        # creating its stream index. The columns it added earlier in the savepoint
        # must not leak through that failed migration.
        conn.execute("CREATE TABLE media_streams(file_id INTEGER)")
        conn.commit()
        conn.close()

        with self.assertRaises(sqlite3.OperationalError):
            Database(broken_path).initialize()

        check = sqlite3.connect(broken_path)
        columns = {row[1] for row in check.execute("PRAGMA table_info(mie_analysis_runs)")}
        applied_18 = check.execute(
            "SELECT 1 FROM schema_migrations WHERE version=18"
        ).fetchone()
        check.close()
        self.assertNotIn("opened_findings", columns)
        self.assertNotIn("resolved_findings", columns)
        self.assertIsNone(applied_18)

    def test_stream_inventory_replace_is_atomic(self) -> None:
        streams = MediaStreamService(self.database)
        streams.replace(1, [
            {
                "index": 0, "type": "video", "codec": "HEVC", "language": "und",
                "default": True,
            },
            {
                "index": 1, "type": "audio", "codec": "EAC3", "language": "eng",
                "channels": 6, "channel_layout": "5.1", "sample_rate": 48000,
                "title": "Main", "default": True,
            },
        ])
        before = streams.file_streams(1)
        self.assertEqual([row["stream_index"] for row in before], [0, 1])
        self.assertEqual(before[1]["language"], "eng")
        self.assertEqual(before[1]["channels"], 6)

        with self.assertRaises(sqlite3.IntegrityError):
            streams.replace(1, [
                {"index": 7, "type": "audio", "codec": "AAC"},
                {"index": 7, "type": "subtitle", "codec": "SRT"},
            ])

        after = streams.file_streams(1)
        self.assertEqual(
            [(row["stream_index"], row["stream_type"], row["codec"]) for row in after],
            [(0, "video", "HEVC"), (1, "audio", "EAC3")],
        )

    def test_integrity_result_becomes_stale_when_file_identity_changes(self) -> None:
        integrity = MediaIntegrityResultService(self.database)
        integrity.record(
            1,
            status="passed",
            mode="sample",
            checked_modified_at=123.5,
            checked_size_bytes=1000,
            issues=[],
            details={"samples": [{"offset": 0, "returncode": 0}]},
        )
        result = integrity.result(1)
        self.assertIsNotNone(result)
        self.assertFalse(result["stale"])
        self.assertEqual(result["issue_count"], 0)
        self.assertEqual(result["details"]["samples"][0]["returncode"], 0)
        self.assertEqual(integrity.pending_files(), [])
        self.assertEqual(integrity.pending_files([]), [])
        current_summary = integrity.summary()
        self.assertEqual(current_summary["passed"], 1)
        self.assertEqual(current_summary["unchecked_or_stale"], 0)

        with self.database.connect() as conn:
            conn.execute("UPDATE files SET size_bytes=1001 WHERE id=1")

        stale = integrity.result(1)
        self.assertIsNotNone(stale)
        self.assertTrue(stale["stale"])
        self.assertEqual([row["id"] for row in integrity.pending_files()], [1])
        stale_summary = integrity.summary()
        self.assertEqual(stale_summary["unchecked_or_stale"], 1)
        self.assertEqual(stale_summary["passed"], 0)

    def test_stream_and_integrity_rows_follow_file_cascade(self) -> None:
        streams = MediaStreamService(self.database)
        integrity = MediaIntegrityResultService(self.database)
        streams.replace(1, [{"index": 0, "type": "video", "codec": "H264"}])
        integrity.record(
            1,
            status="warning",
            mode="sample",
            checked_modified_at=123.5,
            checked_size_bytes=1000,
            issues=["timestamp discontinuity"],
        )
        with self.database.connect() as conn:
            conn.execute("DELETE FROM files WHERE id=1")
        self.assertEqual(streams.file_streams(1), [])
        self.assertIsNone(integrity.result(1))


if __name__ == "__main__":
    unittest.main()

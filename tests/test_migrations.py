from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.migrations import MIGRATIONS, schema_contract


class MigrationTests(unittest.TestCase):
    def test_fresh_database_records_all_numbered_migrations_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "catalog.db")
            database.initialize()
            database.initialize()
            with database.connect() as conn:
                versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
            self.assertEqual(versions, [migration.version for migration in MIGRATIONS])

    def test_legacy_database_receives_missing_columns(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.db"
            conn = sqlite3.connect(path)
            conn.executescript("""
                CREATE TABLE roots(id INTEGER PRIMARY KEY,path TEXT,kind TEXT,label TEXT,enabled INTEGER DEFAULT 1,last_scanned_at TEXT);
                CREATE TABLE titles(id INTEGER PRIMARY KEY,root_id INTEGER,kind TEXT,title TEXT,year INTEGER,folder_path TEXT,metadata_title TEXT,updated_at TEXT);
                CREATE TABLE files(id INTEGER PRIMARY KEY,title_id INTEGER,path TEXT,filename TEXT,extension TEXT,size_bytes INTEGER,modified_at REAL,season INTEGER,episode_start INTEGER,episode_end INTEGER,parsed_title TEXT,seen_scan TEXT);
            """)
            conn.commit()
            conn.close()
            Database(path).initialize()
            with Database(path).connect() as upgraded:
                columns = {row["name"] for row in upgraded.execute("PRAGMA table_info(files)")}
                self.assertIn("edition_name", columns)
                self.assertIn("version_preferred", columns)
                self.assertIsNotNone(upgraded.execute("SELECT 1 FROM schema_migrations WHERE version=11").fetchone())
                self.assertIsNotNone(upgraded.execute("SELECT 1 FROM schema_migrations WHERE version=12").fetchone())
                self.assertIsNotNone(upgraded.execute("SELECT 1 FROM schema_migrations WHERE version=13").fetchone())
                self.assertIsNotNone(upgraded.execute("SELECT 1 FROM schema_migrations WHERE version=14").fetchone())
                self.assertIsNotNone(upgraded.execute("SELECT 1 FROM schema_migrations WHERE version=18").fetchone())
                self.assertIsNotNone(upgraded.execute("SELECT 1 FROM schema_migrations WHERE version=19").fetchone())
                self.assertIsNotNone(upgraded.execute("SELECT 1 FROM schema_migrations WHERE version=20").fetchone())
                self.assertIsNotNone(upgraded.execute("SELECT 1 FROM schema_migrations WHERE version=21").fetchone())
                self.assertIsNotNone(upgraded.execute("SELECT 1 FROM schema_migrations WHERE version=23").fetchone())
                confirmation_columns = {
                    row["name"]
                    for row in upgraded.execute(
                        "PRAGMA table_info(media_identity_confirmations)"
                    )
                }
                self.assertTrue({
                    "source_scan_snapshot_id",
                    "source_result_revision",
                    "source_decision_snapshot_sha256",
                    "source_metadata_signature",
                }.issubset(confirmation_columns))
                rename_columns = {row["name"] for row in upgraded.execute("PRAGMA table_info(rename_proposals)")}
                self.assertTrue({"file_id", "source_path", "destination_path", "source_size", "source_mtime_ns", "status"}.issubset(rename_columns))
                operation_columns = {
                    row["name"] for row in upgraded.execute("PRAGMA table_info(operation_history)")
                }
                self.assertTrue({"operation_type", "status", "undo_kind", "undo_payload", "undone_at"}.issubset(operation_columns))
                saved_view_columns = {
                    row["name"] for row in upgraded.execute("PRAGMA table_info(user_saved_views)")
                }
                self.assertTrue({"user_id", "name", "path", "query_string", "pinned"}.issubset(saved_view_columns))
                lockout_columns = {
                    row["name"] for row in upgraded.execute("PRAGMA table_info(login_lockouts)")
                }
                self.assertEqual(
                    {"scope", "lock_key", "locked_until", "created_at"},
                    lockout_columns,
                )
                self.assertIsNotNone(
                    upgraded.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='provider_episode_series_cache'"
                    ).fetchone()
                )
                self.assertIsNotNone(
                    upgraded.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='external_analysis_sources'"
                    ).fetchone()
                )
                self.assertIsNotNone(
                    upgraded.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='external_path_mappings'"
                    ).fetchone()
                )
                external_source_columns = {
                    row["name"]
                    for row in upgraded.execute("PRAGMA table_info(external_analysis_sources)")
                }
                self.assertTrue({
                    "source_key", "enabled", "server_url", "metadata_root",
                    "credential_generation", "config_revision", "last_test_revision",
                    "last_test_status", "last_test_detail", "last_test_server_name",
                    "last_test_version", "last_test_at",
                }.issubset(external_source_columns))

    def test_existing_migration_21_database_receives_credential_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.db"
            conn = sqlite3.connect(path)
            conn.executescript(
                """
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE external_analysis_sources (
                    source_key TEXT PRIMARY KEY CHECK(source_key!=''),
                    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
                    server_url TEXT NOT NULL DEFAULT '',
                    metadata_root TEXT NOT NULL DEFAULT '',
                    config_json TEXT NOT NULL DEFAULT '{}',
                    config_revision INTEGER NOT NULL DEFAULT 0 CHECK(config_revision>=0),
                    last_test_revision INTEGER,
                    last_test_status TEXT NOT NULL DEFAULT '',
                    last_test_detail TEXT NOT NULL DEFAULT '',
                    last_test_server_name TEXT NOT NULL DEFAULT '',
                    last_test_version TEXT NOT NULL DEFAULT '',
                    last_test_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE external_path_mappings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL
                      REFERENCES external_analysis_sources(source_key) ON DELETE CASCADE,
                    external_root TEXT NOT NULL CHECK(external_root!=''),
                    local_root TEXT NOT NULL CHECK(local_root!=''),
                    priority INTEGER NOT NULL DEFAULT 100,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_key,external_root,local_root)
                );
                """
            )
            for migration in (item for item in MIGRATIONS if item.version <= 21):
                conn.execute(
                    "INSERT INTO schema_migrations(version,name) VALUES (?,?)",
                    (migration.version, migration.name),
                )
            conn.execute(
                """INSERT INTO external_analysis_sources(
                     source_key,enabled,server_url,config_revision
                   ) VALUES ('plex',1,'http://plex.local:32400',7)"""
            )
            conn.commit()
            conn.close()

            Database(path).initialize()

            with Database(path).connect() as upgraded:
                columns = {
                    row["name"]
                    for row in upgraded.execute(
                        "PRAGMA table_info(external_analysis_sources)"
                    )
                }
                self.assertIn("credential_generation", columns)
                row = upgraded.execute(
                    """SELECT credential_generation,config_revision
                       FROM external_analysis_sources WHERE source_key='plex'"""
                ).fetchone()
                self.assertEqual(row["credential_generation"], "")
                self.assertEqual(row["config_revision"], 7)
                self.assertIsNotNone(
                    upgraded.execute(
                        "SELECT 1 FROM schema_migrations WHERE version=22"
                    ).fetchone()
                )

    def test_migrations_17_through_23_preserve_safe_downgrade_semantics(self):
        migration_17 = next(item for item in MIGRATIONS if item.version == 17)
        self.assertEqual(migration_17.compatibility, "behavioral")
        self.assertEqual(migration_17.minimum_reader_schema, 1)
        self.assertEqual(migration_17.minimum_writer_schema, 1)
        self.assertEqual(migration_17.downgrade_policy, "compatible")

        for version in (18, 19, 20, 21, 22, 23):
            migration = next(item for item in MIGRATIONS if item.version == version)
            self.assertEqual(migration.compatibility, "additive")
            self.assertEqual(migration.minimum_reader_schema, 1)
            self.assertEqual(migration.minimum_writer_schema, 1)
            self.assertEqual(migration.downgrade_policy, "compatible")

        self.assertEqual(schema_contract(), {
            "current": 23,
            "minimum_reader_schema": 1,
            "minimum_writer_schema": 1,
            "downgrade_policy": "compatible",
        })

        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "catalog.db")
            database.initialize()
            with database.connect() as conn:
                rows = {
                    int(row["migration_version"]): row
                    for row in conn.execute(
                        """SELECT migration_version,compatibility,minimum_reader_schema,
                                  minimum_writer_schema,downgrade_policy
                           FROM schema_compatibility
                           WHERE migration_version IN (17,18,19,20,21,22,23)"""
                    )
                }
            self.assertEqual(rows[17]["compatibility"], "behavioral")
            for version in (18, 19, 20, 21, 22, 23):
                self.assertEqual(rows[version]["compatibility"], "additive")
                self.assertEqual(rows[version]["minimum_reader_schema"], 1)
                self.assertEqual(rows[version]["minimum_writer_schema"], 1)
                self.assertEqual(rows[version]["downgrade_policy"], "compatible")

    def test_existing_compatibility_snapshot_is_not_silently_rewritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "catalog.db")
            database.initialize()
            with database.connect() as conn:
                conn.execute(
                    """UPDATE schema_compatibility
                       SET compatibility='additive'
                       WHERE migration_version=17"""
                )

            # Simulate opening a database whose migration-17 compatibility snapshot
            # was written by the earlier build. Initialization must preserve that
            # historical record rather than rewriting installed downgrade history.
            database.initialize()
            with database.connect() as conn:
                row = conn.execute(
                    """SELECT compatibility FROM schema_compatibility
                       WHERE migration_version=17"""
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["compatibility"], "additive")


if __name__ == "__main__":
    unittest.main()

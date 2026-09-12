import sqlite3
import unittest

from app.migrations import (
    CURRENT_SCHEMA_VERSION,
    assess_schema_downgrade,
    schema_compatibility_history,
    schema_contract,
    sync_schema_compatibility_ledger,
)


class SchemaCompatibility09Tests(unittest.TestCase):
    def make_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE schema_migrations (
                 version INTEGER PRIMARY KEY,
                 name TEXT NOT NULL,
                 applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        return conn

    def seed_current_history(self, conn: sqlite3.Connection) -> None:
        for version in range(1, CURRENT_SCHEMA_VERSION + 1):
            conn.execute(
                "INSERT INTO schema_migrations(version,name) VALUES (?,?)",
                (version, f"migration {version}"),
            )
        sync_schema_compatibility_ledger(conn)

    def test_current_contract_is_backwards_compatible_through_schema_one(self):
        contract = schema_contract()
        self.assertEqual(contract["current"], CURRENT_SCHEMA_VERSION)
        self.assertEqual(contract["minimum_reader_schema"], 1)
        self.assertEqual(contract["minimum_writer_schema"], 1)
        self.assertEqual(contract["downgrade_policy"], "compatible")

    def test_ledger_backfills_existing_migrations_without_rewriting_history(self):
        conn = self.make_connection()
        self.seed_current_history(conn)
        history = schema_compatibility_history(conn)
        self.assertEqual(len(history), CURRENT_SCHEMA_VERSION)
        self.assertEqual(history[-1]["migration_version"], CURRENT_SCHEMA_VERSION)
        self.assertEqual(history[-1]["downgrade_policy"], "compatible")

        conn.execute(
            "UPDATE schema_compatibility SET migration_name='historical snapshot' WHERE migration_version=1"
        )
        sync_schema_compatibility_ledger(conn)
        name = conn.execute(
            "SELECT migration_name FROM schema_compatibility WHERE migration_version=1"
        ).fetchone()[0]
        self.assertEqual(name, "historical snapshot")
        conn.close()

    def test_known_additive_history_allows_safe_downgrade(self):
        conn = self.make_connection()
        self.seed_current_history(conn)
        assessment = assess_schema_downgrade(conn, 1)
        self.assertEqual(assessment["status"], "safe_downgrade")
        self.assertEqual(assessment["blocking_migrations"], [])
        conn.close()

    def test_unknown_future_migration_fails_closed(self):
        conn = self.make_connection()
        self.seed_current_history(conn)
        future = CURRENT_SCHEMA_VERSION + 1
        conn.execute(
            "INSERT INTO schema_migrations(version,name) VALUES (?,?)",
            (future, "future unknown migration"),
        )
        assessment = assess_schema_downgrade(conn, CURRENT_SCHEMA_VERSION)
        self.assertEqual(assessment["status"], "restore_required")
        self.assertEqual(assessment["reason"], "compatibility_unknown")
        self.assertEqual(assessment["blocking_migrations"], [future])
        conn.close()

    def test_future_read_only_migration_can_allow_reader_but_not_writer(self):
        conn = self.make_connection()
        self.seed_current_history(conn)
        future = CURRENT_SCHEMA_VERSION + 1
        conn.execute(
            "INSERT INTO schema_migrations(version,name) VALUES (?,?)",
            (future, "future read-only migration"),
        )
        conn.execute(
            """INSERT INTO schema_compatibility(
                 migration_version,migration_name,compatibility,
                 minimum_reader_schema,minimum_writer_schema,downgrade_policy
               ) VALUES (?,?,?,?,?,?)""",
            (
                future,
                "future read-only migration",
                "behavioral",
                CURRENT_SCHEMA_VERSION,
                None,
                "read_only",
            ),
        )
        assessment = assess_schema_downgrade(conn, CURRENT_SCHEMA_VERSION)
        self.assertEqual(assessment["status"], "read_only")
        self.assertEqual(assessment["blocking_migrations"], [future])
        conn.close()

    def test_breaking_future_migration_requires_restore(self):
        conn = self.make_connection()
        self.seed_current_history(conn)
        future = CURRENT_SCHEMA_VERSION + 1
        conn.execute(
            "INSERT INTO schema_migrations(version,name) VALUES (?,?)",
            (future, "future breaking migration"),
        )
        conn.execute(
            """INSERT INTO schema_compatibility(
                 migration_version,migration_name,compatibility,
                 minimum_reader_schema,minimum_writer_schema,downgrade_policy
               ) VALUES (?,?,?,?,?,?)""",
            (
                future,
                "future breaking migration",
                "breaking",
                future,
                future,
                "restore_required",
            ),
        )
        assessment = assess_schema_downgrade(conn, CURRENT_SCHEMA_VERSION)
        self.assertEqual(assessment["status"], "restore_required")
        self.assertEqual(assessment["reason"], "reader_incompatible")
        self.assertEqual(assessment["blocking_migrations"], [future])
        conn.close()


if __name__ == "__main__":
    unittest.main()

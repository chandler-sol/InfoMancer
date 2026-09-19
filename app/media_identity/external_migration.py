from __future__ import annotations

import sqlite3


def apply_external_source_foundation(conn: sqlite3.Connection) -> None:
    """Add optional read-only media-server integration configuration."""

    conn.execute(
        """CREATE TABLE IF NOT EXISTS external_analysis_sources (
             source_key TEXT PRIMARY KEY CHECK(source_key!=''),
             enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
             server_url TEXT NOT NULL DEFAULT '',
             metadata_root TEXT NOT NULL DEFAULT '',
             config_json TEXT NOT NULL DEFAULT '{}',
             last_test_status TEXT NOT NULL DEFAULT '',
             last_test_detail TEXT NOT NULL DEFAULT '',
             last_test_server_name TEXT NOT NULL DEFAULT '',
             last_test_version TEXT NOT NULL DEFAULT '',
             last_test_at TEXT,
             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
             updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS external_path_mappings (
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
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_external_path_mappings_source
           ON external_path_mappings(source_key,enabled,priority,id)"""
    )
    conn.executemany(
        """INSERT OR IGNORE INTO external_analysis_sources(source_key,enabled)
           VALUES (?,0)""",
        (("plex",), ("jellyfin",)),
    )

from __future__ import annotations

import sqlite3


def apply_provider_episode_cache(conn: sqlite3.Connection) -> None:
    """Add additive provider episode identity and order-mapping persistence."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS provider_episode_series_cache (
             provider TEXT NOT NULL,
             provider_series_id TEXT NOT NULL,
             language TEXT NOT NULL DEFAULT 'eng',
             provider_updated_at TEXT NOT NULL DEFAULT '',
             source_signature TEXT NOT NULL,
             refreshed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
             episode_count INTEGER NOT NULL DEFAULT 0 CHECK(episode_count>=0),
             mapping_count INTEGER NOT NULL DEFAULT 0 CHECK(mapping_count>=0),
             order_namespaces_json TEXT NOT NULL DEFAULT '[]',
             PRIMARY KEY(provider,provider_series_id,language)
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS provider_episode_identities (
             provider TEXT NOT NULL,
             provider_series_id TEXT NOT NULL,
             provider_episode_id TEXT NOT NULL,
             language TEXT NOT NULL DEFAULT 'eng',
             name TEXT NOT NULL DEFAULT '',
             overview TEXT NOT NULL DEFAULT '',
             aired TEXT NOT NULL DEFAULT '',
             absolute_number INTEGER,
             metadata_json TEXT NOT NULL DEFAULT '{}',
             PRIMARY KEY(provider,provider_series_id,provider_episode_id,language),
             FOREIGN KEY(provider,provider_series_id,language)
               REFERENCES provider_episode_series_cache(provider,provider_series_id,language)
               ON DELETE CASCADE
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_provider_episode_identity_series
           ON provider_episode_identities(provider,provider_series_id,language,provider_episode_id)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS provider_episode_mappings (
             id INTEGER PRIMARY KEY,
             provider TEXT NOT NULL,
             provider_series_id TEXT NOT NULL,
             provider_episode_id TEXT NOT NULL,
             language TEXT NOT NULL DEFAULT 'eng',
             order_namespace TEXT NOT NULL,
             order_name TEXT NOT NULL DEFAULT '',
             season INTEGER,
             episode INTEGER,
             absolute_number INTEGER,
             coordinate_key TEXT NOT NULL,
             details_json TEXT NOT NULL DEFAULT '{}',
             FOREIGN KEY(provider,provider_series_id,provider_episode_id,language)
               REFERENCES provider_episode_identities(
                 provider,provider_series_id,provider_episode_id,language
               ) ON DELETE CASCADE,
             UNIQUE(
               provider,provider_series_id,provider_episode_id,language,
               order_namespace,coordinate_key
             )
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_provider_episode_mapping_coordinate
           ON provider_episode_mappings(
             provider,provider_series_id,language,order_namespace,season,episode
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_provider_episode_mapping_identity
           ON provider_episode_mappings(
             provider,provider_series_id,provider_episode_id,language,order_namespace
           )"""
    )

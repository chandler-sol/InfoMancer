from __future__ import annotations

import sqlite3


def apply_provider_episode_cache(conn: sqlite3.Connection) -> None:
    """Add additive provider episode identity and order-mapping persistence."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS provider_episode_series_cache (
             provider TEXT NOT NULL CHECK(provider!=''),
             provider_series_id TEXT NOT NULL CHECK(provider_series_id!=''),
             language TEXT NOT NULL DEFAULT 'eng' CHECK(language!=''),
             provider_updated_at TEXT NOT NULL DEFAULT '',
             source_signature TEXT NOT NULL CHECK(source_signature!=''),
             refreshed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
             episode_count INTEGER NOT NULL DEFAULT 0 CHECK(episode_count>=0),
             mapping_count INTEGER NOT NULL DEFAULT 0 CHECK(mapping_count>=0),
             order_namespaces_json TEXT NOT NULL DEFAULT '[]',
             PRIMARY KEY(provider,provider_series_id,language)
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS provider_episode_identities (
             provider TEXT NOT NULL CHECK(provider!=''),
             provider_series_id TEXT NOT NULL CHECK(provider_series_id!=''),
             provider_episode_id TEXT NOT NULL CHECK(provider_episode_id!=''),
             language TEXT NOT NULL DEFAULT 'eng' CHECK(language!=''),
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
             provider TEXT NOT NULL CHECK(provider!=''),
             provider_series_id TEXT NOT NULL CHECK(provider_series_id!=''),
             provider_episode_id TEXT NOT NULL CHECK(provider_episode_id!=''),
             language TEXT NOT NULL DEFAULT 'eng' CHECK(language!=''),
             order_namespace TEXT NOT NULL CHECK(order_namespace!=''),
             order_name TEXT NOT NULL DEFAULT '',
             season INTEGER,
             episode INTEGER,
             absolute_number INTEGER,
             coordinate_key TEXT NOT NULL CHECK(coordinate_key!=''),
             details_json TEXT NOT NULL DEFAULT '{}',
             CHECK(season IS NOT NULL OR episode IS NOT NULL OR absolute_number IS NOT NULL),
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

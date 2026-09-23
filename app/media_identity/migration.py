from __future__ import annotations

import json
import sqlite3


RESULT_STATES_SQL = """'verified','probably_correct','inconclusive',
'possible_mismatch','likely_mismatch','strong_match_other',
'episode_order_conflict','duplicate_content_identity','possible_swapped_episodes'"""


def apply_media_identity_foundation(conn: sqlite3.Connection) -> None:
    """Add generic, additive persistence for resumable media identity analysis."""
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS media_identity_scans (
             id INTEGER PRIMARY KEY,
             file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
             identity_kind TEXT NOT NULL DEFAULT 'episode',
             requested_profile TEXT NOT NULL
               CHECK(requested_profile IN ('fast','normal','deep')),
             completed_profile TEXT
               CHECK(completed_profile IN ('fast','normal','deep')),
             status TEXT NOT NULL DEFAULT 'queued'
               CHECK(status IN ('queued','running','paused','complete','error','cancelled')),
             stage TEXT NOT NULL DEFAULT '',
             claimed_identity_json TEXT NOT NULL DEFAULT '{{}}',
             file_size_bytes INTEGER NOT NULL DEFAULT 0 CHECK(file_size_bytes>=0),
             file_modified_at REAL,
             file_sha256 TEXT,
             metadata_signature TEXT NOT NULL DEFAULT '',
             result_state TEXT CHECK(result_state IN ({RESULT_STATES_SQL})),
             best_candidate_key TEXT,
             requested_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
             requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
             started_at TEXT,
             completed_at TEXT,
             error TEXT NOT NULL DEFAULT ''
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_media_identity_scans_file
           ON media_identity_scans(file_id,id DESC)"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_media_identity_scans_work
           ON media_identity_scans(status,requested_profile,requested_at,id)"""
    )

    conn.execute(
        """CREATE TABLE IF NOT EXISTS media_identity_candidates (
             scan_id INTEGER NOT NULL REFERENCES media_identity_scans(id) ON DELETE CASCADE,
             candidate_key TEXT NOT NULL,
             identity_kind TEXT NOT NULL,
             provider TEXT NOT NULL DEFAULT '',
             provider_item_id TEXT NOT NULL DEFAULT '',
             expected_episode_id INTEGER REFERENCES expected_episodes(id) ON DELETE SET NULL,
             order_namespace TEXT NOT NULL DEFAULT '',
             season INTEGER,
             episode INTEGER,
             display_name TEXT NOT NULL DEFAULT '',
             rank INTEGER CHECK(rank IS NULL OR rank>=1),
             score REAL NOT NULL DEFAULT 0,
             support_strength REAL NOT NULL DEFAULT 0
               CHECK(support_strength>=0 AND support_strength<=1),
             conflict_strength REAL NOT NULL DEFAULT 0
               CHECK(conflict_strength>=0 AND conflict_strength<=1),
             independent_categories INTEGER NOT NULL DEFAULT 0
               CHECK(independent_categories>=0),
             details_json TEXT NOT NULL DEFAULT '{}',
             PRIMARY KEY(scan_id,candidate_key)
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_media_identity_candidates_rank
           ON media_identity_candidates(scan_id,rank,score DESC)"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_media_identity_candidates_provider
           ON media_identity_candidates(identity_kind,provider,provider_item_id)"""
    )

    conn.execute(
        """CREATE TABLE IF NOT EXISTS media_identity_evidence (
             id INTEGER PRIMARY KEY,
             scan_id INTEGER NOT NULL REFERENCES media_identity_scans(id) ON DELETE CASCADE,
             candidate_key TEXT NOT NULL DEFAULT '',
             analyzer_key TEXT NOT NULL,
             analyzer_version TEXT NOT NULL,
             evidence_category TEXT NOT NULL,
             correlation_group TEXT NOT NULL,
             relation TEXT NOT NULL CHECK(relation IN ('supports','conflicts','neutral')),
             strength REAL NOT NULL DEFAULT 0 CHECK(strength>=0 AND strength<=1),
             source_kind TEXT NOT NULL DEFAULT '',
             source_ref TEXT NOT NULL DEFAULT '',
             timestamp_ms INTEGER CHECK(timestamp_ms IS NULL OR timestamp_ms>=0),
             value_text TEXT NOT NULL DEFAULT '',
             details_json TEXT NOT NULL DEFAULT '{}',
             cache_key TEXT NOT NULL DEFAULT '',
             profile TEXT NOT NULL CHECK(profile IN ('fast','normal','deep')),
             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_media_identity_evidence_scan
           ON media_identity_evidence(scan_id,candidate_key,evidence_category,id)"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_media_identity_evidence_cache
           ON media_identity_evidence(cache_key) WHERE cache_key!=''"""
    )

    conn.execute(
        """CREATE TABLE IF NOT EXISTS media_identity_artifacts (
             id INTEGER PRIMARY KEY,
             file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
             artifact_type TEXT NOT NULL,
             analyzer_key TEXT NOT NULL,
             analyzer_version TEXT NOT NULL,
             cache_key TEXT NOT NULL DEFAULT '',
             status TEXT NOT NULL DEFAULT 'complete'
               CHECK(status IN ('complete','error')),
             profile TEXT NOT NULL CHECK(profile IN ('fast','normal','deep')),
             source_kind TEXT NOT NULL DEFAULT '',
             source_ref TEXT NOT NULL DEFAULT '',
             source_signature TEXT NOT NULL DEFAULT '',
             file_size_bytes INTEGER NOT NULL DEFAULT 0 CHECK(file_size_bytes>=0),
             file_modified_at REAL,
             start_ms INTEGER CHECK(start_ms IS NULL OR start_ms>=0),
             end_ms INTEGER CHECK(end_ms IS NULL OR end_ms>=0),
             text_value TEXT NOT NULL DEFAULT '',
             cache_path TEXT NOT NULL DEFAULT '',
             payload_json TEXT NOT NULL DEFAULT '{}',
             error TEXT NOT NULL DEFAULT '',
             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
             updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
             last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
             CHECK(start_ms IS NULL OR end_ms IS NULL OR end_ms>=start_ms)
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_media_identity_artifacts_file
           ON media_identity_artifacts(file_id,artifact_type,analyzer_key)"""
    )
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_media_identity_artifacts_cache_identity
           ON media_identity_artifacts(
             file_id,artifact_type,analyzer_key,analyzer_version,cache_key
           ) WHERE cache_key!=''"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_media_identity_artifacts_source
           ON media_identity_artifacts(source_kind,source_ref,source_signature)"""
    )

    conn.execute(
        """CREATE TABLE IF NOT EXISTS media_identity_confirmations (
             file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
             identity_kind TEXT NOT NULL,
             provider TEXT NOT NULL DEFAULT '',
             provider_item_id TEXT NOT NULL DEFAULT '',
             expected_episode_id INTEGER REFERENCES expected_episodes(id) ON DELETE SET NULL,
             order_namespace TEXT NOT NULL DEFAULT '',
             season INTEGER,
             episode INTEGER,
             display_name TEXT NOT NULL DEFAULT '',
             source_scan_id INTEGER REFERENCES media_identity_scans(id) ON DELETE SET NULL,
             confirmed_size_bytes INTEGER NOT NULL DEFAULT 0 CHECK(confirmed_size_bytes>=0),
             confirmed_modified_at REAL,
             confirmed_sha256 TEXT,
             confirmed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
             confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_media_identity_confirmations_provider
           ON media_identity_confirmations(identity_kind,provider,provider_item_id)"""
    )


def apply_media_identity_confirmation_provenance(
    conn: sqlite3.Connection,
) -> None:
    """Persist immutable confirmation-time scan provenance outside the scan FK."""
    confirmation_table = conn.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type='table' AND name='media_identity_confirmations'"""
    ).fetchone()
    if confirmation_table is None:
        # Repair databases whose historical migration ledger claims the
        # foundation ran even though its tables are absent. The foundation is
        # idempotent and uses CREATE IF NOT EXISTS throughout.
        apply_media_identity_foundation(conn)

    existing = {
        str(row["name"])
        for row in conn.execute(
            "PRAGMA table_info(media_identity_confirmations)"
        )
    }
    additions = {
        "source_scan_snapshot_id": "INTEGER NOT NULL DEFAULT 0 CHECK(source_scan_snapshot_id>=0)",
        "source_result_revision": "INTEGER NOT NULL DEFAULT 0 CHECK(source_result_revision>=0)",
        "source_decision_snapshot_sha256": "TEXT NOT NULL DEFAULT ''",
        "source_metadata_signature": "TEXT NOT NULL DEFAULT ''",
    }
    added_columns: set[str] = set()
    for name, definition in additions.items():
        if name not in existing:
            conn.execute(
                f"ALTER TABLE media_identity_confirmations ADD COLUMN {name} {definition}"
            )
            added_columns.add(name)

    if not added_columns:
        return

    # A legacy confirmation can identify which scan it referenced, but a later
    # migration cannot prove which mutable result revision, sealed decision, or
    # metadata signature the human actually reviewed at confirmation time.
    # Preserve the source scan as audit identity only. The zero/blank provenance
    # intentionally makes confirmation_status() treat these rows as stale until
    # the user reviews and confirms a current sealed result.
    conn.execute(
        """UPDATE media_identity_confirmations
           SET source_scan_snapshot_id=CASE
                 WHEN source_scan_id IS NOT NULL AND source_scan_snapshot_id=0
                 THEN source_scan_id
                 ELSE source_scan_snapshot_id
               END,
               source_result_revision=0,
               source_decision_snapshot_sha256='',
               source_metadata_signature=''
           WHERE source_scan_id IS NOT NULL"""
    )

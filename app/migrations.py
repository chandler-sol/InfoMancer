from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Callable

from .media_identity.migration import apply_media_identity_foundation
from .media_identity.provider_migration import apply_provider_episode_cache
from .media_identity.external_migration import apply_external_source_foundation


COMPATIBILITY_LEVELS = {"additive", "behavioral", "breaking"}
DOWNGRADE_POLICIES = {"compatible", "read_only", "restore_required"}


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]
    compatibility: str
    minimum_reader_schema: int
    minimum_writer_schema: int | None
    downgrade_policy: str


def additive_migration(
    version: int,
    name: str,
    apply: Callable[[sqlite3.Connection], None],
    compatible_from_schema: int = 1,
) -> Migration:
    """Declare an additive migration that older schema generations may ignore safely."""
    return Migration(
        version=version,
        name=name,
        apply=apply,
        compatibility="additive",
        minimum_reader_schema=compatible_from_schema,
        minimum_writer_schema=compatible_from_schema,
        downgrade_policy="compatible",
    )


def behavioral_migration(
    version: int,
    name: str,
    apply: Callable[[sqlite3.Connection], None],
    compatible_from_schema: int = 1,
) -> Migration:
    """Declare a behavior-changing migration that remains reader/writer compatible.

    Use this class when a migration changes future database semantics, such as by
    installing triggers or changing workflow-visible behavior, but older builds may
    still safely read and write the resulting database. The compatibility ledger
    records the semantic distinction without unnecessarily blocking a safe downgrade.
    """
    return Migration(
        version=version,
        name=name,
        apply=apply,
        compatibility="behavioral",
        minimum_reader_schema=compatible_from_schema,
        minimum_writer_schema=compatible_from_schema,
        downgrade_policy="compatible",
    )


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_columns(conn: sqlite3.Connection, table: str, additions: dict[str, str]) -> None:
    existing = _columns(conn, table)
    for name, definition in additions.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _titles(conn: sqlite3.Connection) -> None:
    _add_columns(conn, "titles", {
        "end_year": "INTEGER", "continuing": "INTEGER",
        "metadata_end_year": "INTEGER", "metadata_continuing": "INTEGER",
        "metadata_status": "TEXT", "metadata_refreshed_at": "TEXT",
        "metadata_refresh_error": "TEXT NOT NULL DEFAULT ''",
        "metadata_provider": "TEXT NOT NULL DEFAULT ''", "overview": "TEXT",
        "tvdb_movie_id": "INTEGER", "tmdb_id": "TEXT", "imdb_id": "TEXT",
        "imdb_checked_at": "TEXT", "genres": "TEXT", "imdb_title_type": "TEXT",
        "imdb_rating": "REAL", "imdb_votes": "INTEGER", "poster_url": "TEXT",
        "metadata_title_language": "TEXT", "discovered_at": "TEXT",
        "last_scanned_at": "TEXT",
    })


def _roots(conn: sqlite3.Connection) -> None:
    _add_columns(conn, "roots", {
        "health_status": "TEXT NOT NULL DEFAULT 'unknown'", "last_checked_at": "TEXT",
        "last_seen_at": "TEXT", "last_error": "TEXT NOT NULL DEFAULT ''",
        "last_file_count": "INTEGER NOT NULL DEFAULT 0",
        "last_observed_file_count": "INTEGER NOT NULL DEFAULT 0",
        "guard_preserved_count": "INTEGER NOT NULL DEFAULT 0",
    })


def _collections(conn: sqlite3.Connection) -> None:
    _add_columns(conn, "collections", {
        "collection_type": "TEXT NOT NULL DEFAULT 'manual'",
        "filter_json": "TEXT NOT NULL DEFAULT '{}'",
    })


def _files(conn: sqlite3.Connection) -> None:
    _add_columns(conn, "files", {
        "original_filename": "TEXT", "runtime_seconds": "REAL", "width": "INTEGER",
        "height": "INTEGER", "video_codec": "TEXT", "audio_codec": "TEXT",
        "audio_channels": "INTEGER", "bitrate": "INTEGER", "container": "TEXT",
        "dynamic_range": "TEXT", "media_info_at": "TEXT", "media_info_error": "TEXT",
        "edition_name": "TEXT NOT NULL DEFAULT ''", "version_name": "TEXT NOT NULL DEFAULT ''",
        "identity_confirmed": "INTEGER NOT NULL DEFAULT 0",
        "version_preferred": "INTEGER NOT NULL DEFAULT 0",
    })
    conn.execute(
        "UPDATE files SET original_filename=filename WHERE original_filename IS NULL OR original_filename=''"
    )


def _episodes(conn: sqlite3.Connection) -> None:
    _add_columns(conn, "expected_episodes", {"imdb_id": "TEXT"})


def _users(conn: sqlite3.Connection) -> None:
    _add_columns(conn, "users", {
        "home_layout": "TEXT NOT NULL DEFAULT 'modern'",
        "show_home_hero": "INTEGER NOT NULL DEFAULT 1",
        "high_contrast": "INTEGER NOT NULL DEFAULT 0",
    })


def _title_state(conn: sqlite3.Connection) -> None:
    _add_columns(conn, "user_title_state", {"sort_title": "TEXT"})


def _duplicate_reviews(conn: sqlite3.Connection) -> None:
    _add_columns(conn, "duplicate_reviews", {
        "review_source": "TEXT NOT NULL DEFAULT 'manual'",
    })


def _duplicate_trash(conn: sqlite3.Connection) -> None:
    if "size_bytes" not in _columns(conn, "duplicate_trash"):
        conn.execute("ALTER TABLE duplicate_trash ADD COLUMN size_bytes INTEGER NOT NULL DEFAULT 0")
        for row in conn.execute("SELECT id,file_snapshot FROM duplicate_trash WHERE size_bytes=0").fetchall():
            try:
                snapshot = json.loads(row["file_snapshot"] or "{}")
                size_bytes = max(0, int(snapshot.get("size_bytes") or 0))
            except (TypeError, ValueError, json.JSONDecodeError):
                size_bytes = 0
            conn.execute("UPDATE duplicate_trash SET size_bytes=? WHERE id=?", (size_bytes, row["id"]))


def _runtime_lease(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS runtime_leases (
             name TEXT PRIMARY KEY,
             owner TEXT NOT NULL,
             heartbeat_at TEXT NOT NULL
           )"""
    )


def _login_lockouts(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS login_lockouts (
             scope TEXT NOT NULL CHECK(scope IN ('identity','ip')),
             lock_key TEXT NOT NULL,
             locked_until TEXT NOT NULL,
             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
             PRIMARY KEY(scope,lock_key)
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_login_lockouts_until ON login_lockouts(locked_until)"
    )


def _user_saved_views(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS user_saved_views (
             id INTEGER PRIMARY KEY,
             user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
             name TEXT NOT NULL COLLATE NOCASE,
             path TEXT NOT NULL CHECK(path IN ('/library','/movies','/shows')),
             query_string TEXT NOT NULL DEFAULT '',
             pinned INTEGER NOT NULL DEFAULT 0,
             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
             updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
             UNIQUE(user_id,name)
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_user_saved_views_user
           ON user_saved_views(user_id,pinned DESC,name COLLATE NOCASE)"""
    )


def _operation_history(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS operation_history (
             id INTEGER PRIMARY KEY,
             operation_type TEXT NOT NULL,
             status TEXT NOT NULL DEFAULT 'completed'
               CHECK(status IN ('completed','undoing','undone')),
             summary TEXT NOT NULL,
             detail TEXT NOT NULL DEFAULT '',
             actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
             title_id INTEGER REFERENCES titles(id) ON DELETE SET NULL,
             file_id INTEGER,
             root_id INTEGER REFERENCES roots(id) ON DELETE SET NULL,
             undo_kind TEXT,
             undo_payload TEXT NOT NULL DEFAULT '{}',
             undo_error TEXT NOT NULL DEFAULT '',
             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
             undone_at TEXT,
             undone_by INTEGER REFERENCES users(id) ON DELETE SET NULL
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_operation_history_recent ON operation_history(created_at DESC,id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_operation_history_status ON operation_history(status,operation_type,id DESC)"
    )


def _rename_proposals(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS rename_proposals (
             id INTEGER PRIMARY KEY,
             file_id INTEGER NOT NULL UNIQUE REFERENCES files(id) ON DELETE CASCADE,
             title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
             root_id INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
             proposal_kind TEXT NOT NULL CHECK(proposal_kind IN ('movie','episode')),
             source_path TEXT NOT NULL,
             destination_path TEXT NOT NULL,
             source_size INTEGER NOT NULL DEFAULT 0,
             source_mtime_ns INTEGER NOT NULL DEFAULT 0,
             status TEXT NOT NULL DEFAULT 'active'
               CHECK(status IN ('active','blocked','dismissed','resolved','applied','stale')),
             reason TEXT NOT NULL DEFAULT '',
             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
             updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
             last_checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rename_proposals_review ON rename_proposals(status,updated_at DESC,id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rename_proposals_title ON rename_proposals(title_id,status,id)"
    )


def _library_read_indexes(conn: sqlite3.Connection) -> None:
    """Add covering/read-path indexes used by the 0.8 Library hot paths.

    These target cache-signature checks and missing-episode/file-range lookups. They
    intentionally avoid write-heavy or low-selectivity indexes that would cost every
    scan without helping a measured Library query.
    """
    statements = (
        "CREATE INDEX IF NOT EXISTS idx_titles_updated ON titles(updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_user_title_state_updated ON user_title_state(user_id,updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_app_settings_updated ON app_settings(updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_announcements_updated ON announcements(updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_title_tags_tag ON title_tags(tag_id,title_id)",
        "CREATE INDEX IF NOT EXISTS idx_expected_aired_lookup ON expected_episodes(title_id,season,aired,episode)",
        "CREATE INDEX IF NOT EXISTS idx_files_episode_range ON files(title_id,season,episode_start,episode_end)",
    )
    for statement in statements:
        conn.execute(statement)


def _shared_chrome_indexes(conn: sqlite3.Connection) -> None:
    """Index tiny-but-frequent queries that are executed for global application chrome."""
    statements = (
        "CREATE INDEX IF NOT EXISTS idx_event_logs_activity ON event_logs(category,user_id,id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_announcements_due ON announcements(active,audience,starts_at,ends_at,id)",
    )
    for statement in statements:
        conn.execute(statement)


def _announcement_onboarding_receipts(conn: sqlite3.Connection) -> None:
    """Do not treat release notes from before an account existed as unread."""
    conn.execute(
        """INSERT OR IGNORE INTO announcement_receipts(announcement_id,user_id)
           SELECT a.id,u.id
           FROM announcements a
           JOIN users u ON datetime(u.created_at) > datetime(a.starts_at)
           WHERE a.source='official'"""
    )
    conn.execute(
        """CREATE TRIGGER IF NOT EXISTS trg_users_skip_historical_official_announcements
           AFTER INSERT ON users
           BEGIN
             INSERT OR IGNORE INTO announcement_receipts(announcement_id,user_id)
             SELECT id,NEW.id FROM announcements
             WHERE source='official'
               AND datetime(NEW.created_at) > datetime(starts_at);
           END"""
    )
    conn.execute(
        """CREATE TRIGGER IF NOT EXISTS trg_official_announcements_skip_newer_users
           AFTER INSERT ON announcements
           WHEN NEW.source='official'
           BEGIN
             INSERT OR IGNORE INTO announcement_receipts(announcement_id,user_id)
             SELECT NEW.id,id FROM users
             WHERE datetime(created_at) > datetime(NEW.starts_at);
           END"""
    )


def _intelligence_09(conn: sqlite3.Connection) -> None:
    """Add the additive storage model for 0.9 media intelligence."""
    _add_columns(conn, "mie_analysis_runs", {
        "opened_findings": "INTEGER NOT NULL DEFAULT 0",
        "resolved_findings": "INTEGER NOT NULL DEFAULT 0",
    })
    conn.execute(
        """CREATE TABLE IF NOT EXISTS media_streams (
             file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
             stream_index INTEGER NOT NULL,
             stream_type TEXT NOT NULL,
             codec TEXT NOT NULL DEFAULT '',
             language TEXT NOT NULL DEFAULT 'und',
             title TEXT NOT NULL DEFAULT '',
             channels INTEGER,
             channel_layout TEXT NOT NULL DEFAULT '',
             sample_rate INTEGER,
             default_flag INTEGER NOT NULL DEFAULT 0,
             forced_flag INTEGER NOT NULL DEFAULT 0,
             hearing_impaired INTEGER NOT NULL DEFAULT 0,
             visual_impaired INTEGER NOT NULL DEFAULT 0,
             commentary INTEGER NOT NULL DEFAULT 0,
             disposition_json TEXT NOT NULL DEFAULT '{}',
             PRIMARY KEY(file_id,stream_index)
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_media_streams_file_type ON media_streams(file_id,stream_type,language)"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS media_integrity_results (
             file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
             status TEXT NOT NULL CHECK(status IN ('passed','warning','failed','error')),
             mode TEXT NOT NULL CHECK(mode IN ('sample','full')),
             checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
             checked_modified_at REAL,
             checked_size_bytes INTEGER NOT NULL DEFAULT 0,
             issue_count INTEGER NOT NULL DEFAULT 0,
             details_json TEXT NOT NULL DEFAULT '{}'
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_media_integrity_status ON media_integrity_results(status,checked_at DESC)"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS mie_title_health_snapshots (
             run_id INTEGER NOT NULL REFERENCES mie_analysis_runs(id) ON DELETE CASCADE,
             title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
             score INTEGER NOT NULL DEFAULT 100,
             critical_count INTEGER NOT NULL DEFAULT 0,
             warning_count INTEGER NOT NULL DEFAULT 0,
             information_count INTEGER NOT NULL DEFAULT 0,
             PRIMARY KEY(run_id,title_id)
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mie_title_health_title ON mie_title_health_snapshots(title_id,run_id DESC)"
    )


MIGRATIONS = (
    additive_migration(1, "title metadata columns", _titles),
    additive_migration(2, "source health columns", _roots),
    additive_migration(3, "collection filters", _collections),
    additive_migration(4, "media technical and edition columns", _files),
    additive_migration(5, "episode IMDb identity", _episodes),
    additive_migration(6, "user presentation preferences", _users),
    additive_migration(7, "user title sort keys", _title_state),
    additive_migration(8, "duplicate review ownership", _duplicate_reviews),
    additive_migration(9, "duplicate trash size accounting", _duplicate_trash),
    additive_migration(10, "single-runtime lease", _runtime_lease),
    additive_migration(11, "persistent aggregate login lockouts", _login_lockouts),
    additive_migration(12, "user saved library views", _user_saved_views),
    additive_migration(13, "operation history and safe undo", _operation_history),
    additive_migration(14, "persisted global rename proposals", _rename_proposals),
    additive_migration(15, "library read-path indexes", _library_read_indexes),
    additive_migration(16, "shared chrome read indexes", _shared_chrome_indexes),
    behavioral_migration(17, "historical announcement onboarding receipts", _announcement_onboarding_receipts),
    additive_migration(18, "0.9 intelligence foundation", _intelligence_09),
    additive_migration(19, "0.9 media identity foundation", apply_media_identity_foundation),
    additive_migration(20, "0.9 provider episode identity cache", apply_provider_episode_cache),
    additive_migration(21, "0.9 external analysis source foundation", apply_external_source_foundation),
)

CURRENT_SCHEMA_VERSION = max(migration.version for migration in MIGRATIONS)


def validate_migration_contracts() -> None:
    seen: set[int] = set()
    for migration in MIGRATIONS:
        if migration.version in seen:
            raise RuntimeError(f"Duplicate migration version {migration.version}.")
        seen.add(migration.version)
        if migration.compatibility not in COMPATIBILITY_LEVELS:
            raise RuntimeError(f"Migration {migration.version} has an invalid compatibility class.")
        if migration.downgrade_policy not in DOWNGRADE_POLICIES:
            raise RuntimeError(f"Migration {migration.version} has an invalid downgrade policy.")
        if migration.minimum_reader_schema < 1 or migration.minimum_reader_schema > migration.version:
            raise RuntimeError(f"Migration {migration.version} has an invalid minimum reader schema.")
        if (
            migration.minimum_writer_schema is not None
            and (
                migration.minimum_writer_schema < migration.minimum_reader_schema
                or migration.minimum_writer_schema > migration.version
            )
        ):
            raise RuntimeError(f"Migration {migration.version} has an invalid minimum writer schema.")
        if migration.downgrade_policy == "compatible" and migration.minimum_writer_schema is None:
            raise RuntimeError(f"Migration {migration.version} cannot be write-compatible without a writer schema.")
        if migration.downgrade_policy == "read_only" and migration.minimum_writer_schema is not None:
            raise RuntimeError(f"Migration {migration.version} read-only policy must omit a writer schema.")


def _ensure_compatibility_ledger(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_compatibility (
             migration_version INTEGER PRIMARY KEY,
             migration_name TEXT NOT NULL,
             compatibility TEXT NOT NULL
               CHECK(compatibility IN ('additive','behavioral','breaking')),
             minimum_reader_schema INTEGER NOT NULL,
             minimum_writer_schema INTEGER,
             downgrade_policy TEXT NOT NULL
               CHECK(downgrade_policy IN ('compatible','read_only','restore_required')),
             recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )


def _record_compatibility(conn: sqlite3.Connection, migration: Migration) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO schema_compatibility(
             migration_version,migration_name,compatibility,
             minimum_reader_schema,minimum_writer_schema,downgrade_policy
           ) VALUES (?,?,?,?,?,?)""",
        (
            migration.version,
            migration.name,
            migration.compatibility,
            migration.minimum_reader_schema,
            migration.minimum_writer_schema,
            migration.downgrade_policy,
        ),
    )


def sync_schema_compatibility_ledger(conn: sqlite3.Connection) -> None:
    """Backfill compatibility snapshots for already-applied known migrations.

    INSERT OR IGNORE is intentional. Once a migration has been applied, the installed
    database keeps the compatibility declaration that accompanied that migration.
    Later code changes do not silently rewrite its downgrade history.
    """
    validate_migration_contracts()
    _ensure_compatibility_ledger(conn)
    applied = {int(row[0]) for row in conn.execute("SELECT version FROM schema_migrations")}
    for migration in MIGRATIONS:
        if migration.version in applied:
            _record_compatibility(conn, migration)


def schema_contract() -> dict:
    """Return the compatibility contract for a database at this build's schema."""
    validate_migration_contracts()
    minimum_reader = max(migration.minimum_reader_schema for migration in MIGRATIONS)
    writer_values = [migration.minimum_writer_schema for migration in MIGRATIONS]
    minimum_writer = None if any(value is None for value in writer_values) else max(writer_values)
    policy = "compatible"
    if any(migration.downgrade_policy == "restore_required" for migration in MIGRATIONS):
        policy = "restore_required"
    elif any(migration.downgrade_policy == "read_only" for migration in MIGRATIONS):
        policy = "read_only"
    return {
        "current": CURRENT_SCHEMA_VERSION,
        "minimum_reader_schema": minimum_reader,
        "minimum_writer_schema": minimum_writer,
        "downgrade_policy": policy,
    }


def assess_schema_downgrade(conn: sqlite3.Connection, target_schema: int) -> dict:
    """Assess whether an application built for target_schema may use this database."""
    if target_schema < 1:
        raise ValueError("Target schema must be positive.")
    sync_schema_compatibility_ledger(conn)
    applied_versions = sorted(
        int(row[0]) for row in conn.execute("SELECT version FROM schema_migrations")
    )
    current_schema = applied_versions[-1] if applied_versions else 0
    if target_schema >= current_schema:
        return {
            "status": "current" if target_schema == current_schema else "upgrade",
            "current_schema": current_schema,
            "target_schema": target_schema,
            "blocking_migrations": [],
        }

    rows = conn.execute(
        """SELECT migration_version,migration_name,compatibility,
                  minimum_reader_schema,minimum_writer_schema,downgrade_policy
           FROM schema_compatibility
           WHERE migration_version>? AND migration_version<=?
           ORDER BY migration_version""",
        (target_schema, current_schema),
    ).fetchall()
    by_version = {int(row["migration_version"]): row for row in rows}
    unknown = [version for version in applied_versions if target_schema < version <= current_schema and version not in by_version]
    if unknown:
        return {
            "status": "restore_required",
            "current_schema": current_schema,
            "target_schema": target_schema,
            "blocking_migrations": unknown,
            "reason": "compatibility_unknown",
        }

    read_blockers = [
        row for row in rows
        if target_schema < int(row["minimum_reader_schema"])
        or row["downgrade_policy"] == "restore_required"
    ]
    if read_blockers:
        return {
            "status": "restore_required",
            "current_schema": current_schema,
            "target_schema": target_schema,
            "blocking_migrations": [int(row["migration_version"]) for row in read_blockers],
            "reason": "reader_incompatible",
        }

    write_blockers = [
        row for row in rows
        if row["minimum_writer_schema"] is None
        or target_schema < int(row["minimum_writer_schema"])
        or row["downgrade_policy"] == "read_only"
    ]
    if write_blockers:
        return {
            "status": "read_only",
            "current_schema": current_schema,
            "target_schema": target_schema,
            "blocking_migrations": [int(row["migration_version"]) for row in write_blockers],
            "reason": "writer_incompatible",
        }

    return {
        "status": "safe_downgrade",
        "current_schema": current_schema,
        "target_schema": target_schema,
        "blocking_migrations": [],
    }


def schema_compatibility_history(conn: sqlite3.Connection) -> list[dict]:
    sync_schema_compatibility_ledger(conn)
    return [dict(row) for row in conn.execute(
        """SELECT migration_version,migration_name,compatibility,
                  minimum_reader_schema,minimum_writer_schema,downgrade_policy,recorded_at
           FROM schema_compatibility ORDER BY migration_version"""
    )]


def apply_migrations(conn: sqlite3.Connection) -> None:
    validate_migration_contracts()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
             version INTEGER PRIMARY KEY,
             name TEXT NOT NULL,
             applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    sync_schema_compatibility_ledger(conn)
    applied = {int(row[0]) for row in conn.execute("SELECT version FROM schema_migrations")}
    for migration in MIGRATIONS:
        if migration.version in applied:
            continue
        savepoint = f"migration_{migration.version}"
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            migration.apply(conn)
            conn.execute(
                "INSERT INTO schema_migrations(version,name) VALUES (?,?)",
                (migration.version, migration.name),
            )
            _record_compatibility(conn, migration)
        except Exception:
            conn.execute(f"ROLLBACK TO {savepoint}")
            conn.execute(f"RELEASE {savepoint}")
            raise
        conn.execute(f"RELEASE {savepoint}")

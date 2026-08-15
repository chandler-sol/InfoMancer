from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


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


MIGRATIONS = (
    Migration(1, "title metadata columns", _titles),
    Migration(2, "source health columns", _roots),
    Migration(3, "collection filters", _collections),
    Migration(4, "media technical and edition columns", _files),
    Migration(5, "episode IMDb identity", _episodes),
    Migration(6, "user presentation preferences", _users),
    Migration(7, "user title sort keys", _title_state),
    Migration(8, "duplicate review ownership", _duplicate_reviews),
    Migration(9, "duplicate trash size accounting", _duplicate_trash),
    Migration(10, "single-runtime lease", _runtime_lease),
)


def apply_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
             version INTEGER PRIMARY KEY,
             name TEXT NOT NULL,
             applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )
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
        except Exception:
            conn.execute(f"ROLLBACK TO {savepoint}")
            conn.execute(f"RELEASE {savepoint}")
            raise
        conn.execute(f"RELEASE {savepoint}")

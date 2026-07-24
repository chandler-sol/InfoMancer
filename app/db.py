from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS roots (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK(kind IN ('movie', 'tv')),
    label TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_scanned_at TEXT
);

CREATE TABLE IF NOT EXISTS titles (
    id INTEGER PRIMARY KEY,
    root_id INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('movie', 'tv')),
    title TEXT NOT NULL,
    year INTEGER,
    end_year INTEGER,
    continuing INTEGER,
    folder_path TEXT NOT NULL UNIQUE,
    tvdb_id INTEGER,
    tvdb_movie_id INTEGER,
    tmdb_id TEXT,
    imdb_id TEXT,
    imdb_checked_at TEXT,
    genres TEXT,
    imdb_title_type TEXT,
    imdb_rating REAL,
    imdb_votes INTEGER,
    poster_url TEXT,
    metadata_title TEXT,
    metadata_title_language TEXT,
    metadata_year INTEGER,
    metadata_end_year INTEGER,
    metadata_continuing INTEGER,
    metadata_status TEXT,
    matched_at TEXT,
    discovered_at TEXT,
    last_scanned_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    path TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    extension TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    modified_at REAL,
    season INTEGER,
    episode_start INTEGER,
    episode_end INTEGER,
    parsed_title TEXT,
    original_filename TEXT,
    runtime_seconds REAL,
    width INTEGER,
    height INTEGER,
    video_codec TEXT,
    audio_codec TEXT,
    audio_channels INTEGER,
    bitrate INTEGER,
    container TEXT,
    dynamic_range TEXT,
    media_info_at TEXT,
    media_info_error TEXT,
    seen_scan TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expected_episodes (
    id INTEGER PRIMARY KEY,
    title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    tvdb_episode_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    episode INTEGER NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    aired TEXT,
    imdb_id TEXT,
    UNIQUE(title_id, season, episode)
);

CREATE TABLE IF NOT EXISTS title_credits (
    id INTEGER PRIMARY KEY,
    title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    imdb_person_id TEXT NOT NULL,
    person_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('actor', 'director', 'writer')),
    billing_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE(title_id, imdb_person_id, role)
);

CREATE TABLE IF NOT EXISTS episode_credits (
    id INTEGER PRIMARY KEY,
    expected_episode_id INTEGER NOT NULL REFERENCES expected_episodes(id) ON DELETE CASCADE,
    imdb_person_id TEXT NOT NULL,
    person_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('director', 'writer')),
    billing_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE(expected_episode_id, imdb_person_id, role)
);

CREATE TABLE IF NOT EXISTS movie_match_suggestions (
    title_id INTEGER PRIMARY KEY REFERENCES titles(id) ON DELETE CASCADE,
    candidate_json TEXT,
    confidence_score INTEGER,
    confidence_label TEXT,
    result_count INTEGER NOT NULL DEFAULT 0,
    exact INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    analyzed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tv_match_suggestions (
    title_id INTEGER PRIMARY KEY REFERENCES titles(id) ON DELETE CASCADE,
    candidate_json TEXT,
    confidence_score INTEGER,
    confidence_label TEXT,
    result_count INTEGER NOT NULL DEFAULT 0,
    exact INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    analyzed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    email TEXT COLLATE NOCASE UNIQUE,
    display_name TEXT NOT NULL,
    profile_icon TEXT NOT NULL DEFAULT 'initials',
    password_hash TEXT,
    role TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('member', 'librarian')),
    active INTEGER NOT NULL DEFAULT 1,
    force_password_change INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at TEXT,
    password_changed_at TEXT
);

CREATE TABLE IF NOT EXISTS auth_identities (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    subject TEXT NOT NULL,
    email TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at TEXT,
    UNIQUE(provider, subject),
    UNIQUE(user_id, provider)
);

CREATE TABLE IF NOT EXISTS user_sessions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL,
    user_agent TEXT NOT NULL DEFAULT '',
    ip_address TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS login_attempts (
    identity TEXT NOT NULL,
    ip_address TEXT NOT NULL DEFAULT '',
    failures INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    locked_until TEXT,
    PRIMARY KEY(identity, ip_address)
);

CREATE TABLE IF NOT EXISTS account_invitations (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    revoked_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_setting_changes (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL,
    old_value TEXT NOT NULL DEFAULT '',
    new_value TEXT NOT NULL,
    changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'installation'
        CHECK(source IN ('official', 'installation')),
    source_key TEXT UNIQUE,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'information'
        CHECK(category IN ('information', 'update', 'important')),
    audience TEXT NOT NULL DEFAULT 'members'
        CHECK(audience IN ('all', 'members', 'librarians')),
    starts_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ends_at TEXT,
    recurrence_days INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS announcement_receipts (
    announcement_id INTEGER NOT NULL REFERENCES announcements(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    delivery_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(announcement_id, user_id)
);

CREATE TABLE IF NOT EXISTS user_tour_state (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tour_key TEXT NOT NULL,
    completed_at TEXT,
    dismissed_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id, tour_key)
);

CREATE TABLE IF NOT EXISTS user_setup_state (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    mode TEXT NOT NULL DEFAULT 'guided'
        CHECK(mode IN ('guided', 'manual')),
    current_step TEXT NOT NULL DEFAULT 'general'
        CHECK(current_step IN ('general', 'metadata', 'sources', 'finish')),
    completed_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_title_state (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    favorite INTEGER NOT NULL DEFAULT 0,
    personal_rating REAL,
    custom_order INTEGER,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id, title_id),
    CHECK(personal_rating IS NULL OR (personal_rating >= 0 AND personal_rating <= 10))
);

CREATE TABLE IF NOT EXISTS user_tags (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL COLLATE NOCASE,
    color TEXT NOT NULL DEFAULT 'lime',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS title_tags (
    title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES user_tags(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(title_id, tag_id)
);

CREATE TABLE IF NOT EXISTS event_logs (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    level TEXT NOT NULL CHECK(level IN ('debug', 'info', 'warning', 'error')),
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    context_json TEXT NOT NULL DEFAULT '{}',
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_titles_search ON titles(title, metadata_title);
CREATE INDEX IF NOT EXISTS idx_titles_kind_root ON titles(kind, root_id);
CREATE INDEX IF NOT EXISTS idx_titles_root ON titles(root_id);
CREATE INDEX IF NOT EXISTS idx_files_episode ON files(title_id, season, episode_start);
CREATE INDEX IF NOT EXISTS idx_files_title_filename ON files(title_id, filename);
CREATE INDEX IF NOT EXISTS idx_expected_episode ON expected_episodes(title_id, season, episode);
CREATE INDEX IF NOT EXISTS idx_expected_aired ON expected_episodes(title_id, season, aired);
CREATE INDEX IF NOT EXISTS idx_title_credits_title ON title_credits(title_id, role, billing_order);
CREATE INDEX IF NOT EXISTS idx_title_credits_person ON title_credits(imdb_person_id, role, title_id);
CREATE INDEX IF NOT EXISTS idx_title_credits_name ON title_credits(person_name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_episode_credits_episode ON episode_credits(expected_episode_id, role, billing_order);
CREATE INDEX IF NOT EXISTS idx_movie_suggestions_analyzed ON movie_match_suggestions(analyzed_at);
CREATE INDEX IF NOT EXISTS idx_tv_suggestions_analyzed ON tv_match_suggestions(analyzed_at);
CREATE INDEX IF NOT EXISTS idx_users_role_active ON users(role, active);
CREATE INDEX IF NOT EXISTS idx_auth_identities_user ON auth_identities(user_id, provider);
CREATE INDEX IF NOT EXISTS idx_user_sessions_user ON user_sessions(user_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_user_sessions_expiry ON user_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_account_invitations_user
    ON account_invitations(user_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_app_setting_changes_time
    ON app_setting_changes(changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_announcements_delivery
    ON announcements(active, starts_at, ends_at, audience);
CREATE INDEX IF NOT EXISTS idx_announcement_receipts_user
    ON announcement_receipts(user_id, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_user_title_state_favorite
    ON user_title_state(user_id, favorite, title_id);
CREATE INDEX IF NOT EXISTS idx_user_title_state_order
    ON user_title_state(user_id, custom_order, title_id);
CREATE INDEX IF NOT EXISTS idx_user_tags_name
    ON user_tags(user_id, name);
CREATE INDEX IF NOT EXISTS idx_title_tags_title
    ON title_tags(title_id, tag_id);
CREATE INDEX IF NOT EXISTS idx_event_logs_time
    ON event_logs(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_event_logs_category
    ON event_logs(category, level, created_at DESC);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            # SQLite's CREATE TABLE IF NOT EXISTS does not add columns to an
            # existing catalog, so keep these lightweight migrations explicit.
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(titles)")}
            additions = {
                "end_year": "INTEGER",
                "continuing": "INTEGER",
                "metadata_end_year": "INTEGER",
                "metadata_continuing": "INTEGER",
                "metadata_status": "TEXT",
                "tvdb_movie_id": "INTEGER",
                "tmdb_id": "TEXT",
                "imdb_id": "TEXT",
                "imdb_checked_at": "TEXT",
                "genres": "TEXT",
                "imdb_title_type": "TEXT",
                "imdb_rating": "REAL",
                "imdb_votes": "INTEGER",
                "poster_url": "TEXT",
                "metadata_title_language": "TEXT",
                "discovered_at": "TEXT",
                "last_scanned_at": "TEXT",
            }
            for name, column_type in additions.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE titles ADD COLUMN {name} {column_type}")
            file_columns = {row["name"] for row in conn.execute("PRAGMA table_info(files)")}
            file_additions = {
                "original_filename": "TEXT",
                "runtime_seconds": "REAL",
                "width": "INTEGER",
                "height": "INTEGER",
                "video_codec": "TEXT",
                "audio_codec": "TEXT",
                "audio_channels": "INTEGER",
                "bitrate": "INTEGER",
                "container": "TEXT",
                "dynamic_range": "TEXT",
                "media_info_at": "TEXT",
                "media_info_error": "TEXT",
            }
            for name, column_type in file_additions.items():
                if name not in file_columns:
                    conn.execute(f"ALTER TABLE files ADD COLUMN {name} {column_type}")
            conn.execute(
                """UPDATE files SET original_filename=filename
                   WHERE original_filename IS NULL OR original_filename=''"""
            )
            episode_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(expected_episodes)")
            }
            if "imdb_id" not in episode_columns:
                conn.execute("ALTER TABLE expected_episodes ADD COLUMN imdb_id TEXT")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

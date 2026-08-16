from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .migrations import apply_migrations


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS roots (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK(kind IN ('movie', 'tv')),
    label TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_scanned_at TEXT,
    health_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK(health_status IN ('unknown','healthy','degraded','offline')),
    last_checked_at TEXT,
    last_seen_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    last_file_count INTEGER NOT NULL DEFAULT 0,
    last_observed_file_count INTEGER NOT NULL DEFAULT 0,
    guard_preserved_count INTEGER NOT NULL DEFAULT 0
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
    metadata_refreshed_at TEXT,
    metadata_refresh_error TEXT NOT NULL DEFAULT '',
    metadata_provider TEXT NOT NULL DEFAULT '',
    overview TEXT,
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
    edition_name TEXT NOT NULL DEFAULT '',
    version_name TEXT NOT NULL DEFAULT '',
    identity_confirmed INTEGER NOT NULL DEFAULT 0,
    version_preferred INTEGER NOT NULL DEFAULT 0,
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
    home_layout TEXT NOT NULL DEFAULT 'modern'
        CHECK(home_layout IN ('modern', 'classic')),
    show_home_hero INTEGER NOT NULL DEFAULT 1,
    high_contrast INTEGER NOT NULL DEFAULT 0,
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
    sort_title TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id, title_id),
    CHECK(personal_rating IS NULL OR (personal_rating >= 0 AND personal_rating <= 10))
);

CREATE TABLE IF NOT EXISTS user_episode_favorites (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expected_episode_id INTEGER NOT NULL REFERENCES expected_episodes(id) ON DELETE CASCADE,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id, expected_episode_id)
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

CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    artwork_filename TEXT,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS collection_titles (
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(collection_id, title_id)
);

CREATE TABLE IF NOT EXISTS collection_episodes (
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    expected_episode_id INTEGER NOT NULL REFERENCES expected_episodes(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(collection_id, expected_episode_id)
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

CREATE TABLE IF NOT EXISTS custom_libraries (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    library_kind TEXT NOT NULL DEFAULT 'mixed'
        CHECK(library_kind IN ('movie','tv','mixed')),
    description TEXT NOT NULL DEFAULT '',
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    collection_type TEXT NOT NULL DEFAULT 'manual',
    filter_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS custom_library_titles (
    library_id INTEGER NOT NULL REFERENCES custom_libraries(id) ON DELETE CASCADE,
    title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(library_id,title_id)
);

CREATE TABLE IF NOT EXISTS user_event_reads (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id INTEGER NOT NULL REFERENCES event_logs(id) ON DELETE CASCADE,
    read_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id, event_id)
);

CREATE TABLE IF NOT EXISTS metadata_refresh_queue (
    title_id INTEGER PRIMARY KEY REFERENCES titles(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK(status IN ('queued','running','complete','failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    requested_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    completed_at TEXT,
    provider TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS user_search_history (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    query TEXT NOT NULL COLLATE NOCASE,
    searched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, query)
);

CREATE TABLE IF NOT EXISTS user_saved_views (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL COLLATE NOCASE,
    path TEXT NOT NULL CHECK(path IN ('/library','/movies','/shows')),
    query_string TEXT NOT NULL DEFAULT '',
    pinned INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS operation_history (
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
);

CREATE TABLE IF NOT EXISTS rename_proposals (
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
);

CREATE TABLE IF NOT EXISTS mie_findings (
    id INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    rule_key TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('critical', 'warning', 'information')),
    root_id INTEGER REFERENCES roots(id) ON DELETE CASCADE,
    title_id INTEGER REFERENCES titles(id) ON DELETE CASCADE,
    file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
    expected_episode_id INTEGER REFERENCES expected_episodes(id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    explanation TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'dismissed', 'resolved')),
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    dismissed_at TEXT,
    dismissed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS mie_analysis_state (
    id INTEGER PRIMARY KEY CHECK(id=1),
    last_analyzed_at TEXT,
    finding_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS mie_quality_profiles (
    root_id INTEGER PRIMARY KEY REFERENCES roots(id) ON DELETE CASCADE,
    minimum_width INTEGER,
    minimum_height INTEGER,
    minimum_bitrate INTEGER,
    preferred_video_codecs TEXT NOT NULL DEFAULT '',
    preferred_containers TEXT NOT NULL DEFAULT '',
    minimum_audio_channels INTEGER,
    dynamic_range TEXT NOT NULL DEFAULT 'any'
        CHECK(dynamic_range IN ('any', 'sdr', 'hdr')),
    detect_outliers INTEGER NOT NULL DEFAULT 1,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mie_calibration (
    id INTEGER PRIMARY KEY CHECK(id=1),
    identity_warning_threshold INTEGER NOT NULL DEFAULT 70,
    source_stale_hours INTEGER NOT NULL DEFAULT 24,
    critical_weight INTEGER NOT NULL DEFAULT 20,
    warning_weight INTEGER NOT NULL DEFAULT 8,
    information_weight INTEGER NOT NULL DEFAULT 2,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mie_analysis_runs (
    id INTEGER PRIMARY KEY,
    analyzed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    active_findings INTEGER NOT NULL DEFAULT 0,
    suppressed_findings INTEGER NOT NULL DEFAULT 0,
    overall_score INTEGER NOT NULL DEFAULT 100,
    opened_findings INTEGER NOT NULL DEFAULT 0,
    resolved_findings INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS mie_category_scores (
    run_id INTEGER NOT NULL REFERENCES mie_analysis_runs(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    score INTEGER NOT NULL,
    critical_count INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    information_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(run_id, category)
);

CREATE TABLE IF NOT EXISTS mie_feedback (
    id INTEGER PRIMARY KEY,
    finding_fingerprint TEXT NOT NULL,
    rule_key TEXT NOT NULL,
    root_id INTEGER REFERENCES roots(id) ON DELETE CASCADE,
    title_id INTEGER REFERENCES titles(id) ON DELETE CASCADE,
    file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
    reason TEXT NOT NULL CHECK(reason IN (
        'expected','incorrect','resolved_elsewhere','other'
    )),
    scope TEXT NOT NULL CHECK(scope IN ('finding','title','source')),
    note TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE IF NOT EXISTS media_streams (
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
);

CREATE TABLE IF NOT EXISTS media_integrity_results (
    file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK(status IN ('passed','warning','failed','error')),
    mode TEXT NOT NULL CHECK(mode IN ('sample','full')),
    checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    checked_modified_at REAL,
    checked_size_bytes INTEGER NOT NULL DEFAULT 0,
    issue_count INTEGER NOT NULL DEFAULT 0,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS mie_title_health_snapshots (
    run_id INTEGER NOT NULL REFERENCES mie_analysis_runs(id) ON DELETE CASCADE,
    title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    score INTEGER NOT NULL DEFAULT 100,
    critical_count INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    information_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(run_id,title_id)
);

CREATE TABLE IF NOT EXISTS duplicate_reviews (
    file_a_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    file_b_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    decision TEXT NOT NULL DEFAULT 'active'
        CHECK(decision IN ('active', 'ignored', 'not_duplicate')),
    file_a_signature TEXT NOT NULL DEFAULT '',
    file_b_signature TEXT NOT NULL DEFAULT '',
    file_a_sha256 TEXT,
    file_b_sha256 TEXT,
    verified_at TEXT,
    reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    review_source TEXT NOT NULL DEFAULT 'manual',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(file_a_id, file_b_id),
    CHECK(file_a_id < file_b_id)
);

CREATE TABLE IF NOT EXISTS media_file_hashes (
    file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    sha256 TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    modified_at REAL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK(status IN ('queued', 'running', 'complete', 'error')),
    error TEXT NOT NULL DEFAULT '',
    queued_at TEXT,
    hashed_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS duplicate_trash (
    id INTEGER PRIMARY KEY,
    original_file_id INTEGER,
    title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    root_id INTEGER REFERENCES roots(id) ON DELETE SET NULL,
    original_path TEXT NOT NULL,
    trash_path TEXT NOT NULL UNIQUE,
    file_snapshot TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'trashed'
        CHECK(status IN ('trashed', 'restored', 'purged', 'missing')),
    moved_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    moved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    purge_after TEXT,
    restored_at TEXT,
    purged_at TEXT
);

CREATE TABLE IF NOT EXISTS duplicate_manual_removals (
    id INTEGER PRIMARY KEY,
    original_file_id INTEGER,
    title_id INTEGER REFERENCES titles(id) ON DELETE SET NULL,
    root_id INTEGER REFERENCES roots(id) ON DELETE SET NULL,
    path TEXT NOT NULL,
    filename TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    verified_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    verified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_titles_search ON titles(title, metadata_title);
CREATE INDEX IF NOT EXISTS idx_titles_kind_root ON titles(kind, root_id);
CREATE INDEX IF NOT EXISTS idx_titles_root ON titles(root_id);
CREATE INDEX IF NOT EXISTS idx_files_episode ON files(title_id, season, episode_start);
CREATE INDEX IF NOT EXISTS idx_files_title_filename ON files(title_id, filename);
CREATE INDEX IF NOT EXISTS idx_duplicate_trash_status
    ON duplicate_trash(status, purge_after, moved_at DESC);
CREATE INDEX IF NOT EXISTS idx_duplicate_manual_removals_verified
    ON duplicate_manual_removals(verified_at DESC);
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
CREATE INDEX IF NOT EXISTS idx_user_episode_favorites_user
    ON user_episode_favorites(user_id, updated_at DESC, expected_episode_id);
CREATE INDEX IF NOT EXISTS idx_user_episode_favorites_episode
    ON user_episode_favorites(expected_episode_id, user_id);
CREATE INDEX IF NOT EXISTS idx_user_search_history_recent
    ON user_search_history(user_id, searched_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_user_saved_views_user
    ON user_saved_views(user_id, pinned DESC, name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_operation_history_recent
    ON operation_history(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_operation_history_status
    ON operation_history(status, operation_type, id DESC);
CREATE INDEX IF NOT EXISTS idx_rename_proposals_review
    ON rename_proposals(status, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_rename_proposals_title
    ON rename_proposals(title_id, status, id);
CREATE INDEX IF NOT EXISTS idx_user_tags_name
    ON user_tags(user_id, name);
CREATE INDEX IF NOT EXISTS idx_title_tags_title
    ON title_tags(title_id, tag_id);
CREATE INDEX IF NOT EXISTS idx_collections_name
    ON collections(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_collection_titles_order
    ON collection_titles(collection_id, position, title_id);
CREATE INDEX IF NOT EXISTS idx_collection_titles_title
    ON collection_titles(title_id, collection_id);
CREATE INDEX IF NOT EXISTS idx_collection_episodes_order
    ON collection_episodes(collection_id, position, expected_episode_id);
CREATE INDEX IF NOT EXISTS idx_collection_episodes_episode
    ON collection_episodes(expected_episode_id, collection_id);
CREATE INDEX IF NOT EXISTS idx_event_logs_time
    ON event_logs(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_event_logs_category
    ON event_logs(category, level, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mie_findings_status
    ON mie_findings(status, severity, category, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_mie_findings_title
    ON mie_findings(title_id, status);
CREATE INDEX IF NOT EXISTS idx_mie_findings_file
    ON mie_findings(file_id, status);
CREATE INDEX IF NOT EXISTS idx_mie_feedback_active
    ON mie_feedback(active, rule_key, scope);
CREATE INDEX IF NOT EXISTS idx_mie_analysis_runs_time
    ON mie_analysis_runs(analyzed_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_mie_title_health_title
    ON mie_title_health_snapshots(title_id, run_id DESC);
CREATE INDEX IF NOT EXISTS idx_media_streams_file_type
    ON media_streams(file_id, stream_type, language);
CREATE INDEX IF NOT EXISTS idx_media_integrity_status
    ON media_integrity_results(status, checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_duplicate_reviews_decision
    ON duplicate_reviews(decision, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_media_file_hashes_status
    ON media_file_hashes(status, updated_at);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            apply_migrations(conn)

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

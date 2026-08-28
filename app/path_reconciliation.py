from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .scanner import (
    VIDEO_EXTENSIONS,
    _is_movie_bucket,
    _readable_directory,
    _show_folder,
    _walk_files,
    movie_release_title,
    parse_episode,
    parse_title,
)


_ID_TAG_RE = re.compile(r"\{(tvdb|tmdb|imdb)-([^}]+)\}", re.I)
_MISSING_PATH_ERROR_PREFIX = "The media file is no longer available at its cataloged path."


def _file_exists(path: str) -> bool:
    try:
        candidate = Path(path)
        return candidate.exists() and candidate.is_file()
    except OSError:
        return False


def _identity_tags(filename: str) -> dict[str, str]:
    return {
        provider.casefold(): identifier.strip()
        for provider, identifier in _ID_TAG_RE.findall(filename)
        if identifier.strip()
    }


def _same_text(left: object, right: object) -> bool:
    return str(left or "").strip().casefold() == str(right or "").strip().casefold()


def _same_year(candidate: int | None, *values: object) -> bool:
    expected = {int(value) for value in values if value not in {None, ""}}
    if not expected:
        return candidate is None
    return candidate in expected


def _movie_identity_matches(row, candidate: Path) -> bool:
    tags = _identity_tags(candidate.name)
    if tags:
        provider_values = {
            "tmdb": row["tmdb_id"],
            "imdb": row["imdb_id"],
            "tvdb": row["tvdb_movie_id"],
        }
        for provider, identifier in tags.items():
            stored = provider_values.get(provider)
            if stored not in {None, ""} and str(stored).strip() == identifier:
                return True

    parsed = parse_title(movie_release_title(candidate.stem))
    title_matches = _same_text(parsed.title, row["title"]) or _same_text(
        parsed.title, row["metadata_title"]
    )
    return title_matches and _same_year(parsed.year, row["year"], row["metadata_year"])


def _tv_identity_matches(row, candidate: Path, root: Path) -> bool:
    parsed_episode = parse_episode(candidate.name)
    if row["season"] is not None:
        if (
            parsed_episode.season != row["season"]
            or parsed_episode.start != row["episode_start"]
            or parsed_episode.end != row["episode_end"]
        ):
            return False

    current_folder = Path(row["folder_path"])
    try:
        if candidate.is_relative_to(current_folder):
            return True
    except (OSError, ValueError):
        pass

    show_folder = _show_folder(root, candidate)
    parsed_show = parse_title(show_folder.name)
    title_matches = _same_text(parsed_show.title, row["title"]) or _same_text(
        parsed_show.title, row["metadata_title"]
    )
    return title_matches and _same_year(
        parsed_show.year, row["year"], row["metadata_year"]
    )


def _historical_sha256(row) -> str:
    """Return a reusable historical fingerprint only when it matches the old file signature."""
    if row["hash_status"] != "complete" or not row["historical_sha256"]:
        return ""
    try:
        if int(row["hash_size"] or 0) != int(row["size_bytes"] or 0):
            return ""
        if float(row["hash_modified"] or 0) != float(row["modified_at"] or 0):
            return ""
    except (TypeError, ValueError):
        return ""
    return str(row["historical_sha256"])


def _sha256_path(path: Path) -> str:
    """Fingerprint one ambiguity candidate without changing catalog state."""
    try:
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(4 * 1024 * 1024):
                digest.update(chunk)
        after = path.stat()
        if before.st_size != after.st_size or before.st_mtime != after.st_mtime:
            return ""
        return digest.hexdigest()
    except OSError:
        return ""


def missing_file_ids(db, root_id: int) -> list[int]:
    """Return catalog file ids whose current paths are not directly reachable."""
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT f.id, f.path
               FROM files f JOIN titles t ON t.id=f.title_id
               WHERE t.root_id=?""",
            (root_id,),
        ).fetchall()
    return [int(row["id"]) for row in rows if not _file_exists(row["path"])]


def clear_missing_path_failures(db, root_id: int) -> int:
    """Remove per-file path alerts when Source Guard owns the outage instead."""
    with db.connect() as conn:
        cursor = conn.execute(
            """UPDATE files
               SET media_info_error=NULL, media_info_at=NULL
               WHERE title_id IN (SELECT id FROM titles WHERE root_id=?)
                 AND media_info_error LIKE ?""",
            (root_id, f"{_MISSING_PATH_ERROR_PREFIX}%"),
        )
        return int(cursor.rowcount or 0)


def reconcile_root_paths(db, root_id: int) -> dict[str, int | bool]:
    """Reconnect renamed or moved files before a normal source scan.

    Cheap evidence is tried first: size plus provider/title/episode identity. When
    more than one candidate survives and the old catalog row has a current stored
    SHA-256 fingerprint, only those ambiguous candidates are hashed. A unique hash
    match is accepted; duplicate identical copies and unresolved ambiguity are left
    for review rather than guessed.
    """
    with db.connect() as conn:
        root_row = conn.execute(
            "SELECT id,path,kind FROM roots WHERE id=? AND enabled=1", (root_id,)
        ).fetchone()
        if not root_row:
            return {"available": False, "reconciled": 0, "hash_resolved": 0}
        rows = conn.execute(
            """SELECT f.id file_id, f.title_id, f.path, f.filename, f.size_bytes,
                      f.modified_at, f.season, f.episode_start, f.episode_end,
                      t.folder_path, t.title, t.year, t.metadata_title, t.metadata_year,
                      t.tmdb_id, t.imdb_id, t.tvdb_movie_id,
                      h.sha256 historical_sha256, h.status hash_status,
                      h.size_bytes hash_size, h.modified_at hash_modified
               FROM files f JOIN titles t ON t.id=f.title_id
               LEFT JOIN media_file_hashes h ON h.file_id=f.id
               WHERE t.root_id=?
               ORDER BY f.id""",
            (root_id,),
        ).fetchall()

    root = Path(root_row["path"])
    if not _readable_directory(root):
        return {"available": False, "reconciled": 0, "hash_resolved": 0}

    missing = [row for row in rows if not _file_exists(row["path"])]
    if not missing:
        return {"available": True, "reconciled": 0, "hash_resolved": 0}

    known_paths = {str(Path(row["path"])) for row in rows if _file_exists(row["path"])}
    walk_errors: list[str] = []
    candidates: list[dict[str, object]] = []
    for candidate in _walk_files(root, walk_errors):
        try:
            if not candidate.is_file() or candidate.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            if str(candidate) in known_paths:
                continue
            stat = candidate.stat()
        except OSError:
            continue
        candidates.append({"path": candidate, "size": int(stat.st_size)})

    # A partial directory walk is not enough evidence for identity decisions. Let
    # Source Guard's normal scan classify and preserve the source instead.
    if walk_errors:
        return {"available": True, "reconciled": 0, "hash_resolved": 0}

    used: set[str] = set()
    candidate_hashes: dict[str, str] = {}
    matches: list[tuple[object, Path, bool, str]] = []
    for row in missing:
        same_size = [
            item for item in candidates
            if str(item["path"]) not in used
            and int(item["size"]) == int(row["size_bytes"] or 0)
        ]
        if not same_size:
            continue

        if root_row["kind"] == "movie":
            identity_matches = [
                item for item in same_size
                if _movie_identity_matches(row, item["path"])
            ]
        else:
            identity_matches = [
                item for item in same_size
                if _tv_identity_matches(row, item["path"], root)
            ]

        matched_by_hash = False
        historical_hash = ""
        if len(identity_matches) > 1:
            historical_hash = _historical_sha256(row)
            if historical_hash:
                hash_matches: list[dict[str, object]] = []
                for item in identity_matches:
                    candidate = item["path"]
                    cache_key = str(candidate)
                    if cache_key not in candidate_hashes:
                        candidate_hashes[cache_key] = _sha256_path(candidate)
                    if candidate_hashes[cache_key] == historical_hash:
                        hash_matches.append(item)
                # Two byte-identical copies are genuine duplicates, not evidence
                # that either particular path is the historical catalog location.
                if len(hash_matches) == 1:
                    identity_matches = hash_matches
                    matched_by_hash = True

        if len(identity_matches) != 1:
            continue
        candidate = identity_matches[0]["path"]
        used.add(str(candidate))
        matches.append((row, candidate, matched_by_hash, historical_hash))

    if not matches:
        return {"available": True, "reconciled": 0, "hash_resolved": 0}

    reconciled = 0
    hash_resolved = 0
    with db.connect() as conn:
        for row, candidate, matched_by_hash, historical_hash in matches:
            try:
                stat = candidate.stat()
            except OSError:
                continue

            # Stand-alone movies use the file itself as the title catalog path.
            # Preserve the title row and its metadata instead of allowing the scan
            # to delete/recreate it under the renamed filename.
            if root_row["kind"] == "movie" and Path(row["folder_path"]) == Path(row["path"]):
                conflict = conn.execute(
                    "SELECT id FROM titles WHERE folder_path=? AND id!=?",
                    (str(candidate), row["title_id"]),
                ).fetchone()
                if conflict:
                    continue
                conn.execute(
                    "UPDATE titles SET folder_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (str(candidate), row["title_id"]),
                )
            elif root_row["kind"] == "tv":
                old_show = Path(row["folder_path"])
                new_show = _show_folder(root, candidate)
                if old_show != new_show:
                    parsed_show = parse_title(new_show.name)
                    title_matches = _same_text(parsed_show.title, row["title"]) or _same_text(
                        parsed_show.title, row["metadata_title"]
                    )
                    if title_matches and _same_year(
                        parsed_show.year, row["year"], row["metadata_year"]
                    ):
                        conflict = conn.execute(
                            "SELECT id FROM titles WHERE folder_path=? AND id!=?",
                            (str(new_show), row["title_id"]),
                        ).fetchone()
                        if not conflict:
                            conn.execute(
                                "UPDATE titles SET folder_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                (str(new_show), row["title_id"]),
                            )

            path_conflict = conn.execute(
                "SELECT id FROM files WHERE path=? AND id!=?",
                (str(candidate), row["file_id"]),
            ).fetchone()
            if path_conflict:
                continue
            conn.execute(
                """UPDATE files
                   SET path=?, filename=?, extension=?, size_bytes=?, modified_at=?,
                       media_info_error=CASE
                         WHEN media_info_error LIKE ? THEN NULL ELSE media_info_error END,
                       media_info_at=CASE
                         WHEN media_info_error LIKE ? THEN NULL ELSE media_info_at END
                   WHERE id=?""",
                (
                    str(candidate), candidate.name, candidate.suffix.lower(),
                    int(stat.st_size), float(stat.st_mtime),
                    f"{_MISSING_PATH_ERROR_PREFIX}%",
                    f"{_MISSING_PATH_ERROR_PREFIX}%",
                    row["file_id"],
                ),
            )
            if matched_by_hash and historical_hash:
                conn.execute(
                    """UPDATE media_file_hashes
                       SET sha256=?, size_bytes=?, modified_at=?, status='complete',
                           error='', hashed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                       WHERE file_id=?""",
                    (
                        historical_hash, int(stat.st_size), float(stat.st_mtime),
                        row["file_id"],
                    ),
                )
                hash_resolved += 1
            reconciled += 1

    return {
        "available": True,
        "reconciled": reconciled,
        "hash_resolved": hash_resolved,
    }

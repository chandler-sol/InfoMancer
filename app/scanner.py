from __future__ import annotations

import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


VIDEO_EXTENSIONS = {
    ".3gp", ".asf", ".avi", ".divx", ".flv", ".m2ts", ".m4v", ".mkv",
    ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".ogm", ".ogv", ".ts",
    ".vob", ".webm", ".wmv",
}


class SourceUnavailableError(ValueError):
    """The configured source could not be reached safely."""


def _lexical_absolute(path: Path) -> Path:
    """Normalize an absolute path without asking the filesystem to resolve it."""
    expanded = path.expanduser()
    if expanded.is_absolute():
        return Path(os.path.normpath(os.fspath(expanded)))
    return Path(os.path.abspath(os.fspath(expanded)))


def _resolve_scan_path(path: Path, *, directory: bool = False) -> tuple[Path, bool]:
    """Resolve a scan path while tolerating WinError 1272 on readable mappings.

    Windows can reject final-path resolution for a mapped NFS/SMB location even
    though normal directory enumeration and file access work. The source browser
    already has to tolerate that provider quirk. Scanning must do the same or a
    folder can preview successfully and then immediately degrade with zero files.

    The fallback is deliberately narrow: only WinError 1272 is accepted, and the
    lexical path must still be directly readable before it is used. Callers keep
    the existing symlink/junction and containment checks around this helper.
    """
    try:
        return path.resolve(strict=True), False
    except OSError as exc:
        if getattr(exc, "winerror", None) != 1272:
            raise
        lexical = _lexical_absolute(path)
        try:
            if directory:
                with os.scandir(lexical):
                    pass
            else:
                os.stat(lexical)
        except OSError:
            raise exc
        return lexical, True


def _readable_directory(path: Path) -> bool:
    try:
        with os.scandir(path):
            return True
    except OSError:
        return False


def _walk_files(root: Path, errors: list[str]):
    def on_error(error: OSError) -> None:
        errors.append(str(error))

    try:
        resolved_root, lexical_root = _resolve_scan_path(root, directory=True)
    except OSError as exc:
        errors.append(str(exc))
        return

    for directory, names, filenames in os.walk(
        root, topdown=True, onerror=on_error, followlinks=False,
    ):
        folder = Path(directory)
        safe_names: list[str] = []
        for name in names:
            candidate = folder / name
            if name == ".infomancer-trash":
                continue
            try:
                if candidate.is_symlink() or candidate.is_junction():
                    continue
            except OSError as exc:
                errors.append(str(exc))
                continue
            safe_names.append(name)
        names[:] = safe_names

        for filename in filenames:
            candidate = folder / filename
            try:
                # A file symlink can escape a configured source even when os.walk
                # itself does not follow linked directories. Catalog only physical
                # files whose resolved path remains under the configured root.
                if candidate.is_symlink() or candidate.is_junction():
                    continue
                if lexical_root:
                    resolved_candidate = _lexical_absolute(candidate)
                else:
                    resolved_candidate, _ = _resolve_scan_path(candidate)
                resolved_candidate.relative_to(resolved_root)
            except ValueError:
                errors.append(f"Skipped a file that resolves outside the configured source: {candidate}")
                continue
            except OSError as exc:
                errors.append(str(exc))
                continue
            yield candidate


def _catalog_fully_accounted(
    previous_count: int, file_count: int, preserved_count: int,
) -> bool:
    """Return true when a rescan proved every known catalog file is still visible."""
    return previous_count > 0 and preserved_count == 0 and file_count >= previous_count


def _read_errors_block_health(
    errors: list[str], *, previous_count: int, file_count: int, preserved_count: int,
) -> bool:
    """Keep Source Guard strict except for a proven Windows provider metadata quirk.

    WinError 1272 can be raised by Windows path metadata calls against otherwise
    readable mapped NFS/SMB storage. It is safe to treat those errors as warnings
    only after a rescan has independently accounted for every previously cataloged
    media file. Any other read error, or any missing catalog file, remains blocking.
    """
    if not errors:
        return False
    if not _catalog_fully_accounted(previous_count, file_count, preserved_count):
        return True
    return any("winerror 1272" not in error.lower() for error in errors)


EPISODE_RE = re.compile(
    r"(?i)(?:^|[. _\-])s(?P<season>\d{1,3})[. _\-]*e(?P<start>\d{1,3})"
    r"(?:[. _\-]*(?:e|-e?)(?P<end>\d{1,3}))?"
)
YEAR_RE = re.compile(r"(?:^|\s|\()(?P<year>(?:19|20)\d{2})(?:\)|\s|$)")
YEAR_RANGE_RE = re.compile(
    r"(?i)^(?P<title>.+?)\s*\(\s*(?P<start>(?:19|20)\d{2})\s*-\s*"
    r"(?P<end>(?:19|20)\d{2}|present)\s*\)\s*$"
)
TRAILING_YEAR_RE = re.compile(
    r"^(?P<title>.+?)\s*\(\s*(?P<year>(?:19|20)\d{2})\s*\)\s*$"
)
ID_TAG_RE = re.compile(r"\s*\{(?:tvdb|tmdb|imdb)-[^}]+\}\s*", re.I)
NOISE_RE = re.compile(
    r"(?ix)(?:^|[. _\-])(2160p|1080p|720p|480p|4k|uhd|hdr10?|dv|dolby[. _]?vision|"
    r"web[. _-]?(?:dl|rip)|bluray|b[rd]rip|hdtv|remux|x26[45]|h[. ]?26[45]|hevc|"
    r"av1|aac(?:2[.]0)?|ac3|eac3|dts(?:-hd)?|truehd|atmos|proper|repack)(?=$|[. _\-])"
)
MOVIE_BUCKET_RE = re.compile(
    r"^(?:[A-Z]|#|(?:#\s*)?0\s*[-–]\s*9|numbers?)$", re.I
)
MOVIE_RELEASE_YEAR_RE = re.compile(
    r"(?:^|[. _\-(\[])(?P<year>(?:19|20)\d{2})(?=$|[. _\-)\]])"
)


@dataclass(frozen=True)
class ParsedEpisode:
    season: int | None
    start: int | None
    end: int | None
    parsed_title: str


@dataclass(frozen=True)
class ParsedTitle:
    title: str
    year: int | None
    end_year: int | None
    continuing: bool | None


def clean_words(value: str) -> str:
    value = ID_TAG_RE.sub(" ", value)
    # Periods are common filename separators, but a period between digits can be
    # meaningful title punctuation (for example "Jackass 3.5"). Preserve only
    # digit-to-digit periods while normalizing the rest like ordinary separators.
    value = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", value).replace("_", " ")
    return re.sub(r"\s+", " ", value).strip(" -")


def parse_title(folder_name: str) -> ParsedTitle:
    cleaned = ID_TAG_RE.sub("", folder_name).strip()
    range_match = YEAR_RANGE_RE.match(cleaned)
    if range_match:
        end_label = range_match.group("end")
        continuing = end_label.lower() == "present"
        return ParsedTitle(
            clean_words(range_match.group("title")),
            int(range_match.group("start")),
            None if continuing else int(end_label),
            continuing,
        )

    trailing_match = TRAILING_YEAR_RE.match(cleaned)
    if trailing_match:
        return ParsedTitle(
            clean_words(trailing_match.group("title")),
            int(trailing_match.group("year")), None, None,
        )

    # Fall back for loose names such as "Movie Name 2020". Prefer the final
    # year so numeric titles like "1923" are not stolen when a later year exists.
    latest_reasonable_year = datetime.now(timezone.utc).year + 3
    matches = [
        item for item in YEAR_RE.finditer(cleaned)
        if 1888 <= int(item.group("year")) <= latest_reasonable_year
    ]
    match = matches[-1] if matches else None
    year = int(match.group("year")) if match else None
    if match:
        cleaned = (cleaned[: match.start()] + " " + cleaned[match.end() :]).strip()
    return ParsedTitle(clean_words(cleaned) or folder_name, year, None, None)


def title_and_year(folder_name: str) -> tuple[str, int | None]:
    parsed = parse_title(folder_name)
    return parsed.title, parsed.year


def movie_release_title(filename_stem: str) -> str:
    """Trim release-group noise after the movie's release year."""
    # Prefer the final plausible release year. This preserves numeric sequel
    # names such as "Blade Runner 2049" while still finding a later "(2017)".
    latest_reasonable_year = datetime.now(timezone.utc).year + 3
    matches = [
        item for item in MOVIE_RELEASE_YEAR_RE.finditer(filename_stem)
        if 1888 <= int(item.group("year")) <= latest_reasonable_year
    ]
    match = matches[-1] if matches else None
    if not match:
        return clean_words(NOISE_RE.sub(" ", filename_stem))
    title = clean_words(filename_stem[:match.start("year")].rstrip(" ._-[("))
    return f"{title} ({match.group('year')})"


def parse_episode(filename: str) -> ParsedEpisode:
    stem = Path(filename).stem
    match = EPISODE_RE.search(stem)
    if not match:
        return ParsedEpisode(None, None, None, clean_words(NOISE_RE.sub(" ", stem)))
    prefix = clean_words(stem[: match.start()])
    return ParsedEpisode(
        int(match.group("season")),
        int(match.group("start")),
        int(match.group("end")) if match.group("end") else int(match.group("start")),
        prefix,
    )


def _show_folder(root: Path, file: Path) -> Path:
    relative = file.relative_to(root)
    return root / relative.parts[0] if len(relative.parts) > 1 else root


def _is_movie_bucket(root: Path, folder: Path) -> bool:
    try:
        folder.relative_to(root)
    except ValueError:
        return False
    return bool(MOVIE_BUCKET_RE.fullmatch(folder.name))


def scan_root(
    conn: sqlite3.Connection,
    root_row: sqlite3.Row,
    progress: Callable[[int, int], None] | None = None,
    *, force_cleanup: bool = False,
) -> dict[str, int | str]:
    root = Path(root_row["path"])
    if not _readable_directory(root):
        raise SourceUnavailableError(f"Media path is not an accessible directory: {root}")

    scan_id = uuid.uuid4().hex
    file_count = 0
    title_ids: set[int] = set()
    read_errors: list[str] = []
    previous_count = int(conn.execute(
        """SELECT COUNT(*) FROM files f JOIN titles t ON t.id=f.title_id
           WHERE t.root_id=?""", (root_row["id"],),
    ).fetchone()[0])
    for file in _walk_files(root, read_errors):
        try:
            if not file.is_file() or file.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            stat = file.stat()
        except OSError as exc:
            read_errors.append(str(exc))
            continue

        folder = _show_folder(root, file) if root_row["kind"] == "tv" else file.parent
        # A movie file directly under a root is its own catalog item. Using the
        # root or an A-Z/# bucket as its key would merge stand-alone movies.
        standalone_movie = root_row["kind"] == "movie" and (
            folder == root or _is_movie_bucket(root, folder)
        )
        catalog_path = file if standalone_movie else folder
        source_name = movie_release_title(file.stem) if standalone_movie else folder.name
        parsed_folder = parse_title(source_name)
        name, year = parsed_folder.title, parsed_folder.year
        existing = conn.execute(
            "SELECT id FROM titles WHERE folder_path = ?", (str(catalog_path),)
        ).fetchone()
        if existing:
            title_id = existing["id"]
            conn.execute(
                """UPDATE titles SET title=?, year=?, end_year=?, continuing=?,
                   updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (name, year, parsed_folder.end_year, parsed_folder.continuing, title_id),
            )
        else:
            cur = conn.execute(
                """INSERT INTO titles(root_id, kind, title, year, end_year, continuing,
                   folder_path, discovered_at) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (root_row["id"], root_row["kind"], name, year, parsed_folder.end_year,
                 parsed_folder.continuing, str(catalog_path)),
            )
            title_id = cur.lastrowid

        parsed = parse_episode(file.name) if root_row["kind"] == "tv" else ParsedEpisode(None, None, None, name)
        conn.execute(
            """INSERT INTO files(title_id, path, filename, extension, size_bytes, modified_at,
                                  season, episode_start, episode_end, parsed_title,
                                  original_filename, seen_scan)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET title_id=excluded.title_id, filename=excluded.filename,
                 extension=excluded.extension, size_bytes=excluded.size_bytes,
                 modified_at=excluded.modified_at, season=excluded.season,
                 episode_start=excluded.episode_start, episode_end=excluded.episode_end,
                 parsed_title=excluded.parsed_title, seen_scan=excluded.seen_scan,
                 runtime_seconds=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.runtime_seconds END,
                 width=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.width END,
                 height=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.height END,
                 video_codec=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.video_codec END,
                 audio_codec=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.audio_codec END,
                 audio_channels=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.audio_channels END,
                 bitrate=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.bitrate END,
                 container=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.container END,
                 dynamic_range=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.dynamic_range END,
                 media_info_at=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.media_info_at END,
                 media_info_error=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.media_info_error END""",
            (title_id, str(file), file.name, file.suffix.lower(), stat.st_size, stat.st_mtime,
             parsed.season, parsed.start, parsed.end, parsed.parsed_title, file.name, scan_id),
        )
        file_count += 1
        title_ids.add(title_id)
        if progress and (file_count == 1 or file_count % 100 == 0):
            progress(file_count, len(title_ids))

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    suspicious_drop = previous_count > 0 and (
        file_count == 0 or (previous_count >= 10 and file_count * 4 < previous_count)
    )
    preserved_count = int(conn.execute(
        """SELECT COUNT(*) FROM files f JOIN titles t ON t.id=f.title_id
           WHERE t.root_id=? AND f.seen_scan!=?""",
        (root_row["id"], scan_id),
    ).fetchone()[0])
    read_errors_blocking = _read_errors_block_health(
        read_errors,
        previous_count=previous_count,
        file_count=file_count,
        preserved_count=preserved_count,
    )
    degraded = bool(read_errors_blocking or (suspicious_drop and not force_cleanup))
    if not degraded:
        conn.execute(
            "DELETE FROM files WHERE title_id IN (SELECT id FROM titles WHERE root_id = ?) AND seen_scan != ?",
            (root_row["id"], scan_id),
        )
        conn.execute(
            "DELETE FROM titles WHERE root_id = ? AND NOT EXISTS (SELECT 1 FROM files WHERE files.title_id=titles.id)",
            (root_row["id"],),
        )
    for title_id in title_ids:
        conn.execute(
            "UPDATE titles SET last_scanned_at=? WHERE id=?", (now, title_id)
        )
    if degraded:
        reason = (
            f"The source returned {len(read_errors)} read error(s)."
            if read_errors_blocking else
            f"Only {file_count:,} of the previous {previous_count:,} files were visible."
        )
        conn.execute(
            """UPDATE roots SET health_status='degraded',last_checked_at=?,last_seen_at=?,
               last_error=?,last_observed_file_count=?,guard_preserved_count=? WHERE id=?""",
            (now, now, reason, file_count, preserved_count, root_row["id"]),
        )
    else:
        conn.execute(
            """UPDATE roots SET last_scanned_at=?,health_status='healthy',last_checked_at=?,
               last_seen_at=?,last_error='',last_file_count=?,last_observed_file_count=?,
               guard_preserved_count=0 WHERE id=?""",
            (now, now, now, file_count, file_count, root_row["id"]),
        )
    if progress:
        progress(file_count, len(title_ids))
    return {
        "files": file_count, "titles": len(title_ids), "scan_id": scan_id,
        "source_status": "degraded" if degraded else "healthy",
        "preserved": preserved_count if degraded else 0,
        "read_errors": len(read_errors),
        "read_warnings": len(read_errors) if read_errors and not degraded else 0,
    }


def scan_title(
    conn: sqlite3.Connection,
    title_row: sqlite3.Row,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int | str]:
    folder = Path(title_row["folder_path"])
    if title_row["kind"] != "tv" or not _readable_directory(folder):
        raise SourceUnavailableError(f"Series path is not an accessible directory: {folder}")

    scan_id = uuid.uuid4().hex
    file_count = 0
    read_errors: list[str] = []
    previous_count = int(conn.execute(
        "SELECT COUNT(*) FROM files WHERE title_id=?", (title_row["id"],),
    ).fetchone()[0])
    for file in _walk_files(folder, read_errors):
        try:
            if not file.is_file() or file.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            stat = file.stat()
        except OSError as exc:
            read_errors.append(str(exc))
            continue
        parsed = parse_episode(file.name)
        conn.execute(
            """INSERT INTO files(title_id, path, filename, extension, size_bytes, modified_at,
                                  season, episode_start, episode_end, parsed_title,
                                  original_filename, seen_scan)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET filename=excluded.filename,
                 extension=excluded.extension, size_bytes=excluded.size_bytes,
                 modified_at=excluded.modified_at, season=excluded.season,
                 episode_start=excluded.episode_start, episode_end=excluded.episode_end,
                 parsed_title=excluded.parsed_title, seen_scan=excluded.seen_scan,
                 runtime_seconds=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.runtime_seconds END,
                 width=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.width END,
                 height=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.height END,
                 video_codec=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.video_codec END,
                 audio_codec=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.audio_codec END,
                 audio_channels=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.audio_channels END,
                 bitrate=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.bitrate END,
                 container=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.container END,
                 dynamic_range=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.dynamic_range END,
                 media_info_at=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.media_info_at END,
                 media_info_error=CASE WHEN files.size_bytes!=excluded.size_bytes OR files.modified_at!=excluded.modified_at THEN NULL ELSE files.media_info_error END""",
            (title_row["id"], str(file), file.name, file.suffix.lower(), stat.st_size,
             stat.st_mtime, parsed.season, parsed.start, parsed.end,
             parsed.parsed_title, file.name, scan_id),
        )
        file_count += 1
        if progress and (file_count == 1 or file_count % 100 == 0):
            progress(file_count, 1)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    suspicious_drop = previous_count > 0 and (
        file_count == 0 or (previous_count >= 10 and file_count * 4 < previous_count)
    )
    preserved_count = int(conn.execute(
        "SELECT COUNT(*) FROM files WHERE title_id=? AND seen_scan!=?",
        (title_row["id"], scan_id),
    ).fetchone()[0])
    read_errors_blocking = _read_errors_block_health(
        read_errors,
        previous_count=previous_count,
        file_count=file_count,
        preserved_count=preserved_count,
    )
    degraded = bool(read_errors_blocking or suspicious_drop)
    if not degraded:
        conn.execute(
            "DELETE FROM files WHERE title_id=? AND seen_scan != ?",
            (title_row["id"], scan_id),
        )
    else:
        reason = (
            f"The series scan returned {len(read_errors)} read error(s)."
            if read_errors_blocking else
            f"Only {file_count:,} of the previous {previous_count:,} series files were visible."
        )
        conn.execute(
            """UPDATE roots SET health_status='degraded',last_checked_at=?,last_seen_at=?,
               last_error=?,guard_preserved_count=guard_preserved_count+? WHERE id=?""",
            (now, now, reason, preserved_count, title_row["root_id"]),
        )
    conn.execute(
        "UPDATE titles SET last_scanned_at=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (now, title_row["id"]),
    )
    if progress:
        progress(file_count, 1)
    return {
        "files": file_count, "titles": 1, "scan_id": scan_id,
        "source_status": "degraded" if degraded else "healthy",
        "preserved": preserved_count if degraded else 0,
        "read_errors": len(read_errors),
        "read_warnings": len(read_errors) if read_errors and not degraded else 0,
    }

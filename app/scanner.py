from __future__ import annotations

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
    value = value.replace(".", " ").replace("_", " ")
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
) -> dict[str, int | str]:
    root = Path(root_row["path"])
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Media path is not an accessible directory: {root}")

    scan_id = uuid.uuid4().hex
    file_count = 0
    title_ids: set[int] = set()
    for file in root.rglob("*"):
        try:
            if ".infomancer-trash" in file.parts:
                continue
            if not file.is_file() or file.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            stat = file.stat()
        except OSError:
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

    conn.execute(
        "DELETE FROM files WHERE title_id IN (SELECT id FROM titles WHERE root_id = ?) AND seen_scan != ?",
        (root_row["id"], scan_id),
    )
    conn.execute(
        "DELETE FROM titles WHERE root_id = ? AND NOT EXISTS (SELECT 1 FROM files WHERE files.title_id=titles.id)",
        (root_row["id"],),
    )
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for title_id in title_ids:
        conn.execute(
            "UPDATE titles SET last_scanned_at=? WHERE id=?", (now, title_id)
        )
    conn.execute("UPDATE roots SET last_scanned_at = ? WHERE id = ?", (now, root_row["id"]))
    if progress:
        progress(file_count, len(title_ids))
    return {"files": file_count, "titles": len(title_ids), "scan_id": scan_id}


def scan_title(
    conn: sqlite3.Connection,
    title_row: sqlite3.Row,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int | str]:
    folder = Path(title_row["folder_path"])
    if title_row["kind"] != "tv" or not folder.exists() or not folder.is_dir():
        raise ValueError(f"Series path is not an accessible directory: {folder}")

    scan_id = uuid.uuid4().hex
    file_count = 0
    for file in folder.rglob("*"):
        try:
            if ".infomancer-trash" in file.parts:
                continue
            if not file.is_file() or file.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            stat = file.stat()
        except OSError:
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

    conn.execute(
        "DELETE FROM files WHERE title_id=? AND seen_scan != ?",
        (title_row["id"], scan_id),
    )
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE titles SET last_scanned_at=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (now, title_row["id"]),
    )
    if progress:
        progress(file_count, 1)
    return {"files": file_count, "titles": 1, "scan_id": scan_id}

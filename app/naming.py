from __future__ import annotations

import re
from pathlib import Path


INVALID_WINDOWS = re.compile(r'[<>:"/\\|?*]')
ID_TAG_RE = re.compile(r"\s*\{tvdb-\d+\}\s*", re.I)


def safe_component(value: str) -> str:
    value = INVALID_WINDOWS.sub("", value).strip().rstrip(".")
    return re.sub(r"\s+", " ", value)


def plex_show_folder(
    title: str,
    year: int | None,
    tvdb_id: int,
    end_year: int | None = None,
    continuing: bool | None = None,
) -> str:
    base = safe_component(ID_TAG_RE.sub("", title))
    if year and continuing is True:
        date_label = f" ({year} - Present)"
    elif year and end_year:
        date_label = f" ({year} - {end_year})"
    else:
        date_label = f" ({year})" if year else ""
    return f"{base}{date_label} {{tvdb-{tvdb_id}}}"


def plex_episode_filename(
    show: str, year: int | None, season: int, episode: int, episode_name: str,
    extension: str, episode_end: int | None = None,
) -> str:
    label = safe_component(show)
    episode_title = safe_component(episode_name)
    suffix = f" - {episode_title}" if episode_title else ""
    code = f"S{season:02d}E{episode:02d}"
    if episode_end and episode_end > episode:
        code += f"-E{episode_end:02d}"
    return f"{label} - {code}{suffix}{extension.lower()}"


def plex_movie_filename(
    title: str, year: int | None, extension: str,
    tmdb_id: str = "", imdb_id: str = "",
) -> str:
    label = safe_component(title) + (f" ({year})" if year else "")
    provider = f" {{tmdb-{tmdb_id}}}" if tmdb_id else (f" {{imdb-{imdb_id}}}" if imdb_id else "")
    return f"{label}{provider}{extension.lower()}"


def contained_destination(source: Path, new_name: str) -> Path:
    destination = source.with_name(new_name)
    if destination.parent.resolve() != source.parent.resolve():
        raise ValueError("Rename destination escaped its source directory")
    return destination

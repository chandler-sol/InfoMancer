from __future__ import annotations

import os
from collections import deque
from pathlib import Path

from .scanner import EPISODE_RE, MOVIE_BUCKET_RE, VIDEO_EXTENSIONS


IGNORED_DIRECTORIES = {"lost+found", "$recycle.bin", "system volume information"}


class SourceBrowserError(ValueError):
    pass


def _resolved(path: Path | str) -> Path:
    # Browser input is rejected by validate_browse_path unless the resolved
    # result remains inside a configured media-browse root.
    try:
        return Path(path).expanduser().resolve(strict=False)
    except OSError as exc:
        raise SourceBrowserError("That folder is not accessible to InfoMancer") from exc


def _absolute_without_io(path: Path | str) -> Path:
    expanded = Path(path).expanduser()
    return Path(os.path.abspath(os.fspath(expanded)))


def _inside(path: Path, roots: tuple[Path, ...]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _root_is_accessible(root: Path) -> bool:
    try:
        with os.scandir(root):
            return True
    except OSError:
        return False


def configured_roots(values: tuple[Path, ...]) -> tuple[Path, ...]:
    """Return configured locations without resolving or probing them.

    Network-backed Windows drive letters must remain visible in the chooser
    even when the share is temporarily unavailable. Resolution is deferred
    until the user actually browses an accessible location.
    """
    roots: list[Path] = []
    seen: set[str] = set()
    for value in values:
        try:
            root = _absolute_without_io(value)
        except (OSError, RuntimeError):
            root = Path(value)
        key = os.path.normcase(os.fspath(root)).casefold()
        if key not in seen:
            seen.add(key)
            roots.append(root)
    return tuple(roots)


def allowed_roots(values: tuple[Path, ...]) -> tuple[Path, ...]:
    roots: list[Path] = []
    for root in configured_roots(values):
        if not _root_is_accessible(root):
            continue
        try:
            resolved = _resolved(root)
        except SourceBrowserError:
            continue
        if resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def validate_browse_path(path: Path | str, roots: tuple[Path, ...]) -> Path:
    resolved = _resolved(path)
    if not _inside(resolved, roots):
        raise SourceBrowserError("That folder is outside the allowed media locations")
    if not resolved.is_dir():
        raise SourceBrowserError("That folder is not accessible to InfoMancer")
    return resolved


def _visible_directory(entry: os.DirEntry[str]) -> bool:
    name = entry.name
    if name.startswith(".") or name.casefold() in IGNORED_DIRECTORIES:
        return False
    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return False


def list_folders(path: str, configured_root_values: tuple[Path, ...]) -> dict:
    if not path:
        locations = []
        for root in configured_roots(configured_root_values):
            locations.append({
                "name": root.name or str(root), "path": str(root),
                "accessible": _root_is_accessible(root),
            })
        return {"locations": locations, "current": "", "parent": None, "folders": []}

    roots = allowed_roots(configured_root_values)
    current = validate_browse_path(path, roots)
    folders = []
    try:
        entries = sorted(os.scandir(current), key=lambda item: item.name.casefold())
    except OSError as exc:
        raise SourceBrowserError(f"InfoMancer cannot read that folder: {exc}") from exc
    for entry in entries:
        if not _visible_directory(entry):
            continue
        child = _resolved(entry.path)
        if not _inside(child, roots):
            continue
        folders.append({"name": entry.name, "path": str(child)})

    parent = current.parent
    parent_value = str(parent) if parent != current and _inside(parent, roots) else None
    matched_root = next((root for root in roots if _inside(current, (root,))), current)
    relative = current.relative_to(matched_root)
    crumbs = [{"name": matched_root.name or str(matched_root), "path": str(matched_root)}]
    cursor = matched_root
    for part in relative.parts:
        cursor /= part
        crumbs.append({"name": part, "path": str(cursor)})
    return {
        "locations": [], "current": str(current), "name": current.name or str(current),
        "parent": parent_value, "folders": folders[:500], "breadcrumbs": crumbs,
        "truncated": len(folders) > 500,
    }


def preview_folder(
    path: str,
    configured_root_values: tuple[Path, ...],
    max_directories: int = 2500,
    max_video_files: int = 100000,
) -> dict:
    roots = allowed_roots(configured_root_values)
    root = validate_browse_path(path, roots)
    queue: deque[tuple[Path, int]] = deque([(root, 0)])
    directories = 0
    video_files = 0
    episode_files = 0
    season_folders = 0
    bucket_folders = 0
    movie_titles: set[str] = set()
    show_titles: set[str] = set()
    truncated = False

    while queue:
        folder, depth = queue.popleft()
        directories += 1
        if directories > max_directories:
            truncated = True
            break
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if not _visible_directory(entry):
                        continue
                    child = _resolved(entry.path)
                    if not _inside(child, roots):
                        continue
                    if entry.name.casefold().startswith("season "):
                        season_folders += 1
                    if depth == 0 and MOVIE_BUCKET_RE.fullmatch(entry.name):
                        bucket_folders += 1
                    queue.append((child, depth + 1))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            file = Path(entry.path)
            if file.suffix.casefold() not in VIDEO_EXTENSIONS:
                continue
            video_files += 1
            relative = file.relative_to(root)
            if len(relative.parts) > 1:
                first = relative.parts[0]
                show_titles.add(root.name if first.casefold().startswith("season ") else first)
            else:
                show_titles.add(root.name)
            if EPISODE_RE.search(file.stem):
                episode_files += 1

            parent = file.parent
            if parent == root or (parent.parent == root and MOVIE_BUCKET_RE.fullmatch(parent.name)):
                movie_titles.add(str(file))
            else:
                movie_titles.add(str(parent))
            if video_files >= max_video_files:
                truncated = True
                queue.clear()
                break

    tv_signal = episode_files > 0 or season_folders > 0
    movie_signal = bucket_folders > 0 or (video_files > 0 and not tv_signal)
    mixed = tv_signal and bucket_folders > 0
    if mixed:
        recommendation = "mixed"
    elif tv_signal:
        recommendation = "tv"
    elif movie_signal:
        recommendation = "movie"
    else:
        recommendation = "unknown"

    warning = ""
    if mixed:
        warning = "This folder appears to mix movie buckets and TV episode folders. Choose a more specific folder or manually select a type."
    elif not video_files:
        warning = "No supported video files were found in the folders sampled."
    elif truncated:
        warning = "This is a large location, so the preview is an estimate from a bounded sample. The full scan will continue through everything."

    return {
        "path": str(root), "name": root.name or str(root),
        "recommended_kind": recommendation, "mixed": mixed,
        "movie_count": len(movie_titles), "show_count": len(show_titles),
        "episode_count": episode_files, "video_count": video_files,
        "bucket_count": bucket_folders, "season_folder_count": season_folders,
        "directories_sampled": min(directories, max_directories),
        "truncated": truncated, "warning": warning,
    }

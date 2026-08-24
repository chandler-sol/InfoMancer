from __future__ import annotations

import os
import stat
import time
from collections import deque
from pathlib import Path
from threading import RLock

from .scanner import EPISODE_RE, MOVIE_BUCKET_RE, VIDEO_EXTENSIONS


IGNORED_DIRECTORIES = {"lost+found", "$recycle.bin", "system volume information"}
ALLOWED_ROOTS_CACHE_TTL_SECONDS = 15.0
_allowed_roots_cache: dict[tuple[str, ...], tuple[float, tuple[Path, ...]]] = {}
_allowed_roots_cache_lock = RLock()


class SourceBrowserError(ValueError):
    pass


def _windows_browse_path(path: Path | str) -> Path:
    """Lexically normalize a Windows browse path without network resolution."""
    expanded = Path(path).expanduser()
    return Path(os.path.abspath(os.path.normpath(os.fspath(expanded))))


def _resolved(path: Path | str) -> Path:
    """Normalize a browse path without forcing mapped Windows drives to resolve.

    pathlib.Path.resolve() can perform network-provider I/O on Windows. A mapped
    drive may be perfectly browsable with scandir while resolve() fails with
    WinError 1272 because Windows refuses the provider's unauthenticated UNC
    resolution. For Windows we therefore use lexical absolute normalization and
    explicit reparse-point checks. POSIX keeps realpath-style resolution so
    symlink escapes remain rejected there.
    """
    if os.name == "nt":
        return _windows_browse_path(path)
    expanded = Path(path).expanduser()
    try:
        return expanded.resolve(strict=False)
    except OSError as exc:
        raise SourceBrowserError(f"InfoMancer cannot access that folder: {exc}") from exc


def _root_is_accessible(path: Path) -> bool:
    """Return whether a configured browse root can actually be opened now."""
    try:
        with os.scandir(path):
            return True
    except OSError:
        return False


def _root_cache_key(values: tuple[Path, ...]) -> tuple[str, ...]:
    """Build an OS-appropriate lexical key without touching the filesystem."""
    return tuple(
        os.path.normcase(os.path.abspath(os.fspath(value)))
        for value in values
    )


def _configured_roots(values: tuple[Path, ...]) -> tuple[Path, ...]:
    """Return configured roots without resolving or probing their filesystems."""
    roots: list[Path] = []
    seen: set[str] = set()
    for value in values:
        expanded = Path(value).expanduser()
        root = Path(os.path.abspath(os.fspath(expanded)))
        key = os.path.normcase(os.fspath(root))
        if key in seen:
            continue
        seen.add(key)
        roots.append(root)
    return tuple(roots)


def _clear_allowed_roots_cache() -> None:
    """Clear the short-lived browse-root cache, primarily for tests and reconfiguration."""
    with _allowed_roots_cache_lock:
        _allowed_roots_cache.clear()


def _inside(path: Path, roots: tuple[Path, ...]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _windows_has_reparse_component(path: Path, roots: tuple[Path, ...]) -> bool:
    """Detect symlinks/junctions below an allowed Windows root without resolving it.

    The configured drive root itself is deliberately not stat'ed. That is the
    important distinction for mapped SMB/NFS drives: opening X:\\ with scandir can
    succeed even when Windows' path-resolution provider rejects resolving X:\\ to
    its remote target. Components below the root are checked with lstat so a
    crafted symlink or junction cannot escape the configured browse boundary.
    """
    matched_root: Path | None = None
    relative: Path | None = None
    for root in roots:
        try:
            relative = path.relative_to(root)
            matched_root = root
            break
        except ValueError:
            continue
    if matched_root is None or relative is None:
        return True

    cursor = matched_root
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    for part in relative.parts:
        cursor /= part
        try:
            info = os.lstat(cursor)
        except OSError as exc:
            raise SourceBrowserError(f"InfoMancer cannot access that folder: {exc}") from exc
        if stat.S_ISLNK(info.st_mode) or (getattr(info, "st_file_attributes", 0) & reparse_flag):
            return True
    return False


def allowed_roots(values: tuple[Path, ...]) -> tuple[Path, ...]:
    cache_key = _root_cache_key(values)
    now = time.monotonic()
    with _allowed_roots_cache_lock:
        cached = _allowed_roots_cache.get(cache_key)
        if cached and cached[0] >= now:
            return cached[1]

    roots: list[Path] = []
    seen: set[str] = set()
    for value in values:
        try:
            root = _resolved(value)
        except SourceBrowserError:
            continue
        if not _root_is_accessible(root):
            continue
        # normcase performs case folding on Windows while leaving POSIX paths
        # case-sensitive. Do not add casefold(), because /media/Movies and
        # /media/movies can legitimately be different Linux directories.
        key = os.path.normcase(os.path.abspath(os.fspath(root)))
        if key in seen:
            continue
        seen.add(key)
        roots.append(root)

    result = tuple(roots)
    with _allowed_roots_cache_lock:
        _allowed_roots_cache[cache_key] = (
            time.monotonic() + ALLOWED_ROOTS_CACHE_TTL_SECONDS,
            result,
        )
        if len(_allowed_roots_cache) > 64:
            expired = [key for key, value in _allowed_roots_cache.items() if value[0] < now]
            for key in expired:
                _allowed_roots_cache.pop(key, None)
            while len(_allowed_roots_cache) > 64:
                _allowed_roots_cache.pop(next(iter(_allowed_roots_cache)))
    return result


def validate_browse_path(path: Path | str, roots: tuple[Path, ...]) -> Path:
    resolved = _resolved(path)
    if not _inside(resolved, roots):
        raise SourceBrowserError("That folder is outside the allowed media locations")
    if os.name == "nt" and _windows_has_reparse_component(resolved, roots):
        raise SourceBrowserError("That folder uses a Windows link or junction outside the safe browse path")
    if not _root_is_accessible(resolved):
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


def list_folders(path: str, configured_roots: tuple[Path, ...]) -> dict:
    if not path:
        locations = []
        for root in _configured_roots(configured_roots):
            locations.append({
                "name": root.name or str(root), "path": str(root),
                "accessible": _root_is_accessible(root),
            })
        return {"locations": locations, "current": "", "parent": None, "folders": []}

    roots = allowed_roots(configured_roots)
    current = validate_browse_path(path, roots)
    folders = []
    try:
        entries = sorted(os.scandir(current), key=lambda item: item.name.casefold())
    except OSError as exc:
        raise SourceBrowserError(f"InfoMancer cannot read that folder: {exc}") from exc
    for entry in entries:
        if not _visible_directory(entry):
            continue
        try:
            child = _resolved(entry.path)
        except SourceBrowserError:
            continue
        if not _inside(child, roots):
            continue
        if os.name == "nt":
            try:
                if _windows_has_reparse_component(child, roots):
                    continue
            except SourceBrowserError:
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
    configured_roots: tuple[Path, ...],
    max_directories: int = 2500,
    max_video_files: int = 100000,
) -> dict:
    roots = allowed_roots(configured_roots)
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
                    try:
                        child = _resolved(entry.path)
                    except SourceBrowserError:
                        continue
                    if not _inside(child, roots):
                        continue
                    if os.name == "nt":
                        try:
                            if _windows_has_reparse_component(child, roots):
                                continue
                        except SourceBrowserError:
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

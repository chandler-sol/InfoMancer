from __future__ import annotations

from dataclasses import dataclass, replace
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat as stat_module
import struct
from typing import Any, Mapping, Sequence
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image, UnidentifiedImageError

from ...path_mapping import ExternalPathMapper, PathMappingError, parse_absolute_path
from ..external import (
    ExternalAnalysisError,
    ExternalCapability,
    ExternalMediaRef,
    ExternalPreviewUnavailable,
    ExternalSourceFailure,
    ExternalSourceStatus,
    PreviewFrameRef,
)
from ..models import AnalyzerContext
from ..visual_budget import (
    VisualBudgetExceeded,
    account_source_bytes,
    current_visual_budget,
    deny_visual_budget,
    source_read_plan,
)


_BIF_MAGIC = b"\x89BIF\r\n\x1a\n"
_BIF_HEADER_SIZE = 64
_BIF_INDEX_ENTRY_SIZE = 8
_BIF_END_TIMESTAMP = 0xFFFFFFFF
_BIF_VERSION = 0
_MAX_BIF_IMAGES = 100_000
_MAX_TIMESTAMP_MS = (1 << 63) - 1
_MAX_PLEX_JSON_BYTES = 8 * 1024 * 1024
_MAX_PLEX_BIF_BYTES = 128 * 1024 * 1024
_MAX_PLEX_JPEG_BYTES = 8 * 1024 * 1024
_MAX_PLEX_JPEG_DIMENSION = 16_384
_MAX_PLEX_JPEG_PIXELS = 64_000_000
_MAX_EPISODE_CANDIDATES = 4096
_PLEX_PAGE_SIZE = 256
_PLEX_MEDIA_HASH = re.compile(r"^[0-9a-fA-F]{40}$")


class PlexBifError(ExternalAnalysisError):
    """Raised when a Plex source operation is malformed, unsafe, or unavailable."""


class PlexSourceFailure(ExternalSourceFailure, PlexBifError):
    """Plex could not be queried or returned untrustworthy source metadata."""


class PlexPreviewUnavailable(ExternalPreviewUnavailable, PlexBifError):
    """Raised when optional Plex preview evidence is unavailable for an item."""


class _PlexHttpError(PlexSourceFailure):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = int(status_code)


class _PlexResponseTooLarge(PlexSourceFailure):
    """Raised when a bounded Plex response exceeds its caller's safe limit."""


def _validate_plex_jpeg(payload: bytes) -> bytes:
    """Decode one preview image with explicit dimension and pixel ceilings."""
    if (
        len(payload) < 4
        or not payload.startswith(b"\xff\xd8")
        or not payload.endswith(b"\xff\xd9")
    ):
        raise PlexPreviewUnavailable("Plex returned an invalid JPEG preview image.")
    try:
        with Image.open(BytesIO(payload)) as image:
            if image.format != "JPEG":
                raise PlexPreviewUnavailable(
                    "Plex preview image did not decode as JPEG."
                )
            width, height = image.size
            if (
                width <= 0
                or height <= 0
                or width > _MAX_PLEX_JPEG_DIMENSION
                or height > _MAX_PLEX_JPEG_DIMENSION
                or width * height > _MAX_PLEX_JPEG_PIXELS
            ):
                raise PlexPreviewUnavailable(
                    "Plex preview image dimensions exceed safe decode limits."
                )
            image.load()
    except PlexPreviewUnavailable:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
        raise PlexPreviewUnavailable(
            "Plex preview image could not be decoded safely."
        ) from exc
    return payload


@dataclass(frozen=True)
class PlexBifFrameRange:
    timestamp_ms: int
    offset: int
    length: int


@dataclass(frozen=True)
class PlexBifIndex:
    version: int
    image_count: int
    timestamp_multiplier_ms: int
    frames: tuple[PlexBifFrameRange, ...]
    file_size: int
    source_mtime_ns: int
    index_digest: str
    frame_digests: tuple[str, ...] = ()


def _header_fields(header: bytes) -> tuple[int, int, int]:
    if len(header) != _BIF_HEADER_SIZE:
        raise PlexBifError("Plex BIF header is truncated.")
    if header[:8] != _BIF_MAGIC:
        raise PlexBifError("Plex BIF magic number is invalid.")
    version, image_count, raw_multiplier = struct.unpack_from("<III", header, 8)
    if version != _BIF_VERSION:
        raise PlexBifError(
            f"Plex BIF version {version} is unsupported; only version 0 is accepted."
        )
    if image_count > _MAX_BIF_IMAGES:
        raise PlexBifError("Plex BIF image count exceeds the safe parser limit.")
    if any(header[20:_BIF_HEADER_SIZE]):
        raise PlexBifError("Plex BIF reserved header bytes are not zero.")
    multiplier = raw_multiplier or 1000
    return version, image_count, multiplier


def parse_bif_index(
    payload: bytes,
    *,
    file_size: int,
    source_mtime_ns: int = 0,
) -> PlexBifIndex:
    """Parse only the BIF header/index; JPEG payload bytes are not required."""
    if len(payload) < _BIF_HEADER_SIZE:
        raise PlexBifError("Plex BIF header is truncated.")
    version, image_count, multiplier = _header_fields(payload[:_BIF_HEADER_SIZE])
    index_size = (image_count + 1) * _BIF_INDEX_ENTRY_SIZE
    required_size = _BIF_HEADER_SIZE + index_size
    if len(payload) != required_size:
        raise PlexBifError("Plex BIF index payload has an unexpected size.")

    total_size = int(file_size)
    if total_size < required_size:
        raise PlexBifError("Plex BIF file is smaller than its declared index.")
    if total_size > 0xFFFFFFFF:
        raise PlexBifError("Plex BIF file exceeds the version 0 offset range.")

    entries = tuple(
        struct.unpack_from(
            "<II",
            payload,
            _BIF_HEADER_SIZE + index * _BIF_INDEX_ENTRY_SIZE,
        )
        for index in range(image_count + 1)
    )
    end_timestamp, end_offset = entries[-1]
    if end_timestamp != _BIF_END_TIMESTAMP:
        raise PlexBifError("Plex BIF index is missing its end-of-data marker.")
    if end_offset != total_size:
        raise PlexBifError(
            "Plex BIF end-of-data offset does not match the file size."
        )

    frames: list[PlexBifFrameRange] = []
    previous_timestamp: int | None = None
    for index in range(image_count):
        timestamp, offset = entries[index]
        next_offset = entries[index + 1][1]
        if offset < required_size:
            raise PlexBifError("Plex BIF frame data overlaps the header or index.")
        if next_offset <= offset:
            raise PlexBifError("Plex BIF frame offsets are not strictly increasing.")
        if next_offset > total_size:
            raise PlexBifError("Plex BIF frame range extends beyond the file.")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise PlexBifError("Plex BIF timestamps are not strictly increasing.")
        timestamp_ms = int(timestamp) * int(multiplier)
        if timestamp_ms > _MAX_TIMESTAMP_MS:
            raise PlexBifError("Plex BIF timestamp exceeds the safe range.")
        frames.append(
            PlexBifFrameRange(
                timestamp_ms=timestamp_ms,
                offset=offset,
                length=next_offset - offset,
            )
        )
        previous_timestamp = timestamp

    digest = hashlib.sha256(payload).hexdigest()
    return PlexBifIndex(
        version=version,
        image_count=image_count,
        timestamp_multiplier_ms=multiplier,
        frames=tuple(frames),
        file_size=total_size,
        source_mtime_ns=max(0, int(source_mtime_ns)),
        index_digest=digest,
    )


def _bif_stat_mtime_ns(value: os.stat_result) -> int:
    return int(
        getattr(
            value,
            "st_mtime_ns",
            int(float(value.st_mtime) * 1_000_000_000),
        )
    )


def _bif_stat_ctime_ns(value: os.stat_result) -> int:
    return int(
        getattr(
            value,
            "st_ctime_ns",
            int(float(value.st_ctime) * 1_000_000_000),
        )
    )


def _bif_index_budget_cache_key(
    path: Path,
    stat_result: os.stat_result,
) -> str:
    identity = {
        "path": str(path.absolute()),
        "size": int(stat_result.st_size),
        "mtime_ns": _bif_stat_mtime_ns(stat_result),
        "ctime_ns": _bif_stat_ctime_ns(stat_result),
        "device_id": int(getattr(stat_result, "st_dev", 0) or 0),
        "inode_id": int(getattr(stat_result, "st_ino", 0) or 0),
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "plex-bif-index:" + hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def _read_budgeted_bif_index_payload(
    handle,
    path: Path,
    stat_result: os.stat_result,
) -> bytes:
    """Read a BIF header/index once per visual attempt and charge actual reads."""
    budget = current_visual_budget()
    cache_key = _bif_index_budget_cache_key(path, stat_result)
    if budget is not None:
        cached = budget.cached_source_asset(cache_key)
        if cached is not None:
            return bytes(cached)

    account_source_bytes(_BIF_HEADER_SIZE)
    header = handle.read(_BIF_HEADER_SIZE)
    _, image_count, _ = _header_fields(header)
    index_size = (image_count + 1) * _BIF_INDEX_ENTRY_SIZE
    account_source_bytes(index_size)
    index_bytes = handle.read(index_size)
    if len(index_bytes) != index_size:
        raise PlexBifError("Plex BIF index is truncated.")

    payload = header + index_bytes
    if budget is not None:
        budget.cache_source_asset(cache_key, payload)
    return payload


def read_bif_index(path: str | Path) -> PlexBifIndex:
    """Read only the bounded BIF header/index from disk, never the JPEG section."""
    bif_path = Path(path)
    try:
        with bif_path.open("rb") as handle:
            stat = os.fstat(handle.fileno())
            if not stat_module.S_ISREG(stat.st_mode):
                raise PlexBifError("Plex BIF path is not a regular file.")
            if stat.st_size < _BIF_HEADER_SIZE + _BIF_INDEX_ENTRY_SIZE:
                raise PlexBifError(
                    "Plex BIF file is too small to contain an index."
                )
            if stat.st_size > 0xFFFFFFFF:
                raise PlexBifError(
                    "Plex BIF file exceeds the version 0 offset range."
                )
            index_payload = _read_budgeted_bif_index_payload(
                handle,
                bif_path,
                stat,
            )
            after = os.fstat(handle.fileno())
    except PlexBifError:
        raise
    except OSError as exc:
        raise PlexBifError(f"Plex BIF index could not be read: {exc}") from exc

    if (
        int(stat.st_size) != int(after.st_size)
        or _bif_stat_mtime_ns(stat) != _bif_stat_mtime_ns(after)
        or _bif_stat_ctime_ns(stat) != _bif_stat_ctime_ns(after)
    ):
        raise PlexBifError("Plex BIF changed while its index was being read.")
    return parse_bif_index(
        index_payload,
        file_size=stat.st_size,
        source_mtime_ns=_bif_stat_mtime_ns(stat),
    )


def normalize_plex_metadata_root(value: str | Path) -> Path:
    raw = str(value or "")
    if not raw:
        raise PlexBifError("Plex metadata root is required.")
    if any(ord(character) < 32 or ord(character) == 127 for character in raw):
        raise PlexBifError("Plex metadata root contains control characters.")
    root = Path(raw)
    if not root.is_absolute():
        raise PlexBifError("Plex metadata root must be an absolute local path.")
    return root


def plex_metadata_root_candidates() -> tuple[Path, ...]:
    """Return bounded common Plex data-root candidates without scanning the filesystem."""
    candidates: list[Path] = []

    support_dir = str(
        os.environ.get("PLEX_MEDIA_SERVER_APPLICATION_SUPPORT_DIR", "")
    ).strip()
    if support_dir:
        support = Path(support_dir).expanduser()
        candidates.append(support)
        if support.name.casefold() != "plex media server":
            candidates.append(support / "Plex Media Server")

    local_app_data = str(os.environ.get("LOCALAPPDATA", "")).strip()
    if local_app_data:
        candidates.append(Path(local_app_data).expanduser() / "Plex Media Server")

    home = Path.home()
    candidates.extend(
        (
            Path(
                "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server"
            ),
            Path("/config/Library/Application Support/Plex Media Server"),
            home / "Library" / "Application Support" / "Plex Media Server",
        )
    )

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        text = str(candidate)
        key = text.casefold() if os.name == "nt" else text
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return tuple(unique)


def detect_plex_metadata_root() -> Path | None:
    """Detect a common readable Plex data root without recursive filesystem search."""
    for candidate in plex_metadata_root_candidates():
        try:
            root = normalize_plex_metadata_root(candidate)
            database = plex_library_database_path(root)
        except PlexBifError:
            continue
        if database.is_file() and (root / "Media" / "localhost").is_dir():
            return root
    return None


def effective_plex_metadata_root(
    configured_root: str | Path = "",
) -> Path | None:
    raw = str(configured_root or "").strip()
    if raw:
        return normalize_plex_metadata_root(raw)
    return detect_plex_metadata_root()


def plex_bif_path_for_bundle(
    metadata_root: str | Path,
    bundle_relative_path: str,
) -> Path:
    """Build a contained index-sd.bif path beneath Plex Media/localhost."""
    root = normalize_plex_metadata_root(metadata_root)
    raw_relative = str(bundle_relative_path or "").replace("\\", "/")
    relative = PurePosixPath(raw_relative)
    windows_absolute = (
        len(raw_relative) >= 2
        and raw_relative[0].isalpha()
        and raw_relative[1] == ":"
    )
    if (
        not raw_relative
        or raw_relative.startswith("/")
        or windows_absolute
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise PlexBifError("Plex media bundle path is invalid.")
    if not relative.name.endswith(".bundle"):
        raise PlexBifError("Plex media bundle path must end in .bundle.")
    return (
        root
        / "Media"
        / "localhost"
        / Path(*relative.parts)
        / "Contents"
        / "Indexes"
        / "index-sd.bif"
    )


def plex_library_database_path(metadata_root: str | Path) -> Path:
    root = normalize_plex_metadata_root(metadata_root)
    return (
        root
        / "Plug-in Support"
        / "Databases"
        / "com.plexapp.plugins.library.db"
    )


def plex_bif_path_for_media_hash(
    metadata_root: str | Path,
    media_hash: str,
) -> Path:
    normalized_hash = str(media_hash or "").strip()
    if not _PLEX_MEDIA_HASH.fullmatch(normalized_hash):
        raise PlexBifError("Plex media-part hash is invalid.")
    normalized_hash = normalized_hash.casefold()
    return plex_bif_path_for_bundle(
        metadata_root,
        f"{normalized_hash[0]}/{normalized_hash[1:]}.bundle",
    )


def resolve_local_bif_path(
    metadata_root: str | Path,
    *,
    part_id: str,
    expected_external_path: str,
) -> Path | None:
    normalized_part = _plex_numeric_id(part_id, "Plex part id")
    root = normalize_plex_metadata_root(metadata_root)
    database_path = plex_library_database_path(root)
    if not database_path.is_file():
        return None
    try:
        connection = sqlite3.connect(
            database_path.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=1.0,
        )
    except (OSError, sqlite3.Error) as exc:
        raise PlexBifError(
            f"Plex library database could not be opened read-only: {exc}"
        ) from exc
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT hash,file FROM media_parts WHERE id=? LIMIT 2",
            (int(normalized_part),),
        ).fetchall()
    except sqlite3.Error as exc:
        raise PlexBifError(
            f"Plex library database could not resolve the media part: {exc}"
        ) from exc
    finally:
        connection.close()

    if not rows:
        return None
    if len(rows) != 1:
        raise PlexBifError("Plex library database returned an ambiguous media part.")
    media_hash, part_path = rows[0]
    if not _external_paths_equal(
        str(part_path or "").strip(),
        expected_external_path,
    ):
        raise PlexBifError(
            "Plex library database media path does not match the resolved media part."
        )
    bif_path = plex_bif_path_for_media_hash(root, str(media_hash or ""))
    try:
        allowed_root = (root / "Media" / "localhost").resolve(strict=True)
        resolved_bif = bif_path.resolve(strict=True)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PlexBifError(
            f"Plex local BIF path could not be resolved safely: {exc}"
        ) from exc
    if (
        resolved_bif.name != "index-sd.bif"
        or not resolved_bif.is_relative_to(allowed_root)
        or not resolved_bif.is_file()
    ):
        raise PlexBifError(
            "Plex local BIF path escapes the configured metadata root."
        )
    return resolved_bif


def read_bif_preview_range(
    path: str | Path,
    *,
    offset: int,
    length: int,
) -> bytes:
    bif_path = Path(path)
    start = int(offset)
    count = int(length)
    if start < 0 or count <= 0 or count > _MAX_PLEX_JPEG_BYTES:
        raise PlexBifError("Plex BIF preview byte range is outside safe bounds.")
    try:
        stat = bif_path.stat()
    except OSError as exc:
        raise PlexBifError(f"Plex BIF preview could not be inspected: {exc}") from exc
    if not bif_path.is_file() or start + count > stat.st_size:
        raise PlexBifError("Plex BIF preview byte range is outside the file.")
    try:
        with bif_path.open("rb") as handle:
            handle.seek(start)
            payload = handle.read(count)
    except OSError as exc:
        raise PlexBifError(f"Plex BIF preview could not be read: {exc}") from exc
    if len(payload) != count:
        raise PlexBifError("Plex BIF preview byte range was truncated.")
    try:
        return _validate_plex_jpeg(payload)
    except PlexPreviewUnavailable as exc:
        raise PlexPreviewUnavailable(
            f"Plex local BIF preview range is unusable: {exc}"
        ) from exc


def bif_source_signature(path: str | Path, index: PlexBifIndex) -> str:
    payload = json.dumps(
        {
            "path": str(Path(path)),
            "size": index.file_size,
            "mtime_ns": index.source_mtime_ns,
            "index_digest": index.index_digest,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "plex-bif:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_verified_bif_preview(
    path: str | Path,
    *,
    expected_signature: str,
    timestamp_ms: int,
    offset: int,
    length: int,
) -> bytes:
    """Verify the local BIF index and read one JPEG through the same open file."""
    bif_path = Path(path)
    start = int(offset)
    count = int(length)
    if start < 0 or count <= 0 or count > _MAX_PLEX_JPEG_BYTES:
        raise PlexBifError("Plex BIF preview byte range is outside safe bounds.")

    try:
        with bif_path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat_module.S_ISREG(before.st_mode):
                raise PlexPreviewUnavailable(
                    "Plex BIF path is no longer a regular file."
                )
            if before.st_size < _BIF_HEADER_SIZE + _BIF_INDEX_ENTRY_SIZE:
                raise PlexPreviewUnavailable(
                    "Plex BIF file is too small to contain an index."
                )
            if before.st_size > 0xFFFFFFFF:
                raise PlexPreviewUnavailable(
                    "Plex BIF file exceeds the supported version 0 offset range."
                )

            try:
                index_payload = _read_budgeted_bif_index_payload(
                    handle,
                    bif_path,
                    before,
                )
                current_index = parse_bif_index(
                    index_payload,
                    file_size=before.st_size,
                    source_mtime_ns=_bif_stat_mtime_ns(before),
                )
            except PlexBifError as exc:
                raise PlexPreviewUnavailable(
                    f"Plex local BIF index is unusable: {exc}"
                ) from exc

            if bif_source_signature(bif_path, current_index) != expected_signature:
                raise PlexPreviewUnavailable(
                    "Plex local BIF changed after preview frames were enumerated."
                )
            current_range = next(
                (
                    candidate
                    for candidate in current_index.frames
                    if candidate.timestamp_ms == int(timestamp_ms)
                ),
                None,
            )
            if (
                current_range is None
                or current_range.offset != start
                or current_range.length != count
            ):
                raise PlexPreviewUnavailable(
                    "Plex local BIF frame range changed after preview enumeration."
                )

            budget = current_visual_budget()
            frame_cache_key = (
                _bif_index_budget_cache_key(
                    bif_path,
                    before,
                )
                + f":frame:{start}:{count}"
            )
            cached_payload = (
                budget.cached_source_asset(frame_cache_key)
                if budget is not None
                else None
            )
            if cached_payload is not None:
                payload = bytes(cached_payload)
            else:
                account_source_bytes(count)
                handle.seek(start)
                payload = handle.read(count)
                if len(payload) == count and budget is not None:
                    budget.cache_source_asset(
                        frame_cache_key,
                        payload,
                    )
            after = os.fstat(handle.fileno())
    except PlexPreviewUnavailable:
        raise
    except OSError as exc:
        raise PlexPreviewUnavailable(
            f"Plex local BIF preview could not be read: {exc}"
        ) from exc

    before_mtime = getattr(
        before, "st_mtime_ns", int(before.st_mtime * 1_000_000_000)
    )
    after_mtime = getattr(
        after, "st_mtime_ns", int(after.st_mtime * 1_000_000_000)
    )
    if before.st_size != after.st_size or before_mtime != after_mtime:
        raise PlexPreviewUnavailable(
            "Plex local BIF changed while the preview frame was being read."
        )
    if len(payload) != count:
        raise PlexPreviewUnavailable("Plex local BIF preview range was truncated.")
    try:
        return _validate_plex_jpeg(payload)
    except PlexPreviewUnavailable as exc:
        raise PlexPreviewUnavailable(
            f"Plex local BIF preview range is unusable: {exc}"
        ) from exc


def enumerate_bif_preview_frames(
    *,
    item_id: str,
    bif_path: str | Path,
    index: PlexBifIndex,
    part_id: str = "",
    expected_external_path: str = "",
    metadata_root: str | Path = "",
) -> tuple[PreviewFrameRef, ...]:
    normalized_item_id = str(item_id or "").strip()
    if not normalized_item_id:
        raise PlexBifError("A Plex item id is required for BIF preview frames.")
    source_signature = bif_source_signature(bif_path, index)
    path_text = str(Path(bif_path))
    normalized_part = (
        _plex_numeric_id(part_id, "Plex part id")
        if str(part_id or "").strip()
        else ""
    )
    expected_path = str(expected_external_path or "").strip()
    root_text = (
        str(normalize_plex_metadata_root(metadata_root))
        if str(metadata_root or "").strip()
        else ""
    )
    if any((normalized_part, expected_path, root_text)) and not all(
        (normalized_part, expected_path, root_text)
    ):
        raise PlexBifError(
            "Local Plex BIF frames require part id, external path, and metadata root together."
        )
    frames: list[PreviewFrameRef] = []
    for frame in index.frames:
        frames.append(
            PreviewFrameRef(
                source_key="plex",
                item_id=normalized_item_id,
                timestamp_ms=frame.timestamp_ms,
                asset_ref=json.dumps(
                    {
                        "kind": "plex_bif",
                        "path": path_text,
                        "offset": frame.offset,
                        "length": frame.length,
                        "part_id": normalized_part,
                        "expected_external_path": expected_path,
                        "metadata_root": root_text,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                source_signature=source_signature,
            )
        )
    return tuple(frames)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _normalize_plex_server_url(value: str) -> str:
    raw = str(value or "")
    if not raw:
        raise PlexBifError("Plex server URL is required.")
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in raw
    ):
        raise PlexBifError("Plex server URL contains whitespace or control characters.")
    if any(ord(character) > 127 for character in raw):
        raise PlexBifError("Plex server URL must use ASCII characters.")
    try:
        parsed = urllib.parse.urlsplit(raw)
        scheme = parsed.scheme.casefold()
        hostname = parsed.hostname
        username = parsed.username
        password = parsed.password
        _port = parsed.port
    except ValueError as exc:
        raise PlexBifError("Plex server URL is invalid.") from exc
    if scheme not in {"http", "https"} or not hostname:
        raise PlexBifError("Plex server URL must be a complete HTTP or HTTPS address.")
    if username or password:
        raise PlexBifError("Do not put credentials in the Plex server URL.")
    if parsed.query or parsed.fragment:
        raise PlexBifError("Plex server URL cannot contain a query or fragment.")
    return urllib.parse.urlunsplit(
        (scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
    )


def _credential_transport_url(
    server_url: str,
    *,
    allow_insecure_http: bool = False,
) -> str:
    base = _normalize_plex_server_url(server_url)
    if (
        urllib.parse.urlsplit(base).scheme.casefold() == "http"
        and not allow_insecure_http
    ):
        raise PlexBifError(
            "Plex credentials will not be sent over plain HTTP. "
            "Use HTTPS or explicitly allow insecure HTTP for this integration."
        )
    return base


def _plex_numeric_id(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text.isdigit() or int(text) <= 0:
        raise PlexBifError(f"{label} must be a positive numeric Plex id.")
    return str(int(text))


def _external_paths_equal(left_value: str, right_value: str) -> bool:
    try:
        left = parse_absolute_path(left_value)
        right = parse_absolute_path(right_value)
    except PathMappingError:
        return False
    if left.windows != right.windows:
        return False

    def comparable(value: str) -> str:
        return value.casefold() if left.windows else value

    if comparable(left.anchor) != comparable(right.anchor):
        return False
    if len(left.parts) != len(right.parts):
        return False
    return all(
        comparable(actual) == comparable(expected)
        for actual, expected in zip(left.parts, right.parts)
    )


def _plex_headers(token: str, *, accept: str) -> dict[str, str]:
    credential = str(token or "").strip()
    if not credential:
        raise PlexBifError("A Plex access token is required.")
    return {
        "Accept": accept,
        "X-Plex-Token": credential,
        "X-Plex-Product": "InfoMancer",
        "X-Plex-Client-Identifier": "infomancer-episode-identity",
    }


def _read_plex_bytes(
    server_url: str,
    token: str,
    path: str,
    *,
    query: Mapping[str, object] | None = None,
    accept: str,
    max_bytes: int,
    timeout: float = 5.0,
    allow_insecure_http: bool = False,
) -> tuple[bytes, str]:
    base = _credential_transport_url(
        server_url,
        allow_insecure_http=allow_insecure_http,
    )
    route = str(path or "")
    if not route.startswith("/") or "\x00" in route:
        raise PlexBifError("Plex API path is invalid.")
    query_string = urllib.parse.urlencode(
        {key: value for key, value in (query or {}).items()}
    )
    url = base + route + (f"?{query_string}" if query_string else "")
    request = urllib.request.Request(
        url,
        headers=_plex_headers(token, accept=accept),
        method="GET",
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
    )
    configured_limit = max(1, int(max_bytes))
    limit, budget_limited = source_read_plan(configured_limit)
    try:
        with opener.open(
            request,
            timeout=max(1.0, min(float(timeout), 15.0)),
        ) as response:
            if response.status != 200:
                raise _PlexHttpError(
                    int(response.status),
                    f"Plex returned HTTP {response.status}.",
                )
            content_type = str(
                response.headers.get("Content-Type", "")
            ).split(";", 1)[0].strip().casefold()
            raw_length = response.headers.get("Content-Length")
            if raw_length not in {None, ""}:
                try:
                    content_length = int(raw_length)
                except (TypeError, ValueError) as exc:
                    raise PlexSourceFailure(
                        "Plex response had an invalid Content-Length."
                    ) from exc
                if content_length < 0:
                    raise PlexSourceFailure(
                        "Plex response had an invalid Content-Length."
                    )
                if content_length > limit:
                    if budget_limited:
                        deny_visual_budget(
                            "Normal visual source-byte budget cannot admit this Plex response."
                        )
                    raise _PlexResponseTooLarge(
                        "Plex response exceeded the safe size limit."
                    )
            payload = response.read(limit if budget_limited else limit + 1)
            account_source_bytes(len(payload))
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            detail = "Plex rejected the access token."
        elif 300 <= exc.code < 400:
            detail = "Plex redirected the request; save the final server URL instead."
        elif exc.code == 404:
            detail = "The requested Plex resource does not exist."
        else:
            detail = f"Plex returned HTTP {exc.code}."
        raise _PlexHttpError(exc.code, detail) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise PlexSourceFailure(f"InfoMancer could not read Plex: {reason}") from exc

    if len(payload) > limit:
        raise _PlexResponseTooLarge("Plex response exceeded the safe size limit.")
    return payload, content_type


def _read_plex_json(
    server_url: str,
    token: str,
    path: str,
    *,
    query: Mapping[str, object] | None = None,
    timeout: float = 5.0,
    allow_insecure_http: bool = False,
) -> Mapping[str, Any]:
    payload, content_type = _read_plex_bytes(
        server_url,
        token,
        path,
        query=query,
        accept="application/json",
        max_bytes=_MAX_PLEX_JSON_BYTES,
        timeout=timeout,
        allow_insecure_http=allow_insecure_http,
    )
    if content_type and content_type not in {
        "application/json",
        "text/json",
        "text/plain",
    }:
        raise PlexSourceFailure("Plex returned an unexpected metadata content type.")
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlexSourceFailure("Plex returned malformed JSON metadata.") from exc
    if not isinstance(parsed, Mapping):
        raise PlexSourceFailure("Plex returned an unexpected metadata payload.")
    return parsed


def fetch_plex_episode_candidates(
    server_url: str,
    token: str,
    *,
    season: int,
    episode: int,
    timeout: float = 5.0,
    allow_insecure_http: bool = False,
) -> tuple[Mapping[str, Any], ...]:
    season_number = int(season)
    episode_number = int(episode)
    if season_number < 0 or episode_number < 0:
        raise PlexBifError("Plex episode coordinates cannot be negative.")

    items: list[Mapping[str, Any]] = []
    expected_total: int | None = None
    start = 0
    while True:
        payload = _read_plex_json(
            server_url,
            token,
            "/library/all",
            query={
                "type": 4,
                "parentIndex": season_number,
                "index": episode_number,
                "includeGuids": 1,
                "X-Plex-Container-Start": start,
                "X-Plex-Container-Size": _PLEX_PAGE_SIZE,
            },
            timeout=timeout,
            allow_insecure_http=allow_insecure_http,
        )
        container = payload.get("MediaContainer")
        if not isinstance(container, Mapping):
            raise PlexSourceFailure("Plex episode search returned an invalid container.")
        raw_items = container.get("Metadata", ())
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
            raise PlexSourceFailure("Plex episode search returned an invalid item list.")
        if any(not isinstance(item, Mapping) for item in raw_items):
            raise PlexSourceFailure(
                "Plex episode search returned a malformed candidate entry."
            )
        try:
            returned_offset = int(container.get("offset", start))
            returned_size = int(container.get("size", len(raw_items)))
            raw_total = container.get("totalSize")
            total = int(raw_total) if raw_total is not None else None
        except (TypeError, ValueError) as exc:
            raise PlexSourceFailure(
                "Plex episode search returned invalid pagination metadata."
            ) from exc
        if returned_offset != start or returned_size != len(raw_items):
            raise PlexSourceFailure(
                "Plex episode search returned inconsistent pagination metadata."
            )
        if len(raw_items) > _PLEX_PAGE_SIZE:
            raise PlexSourceFailure(
                "Plex episode search returned more items than the requested page size."
            )
        if total is not None:
            if total < 0 or total > _MAX_EPISODE_CANDIDATES:
                raise PlexBifError(
                    "Plex returned too many episode candidates to resolve safely."
                )
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                raise PlexSourceFailure(
                    "Plex episode search changed while candidates were being paged."
                )
        if len(items) + len(raw_items) > _MAX_EPISODE_CANDIDATES:
            raise PlexBifError(
                "Plex returned too many episode candidates to resolve safely."
            )
        items.extend(raw_items)

        if expected_total is not None:
            if len(items) > expected_total:
                raise PlexBifError(
                    "Plex episode search returned more candidates than declared."
                )
            if len(items) == expected_total:
                break
            if not raw_items:
                raise PlexSourceFailure(
                    "Plex episode search ended before all candidates were returned."
                )
        elif not raw_items:
            break

        start += len(raw_items)

    return tuple(items)


def fetch_plex_path_candidates(
    server_url: str,
    token: str,
    *,
    path: str,
    timeout: float = 5.0,
    allow_insecure_http: bool = False,
) -> tuple[Mapping[str, Any], ...]:
    expected_path = str(path or "").strip()
    try:
        parse_absolute_path(expected_path)
    except PathMappingError as exc:
        raise PlexBifError("Plex path lookup requires an absolute media path.") from exc

    items: list[Mapping[str, Any]] = []
    expected_total: int | None = None
    start = 0
    while True:
        payload = _read_plex_json(
            server_url,
            token,
            "/library/all",
            query={
                "type": 4,
                "path": expected_path,
                "includeGuids": 1,
                "X-Plex-Container-Start": start,
                "X-Plex-Container-Size": _PLEX_PAGE_SIZE,
            },
            timeout=timeout,
            allow_insecure_http=allow_insecure_http,
        )
        container = payload.get("MediaContainer")
        if not isinstance(container, Mapping):
            raise PlexSourceFailure(
                "Plex path lookup returned an invalid container."
            )
        raw_items = container.get("Metadata", ())
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
            raise PlexSourceFailure(
                "Plex path lookup returned an invalid item list."
            )
        if any(not isinstance(item, Mapping) for item in raw_items):
            raise PlexSourceFailure(
                "Plex path lookup returned a malformed candidate entry."
            )
        if "totalSize" not in container:
            raise PlexSourceFailure(
                "Plex path lookup did not report a complete result count."
            )
        try:
            total = int(container["totalSize"])
            returned_offset = int(container.get("offset", start))
            returned_size = int(container.get("size", len(raw_items)))
        except (TypeError, ValueError) as exc:
            raise PlexSourceFailure(
                "Plex path lookup returned invalid pagination metadata."
            ) from exc
        if total < 0 or total > _MAX_EPISODE_CANDIDATES:
            raise PlexSourceFailure(
                "Plex path lookup returned too many candidates safely."
            )
        if returned_offset != start or returned_size != len(raw_items):
            raise PlexSourceFailure(
                "Plex path lookup returned inconsistent pagination metadata."
            )
        if len(raw_items) > _PLEX_PAGE_SIZE:
            raise PlexSourceFailure(
                "Plex path lookup returned more items than the requested page size."
            )
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise PlexSourceFailure(
                "Plex path lookup changed while candidates were being paged."
            )
        if len(items) + len(raw_items) > _MAX_EPISODE_CANDIDATES:
            raise PlexSourceFailure(
                "Plex path lookup returned too many candidates safely."
            )
        items.extend(raw_items)

        if len(items) > expected_total:
            raise PlexSourceFailure(
                "Plex path lookup returned more candidates than declared."
            )
        if len(items) == expected_total:
            break
        if not raw_items:
            raise PlexSourceFailure(
                "Plex path lookup ended before all candidates were returned."
            )
        start += len(raw_items)

    return tuple(items)


def _iter_plex_parts(
    item: Mapping[str, Any],
) -> tuple[tuple[Mapping[str, Any], Mapping[str, Any]], ...]:
    raw_media = item.get("Media", ())
    if not isinstance(raw_media, Sequence) or isinstance(raw_media, (str, bytes)):
        raise PlexSourceFailure("Plex returned an invalid Media list.")
    parts: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for media in raw_media:
        if not isinstance(media, Mapping):
            raise PlexSourceFailure("Plex returned a malformed Media entry.")
        raw_parts = media.get("Part", ())
        if not isinstance(raw_parts, Sequence) or isinstance(raw_parts, (str, bytes)):
            raise PlexSourceFailure("Plex returned an invalid Part list.")
        for part in raw_parts:
            if not isinstance(part, Mapping):
                raise PlexSourceFailure("Plex returned a malformed Part entry.")
            _plex_numeric_id(part.get("id"), "Plex part id")
            parts.append((media, part))
    return tuple(parts)


def _plex_provider_ids(item: Mapping[str, Any]) -> dict[str, str]:
    raw_guids = item.get("Guid", ())
    if not isinstance(raw_guids, Sequence) or isinstance(raw_guids, (str, bytes)):
        return {}
    result: dict[str, str] = {}
    for entry in raw_guids:
        if not isinstance(entry, Mapping):
            continue
        raw = str(entry.get("id") or "").strip()
        if "://" not in raw:
            continue
        provider, value = raw.split("://", 1)
        provider = provider.strip().casefold()
        value = value.strip()
        if provider and value and provider not in result:
            result[provider] = value
    return result


def resolve_plex_media_ref(
    items: Sequence[Mapping[str, Any]],
    *,
    expected_external_path: str,
) -> ExternalMediaRef | None:
    matches: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise PlexBifError("Plex returned a malformed episode candidate.")
        for _media, part in _iter_plex_parts(item):
            part_path = str(part.get("file") or "").strip()
            if part_path and _external_paths_equal(
                part_path, expected_external_path
            ):
                matches.append((item, part))

    if len(matches) > 1:
        raise PlexBifError(
            "More than one Plex media part matches the mapped media path; resolution is ambiguous."
        )
    if not matches:
        return None

    item, part = matches[0]
    item_id = _plex_numeric_id(item.get("ratingKey"), "Plex rating key")
    part_id = _plex_numeric_id(part.get("id"), "Plex part id")
    updated_at = str(item.get("updatedAt") or "").strip()
    part_key = str(part.get("key") or "").strip()
    signature_payload = json.dumps(
        {
            "item": item_id,
            "part": part_id,
            "path": expected_external_path,
            "updated_at": updated_at,
            "part_key": part_key,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    signature = "plex-item:" + hashlib.sha256(
        signature_payload.encode("utf-8")
    ).hexdigest()
    return ExternalMediaRef(
        source_key="plex",
        item_id=item_id,
        path=expected_external_path,
        media_source_id=part_id,
        provider_ids=_plex_provider_ids(item),
        source_signature=signature,
    )


def fetch_plex_item(
    server_url: str,
    token: str,
    item_id: str,
    *,
    timeout: float = 5.0,
    allow_insecure_http: bool = False,
) -> Mapping[str, Any]:
    normalized_id = _plex_numeric_id(item_id, "Plex rating key")
    payload = _read_plex_json(
        server_url,
        token,
        f"/library/metadata/{urllib.parse.quote(normalized_id, safe='')}",
        query={"includeGuids": 1},
        timeout=timeout,
        allow_insecure_http=allow_insecure_http,
    )
    container = payload.get("MediaContainer")
    if not isinstance(container, Mapping):
        raise PlexSourceFailure("Plex item lookup returned an invalid container.")
    raw_items = container.get("Metadata", ())
    if (
        not isinstance(raw_items, Sequence)
        or isinstance(raw_items, (str, bytes))
        or len(raw_items) != 1
        or not isinstance(raw_items[0], Mapping)
    ):
        raise PlexSourceFailure("Plex item lookup did not return exactly one item.")
    returned_id = _plex_numeric_id(raw_items[0].get("ratingKey"), "Plex rating key")
    if returned_id != normalized_id:
        raise PlexSourceFailure("Plex returned a different item than the one requested.")
    return raw_items[0]


def fetch_plex_bif_index(
    server_url: str,
    token: str,
    part_id: str,
    *,
    timeout: float = 10.0,
    allow_insecure_http: bool = False,
) -> PlexBifIndex:
    normalized_part = _plex_numeric_id(part_id, "Plex part id")
    try:
        payload, content_type = _read_plex_bytes(
            server_url,
            token,
            f"/library/parts/{normalized_part}/indexes/sd",
            accept="application/octet-stream",
            max_bytes=_MAX_PLEX_BIF_BYTES,
            timeout=timeout,
            allow_insecure_http=allow_insecure_http,
        )
    except _PlexHttpError as exc:
        if exc.status_code == 404:
            raise PlexPreviewUnavailable(
                "Plex has no BIF preview asset for this media part."
            ) from exc
        raise
    except _PlexResponseTooLarge as exc:
        raise PlexPreviewUnavailable(
            "Plex BIF preview exceeds the safe size limit."
        ) from exc
    if content_type and content_type not in {
        "application/octet-stream",
        "application/bif",
        "binary/octet-stream",
    }:
        raise PlexPreviewUnavailable(
            "Plex returned an unsupported BIF content type."
        )
    if len(payload) < _BIF_HEADER_SIZE:
        raise PlexPreviewUnavailable("Plex returned a truncated BIF.")
    try:
        _, image_count, _ = _header_fields(payload[:_BIF_HEADER_SIZE])
        index_size = _BIF_HEADER_SIZE + (image_count + 1) * _BIF_INDEX_ENTRY_SIZE
        if index_size > len(payload):
            raise PlexBifError("Plex returned a truncated BIF index.")
        parsed = parse_bif_index(
            payload[:index_size],
            file_size=len(payload),
        )
    except PlexBifError as exc:
        raise PlexPreviewUnavailable(
            f"Plex BIF preview index is unusable: {exc}"
        ) from exc
    frame_digests = tuple(
        hashlib.sha256(
            payload[frame.offset : frame.offset + frame.length]
        ).hexdigest()
        for frame in parsed.frames
    )
    return replace(
        parsed,
        index_digest=hashlib.sha256(payload).hexdigest(),
        frame_digests=frame_digests,
    )


def _http_bif_signature(part_id: str, index: PlexBifIndex) -> str:
    payload = json.dumps(
        {
            "part": _plex_numeric_id(part_id, "Plex part id"),
            "size": index.file_size,
            "index_digest": index.index_digest,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "plex-bif-http:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def enumerate_plex_http_preview_frames(
    *,
    item_id: str,
    part_id: str,
    index: PlexBifIndex,
) -> tuple[PreviewFrameRef, ...]:
    normalized_item = _plex_numeric_id(item_id, "Plex rating key")
    normalized_part = _plex_numeric_id(part_id, "Plex part id")
    if len(index.frame_digests) != len(index.frames):
        raise PlexBifError(
            "Plex HTTP BIF frame digests are unavailable for safe preview reads."
        )
    signature = _http_bif_signature(normalized_part, index)
    return tuple(
        PreviewFrameRef(
            source_key="plex",
            item_id=normalized_item,
            timestamp_ms=frame.timestamp_ms,
            asset_ref=json.dumps(
                {
                    "kind": "plex_bif_http",
                    "part_id": normalized_part,
                    "timestamp_ms": frame.timestamp_ms,
                    "offset": frame.offset,
                    "length": frame.length,
                    "sha256": index.frame_digests[position],
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ),
            source_signature=signature,
        )
        for position, frame in enumerate(index.frames)
    )


def fetch_plex_bif_image(
    server_url: str,
    token: str,
    part_id: str,
    timestamp_ms: int,
    *,
    timeout: float = 5.0,
    allow_insecure_http: bool = False,
) -> bytes:
    normalized_part = _plex_numeric_id(part_id, "Plex part id")
    offset = int(timestamp_ms)
    if offset < 0 or offset > _MAX_TIMESTAMP_MS:
        raise PlexBifError("Plex BIF image timestamp is outside the safe range.")
    try:
        payload, content_type = _read_plex_bytes(
            server_url,
            token,
            f"/library/parts/{normalized_part}/indexes/sd/{offset}",
            accept="image/jpeg",
            max_bytes=_MAX_PLEX_JPEG_BYTES,
            timeout=timeout,
            allow_insecure_http=allow_insecure_http,
        )
    except _PlexHttpError as exc:
        if exc.status_code == 404:
            raise PlexPreviewUnavailable(
                "Plex no longer has the requested preview frame."
            ) from exc
        raise
    except _PlexResponseTooLarge as exc:
        raise PlexPreviewUnavailable(
            "Plex preview frame exceeds the safe size limit."
        ) from exc
    if content_type and content_type not in {"image/jpeg", "image/jpg"}:
        raise PlexPreviewUnavailable(
            "Plex returned an unsupported preview-image content type."
        )
    return _validate_plex_jpeg(payload)


class PlexBifSource:
    """Configured read-only Plex BIF source for Episode Identity."""

    source_key = "plex"
    version = "0.9-pr-g"

    def __init__(
        self,
        server_url: str,
        token: str,
        mapper: ExternalPathMapper,
        *,
        metadata_root: str = "",
        enabled: bool = True,
        last_test_status: str = "",
        allow_insecure_http: bool = False,
        advertise_preview_frames: bool = False,
    ) -> None:
        self.server_url = (
            _normalize_plex_server_url(server_url)
            if str(server_url or "").strip()
            else ""
        )
        self._token = str(token or "").strip()
        self.mapper = mapper
        self.metadata_root = str(metadata_root or "").strip()
        self.enabled = bool(enabled)
        self.last_test_status = str(last_test_status or "").strip().casefold()
        self.allow_insecure_http = bool(allow_insecure_http)
        self.advertise_preview_frames = bool(advertise_preview_frames)

    def status(self) -> ExternalSourceStatus:
        if not self.enabled:
            return ExternalSourceStatus(
                source_key=self.source_key,
                available=False,
                detail="Disabled in Settings.",
            )
        if not self.server_url:
            return ExternalSourceStatus(
                source_key=self.source_key,
                available=False,
                detail="Server URL is not configured.",
            )
        if not self._token:
            return ExternalSourceStatus(
                source_key=self.source_key,
                available=False,
                detail="Access token is not configured or is not bound to this server URL.",
            )
        if self.last_test_status == "error":
            return ExternalSourceStatus(
                source_key=self.source_key,
                available=False,
                detail="Configured; the last explicit connection test failed.",
            )
        if (
            urllib.parse.urlsplit(self.server_url).scheme.casefold() == "http"
            and not self.allow_insecure_http
        ):
            return ExternalSourceStatus(
                source_key=self.source_key,
                available=False,
                detail=(
                    "Plex uses plain HTTP. Use HTTPS or explicitly allow insecure "
                    "HTTP before InfoMancer sends the access token."
                ),
            )
        if not self.mapper.mappings_for(self.source_key):
            return ExternalSourceStatus(
                source_key=self.source_key,
                available=False,
                detail="Add at least one Plex-to-InfoMancer path mapping.",
            )
        capabilities = (
            frozenset({ExternalCapability.PREVIEW_FRAMES})
            if self.advertise_preview_frames
            else frozenset()
        )
        detail = (
            "Plex BIF preview frames are ready for Episode Identity."
            if self.advertise_preview_frames
            else (
                "Plex BIF adapter is configured and validated. "
                "Preview-frame analysis becomes available when a consuming analyzer is installed."
            )
        )
        return ExternalSourceStatus(
            source_key=self.source_key,
            available=True,
            capabilities=capabilities,
            detail=detail,
        )

    def resolve_media(self, context: AnalyzerContext) -> ExternalMediaRef | None:
        if not self.status().available:
            return None
        try:
            translation = self.mapper.reverse_translate(
                self.source_key,
                context.media.path,
            )
        except PathMappingError as exc:
            raise PlexSourceFailure(
                f"Plex path mapping could not resolve this media unambiguously: {exc}"
            ) from exc
        if translation is None:
            return None
        candidates = fetch_plex_path_candidates(
            self.server_url,
            self._token,
            path=translation.external_path,
            allow_insecure_http=self.allow_insecure_http,
        )
        resolved = resolve_plex_media_ref(
            candidates,
            expected_external_path=translation.external_path,
        )

        if resolved is None:
            season = context.claimed_identity.season
            episode = context.claimed_identity.episode
            if season is None or episode is None:
                return None
            candidates = fetch_plex_episode_candidates(
                self.server_url,
                self._token,
                season=season,
                episode=episode,
                allow_insecure_http=self.allow_insecure_http,
            )
            resolved = resolve_plex_media_ref(
                candidates,
                expected_external_path=translation.external_path,
            )
            if resolved is None:
                return None

        detail = fetch_plex_item(
            self.server_url,
            self._token,
            resolved.item_id,
            allow_insecure_http=self.allow_insecure_http,
        )
        current = resolve_plex_media_ref(
            (detail,),
            expected_external_path=translation.external_path,
        )
        if current is None:
            raise PlexBifError(
                "The Plex episode changed while it was being resolved."
            )
        if (
            current.item_id != resolved.item_id
            or current.media_source_id != resolved.media_source_id
        ):
            raise PlexBifError(
                "The Plex episode media version changed while it was being resolved."
            )
        return current

    def preview_frames(
        self,
        media: ExternalMediaRef,
    ) -> tuple[PreviewFrameRef, ...]:
        if not self.status().available:
            return ()
        if str(media.source_key or "").strip().casefold() != self.source_key:
            return ()
        if not media.item_id or not media.path or not media.media_source_id:
            return ()

        item = fetch_plex_item(
            self.server_url,
            self._token,
            media.item_id,
            allow_insecure_http=self.allow_insecure_http,
        )
        current = resolve_plex_media_ref(
            (item,),
            expected_external_path=media.path,
        )
        if (
            current is None
            or current.item_id != media.item_id
            or current.media_source_id != media.media_source_id
        ):
            return ()

        try:
            index = fetch_plex_bif_index(
                self.server_url,
                self._token,
                current.media_source_id,
                allow_insecure_http=self.allow_insecure_http,
            )
        except PlexPreviewUnavailable:
            metadata_root = effective_plex_metadata_root(self.metadata_root)
            if metadata_root is None:
                return ()
            try:
                bif_path = resolve_local_bif_path(
                    metadata_root,
                    part_id=current.media_source_id,
                    expected_external_path=current.path,
                )
                if bif_path is None:
                    return ()
                local_index = read_bif_index(bif_path)
                return enumerate_bif_preview_frames(
                    item_id=current.item_id,
                    bif_path=bif_path,
                    index=local_index,
                    part_id=current.media_source_id,
                    expected_external_path=current.path,
                    metadata_root=metadata_root,
                )
            except PlexBifError:
                return ()
        return enumerate_plex_http_preview_frames(
            item_id=current.item_id,
            part_id=current.media_source_id,
            index=index,
        )

    def read_preview(self, frame: PreviewFrameRef) -> bytes:
        if not self.status().available:
            raise PlexBifError(
                "Plex BIF preview reuse is not currently available."
            )
        if str(frame.source_key or "").strip().casefold() != self.source_key:
            raise PlexBifError("Preview frame does not belong to Plex.")
        try:
            asset = json.loads(frame.asset_ref)
        except json.JSONDecodeError as exc:
            raise PlexBifError("Plex preview asset reference is malformed.") from exc
        if not isinstance(asset, Mapping):
            raise PlexBifError("Plex preview asset reference is invalid.")
        kind = str(asset.get("kind") or "")
        if kind == "plex_bif_http":
            part_id = _plex_numeric_id(asset.get("part_id"), "Plex part id")
            try:
                timestamp_ms = int(asset.get("timestamp_ms"))
                expected_length = int(asset.get("length"))
            except (TypeError, ValueError) as exc:
                raise PlexBifError("Plex preview frame reference is invalid.") from exc
            expected_sha256 = str(asset.get("sha256") or "").strip().casefold()
            if (
                len(expected_sha256) != 64
                or any(character not in "0123456789abcdef" for character in expected_sha256)
            ):
                raise PlexBifError("Plex preview frame digest is invalid.")
            if expected_length <= 0 or expected_length > _MAX_PLEX_JPEG_BYTES:
                raise PlexBifError("Plex preview frame length is outside safe bounds.")
            if timestamp_ms != int(frame.timestamp_ms):
                raise PlexBifError(
                    "Plex preview asset timestamp does not match its frame."
                )
            payload = fetch_plex_bif_image(
                self.server_url,
                self._token,
                part_id,
                timestamp_ms,
                allow_insecure_http=self.allow_insecure_http,
            )
            if len(payload) != expected_length:
                raise PlexPreviewUnavailable(
                    "Plex BIF frame length changed after preview enumeration."
                )
            if hashlib.sha256(payload).hexdigest() != expected_sha256:
                raise PlexPreviewUnavailable(
                    "Plex BIF frame content changed after preview enumeration."
                )
            return payload
        if kind == "plex_bif":
            try:
                offset = int(asset.get("offset"))
                length = int(asset.get("length"))
                part_id = _plex_numeric_id(asset.get("part_id"), "Plex part id")
            except (TypeError, ValueError) as exc:
                raise PlexBifError("Plex local BIF reference is invalid.") from exc
            expected_external_path = str(
                asset.get("expected_external_path") or ""
            ).strip()
            asset_root = str(asset.get("metadata_root") or "").strip()
            if not expected_external_path or not asset_root:
                raise PlexBifError(
                    "Plex local BIF reference is missing its media identity anchor."
                )
            current_root = effective_plex_metadata_root(self.metadata_root)
            if current_root is None:
                raise PlexPreviewUnavailable(
                    "The Plex metadata root is no longer available."
                )
            try:
                normalized_asset_root = normalize_plex_metadata_root(asset_root).resolve()
                normalized_current_root = current_root.resolve()
            except OSError as exc:
                raise PlexBifError(
                    f"Plex metadata root could not be resolved: {exc}"
                ) from exc
            if normalized_asset_root != normalized_current_root:
                raise PlexPreviewUnavailable(
                    "Plex metadata root changed after preview enumeration."
                )
            candidate = Path(str(asset.get("path") or ""))
            try:
                allowed_root = (
                    normalized_current_root / "Media" / "localhost"
                ).resolve()
                resolved_candidate = candidate.resolve(strict=True)
            except OSError as exc:
                raise PlexPreviewUnavailable(
                    f"Plex local BIF preview path could not be resolved: {exc}"
                ) from exc
            if (
                resolved_candidate.name != "index-sd.bif"
                or not resolved_candidate.is_relative_to(allowed_root)
            ):
                raise PlexBifError(
                    "Plex local BIF preview path is outside the configured metadata root."
                )
            try:
                current_bif_path = resolve_local_bif_path(
                    normalized_current_root,
                    part_id=part_id,
                    expected_external_path=expected_external_path,
                )
            except PlexBifError as exc:
                raise PlexPreviewUnavailable(
                    f"Plex media identity anchor changed after preview enumeration: {exc}"
                ) from exc
            if (
                current_bif_path is None
                or current_bif_path.resolve() != resolved_candidate
            ):
                raise PlexPreviewUnavailable(
                    "Plex media identity anchor changed after preview enumeration."
                )
            return read_verified_bif_preview(
                resolved_candidate,
                expected_signature=frame.source_signature,
                timestamp_ms=int(frame.timestamp_ms),
                offset=offset,
                length=length,
            )
        raise PlexBifError("Plex preview asset reference is invalid.")

    def subtitles(self, media: ExternalMediaRef):
        return ()

    def read_subtitle(self, subtitle):
        raise PlexBifError(
            "Plex subtitle ingestion is not implemented by the BIF adapter."
        )

    def media_metadata(self, media: ExternalMediaRef):
        return {}

    def fingerprints(self, media: ExternalMediaRef):
        return ()

    def known_identity(self, media: ExternalMediaRef):
        return None

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import struct
from typing import Sequence

from ..external import PreviewFrameRef


_BIF_MAGIC = b"\x89BIF\r\n\x1a\n"
_BIF_HEADER_SIZE = 64
_BIF_INDEX_ENTRY_SIZE = 8
_BIF_END_TIMESTAMP = 0xFFFFFFFF
_BIF_VERSION = 0
_MAX_BIF_IMAGES = 100_000
_MAX_TIMESTAMP_MS = (1 << 63) - 1


class PlexBifError(ValueError):
    """Raised when a Plex BIF asset is malformed, unsupported, or unsafe."""


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
        if previous_timestamp is not None and timestamp < previous_timestamp:
            raise PlexBifError("Plex BIF timestamps are not monotonic.")
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


def read_bif_index(path: str | Path) -> PlexBifIndex:
    """Read only the bounded BIF header/index from disk, never the JPEG section."""
    bif_path = Path(path)
    try:
        stat = bif_path.stat()
    except OSError as exc:
        raise PlexBifError(f"Plex BIF could not be inspected: {exc}") from exc
    if not bif_path.is_file():
        raise PlexBifError("Plex BIF path is not a regular file.")
    if stat.st_size < _BIF_HEADER_SIZE + _BIF_INDEX_ENTRY_SIZE:
        raise PlexBifError("Plex BIF file is too small to contain an index.")
    if stat.st_size > 0xFFFFFFFF:
        raise PlexBifError("Plex BIF file exceeds the version 0 offset range.")

    try:
        with bif_path.open("rb") as handle:
            header = handle.read(_BIF_HEADER_SIZE)
            _, image_count, _ = _header_fields(header)
            index_size = (image_count + 1) * _BIF_INDEX_ENTRY_SIZE
            index_bytes = handle.read(index_size)
    except OSError as exc:
        raise PlexBifError(f"Plex BIF index could not be read: {exc}") from exc

    if len(index_bytes) != index_size:
        raise PlexBifError("Plex BIF index is truncated.")
    return parse_bif_index(
        header + index_bytes,
        file_size=stat.st_size,
        source_mtime_ns=getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)),
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


def enumerate_bif_preview_frames(
    *,
    item_id: str,
    bif_path: str | Path,
    index: PlexBifIndex,
) -> tuple[PreviewFrameRef, ...]:
    normalized_item_id = str(item_id or "").strip()
    if not normalized_item_id:
        raise PlexBifError("A Plex item id is required for BIF preview frames.")
    source_signature = bif_source_signature(bif_path, index)
    path_text = str(Path(bif_path))
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
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                source_signature=source_signature,
            )
        )
    return tuple(frames)

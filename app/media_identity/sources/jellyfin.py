from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence

from ...path_mapping import PathMappingError, parse_absolute_path
from ..external import ExternalMediaRef, PreviewFrameRef


_MAX_THUMBNAILS = 1_000_000
_MAX_DIMENSION = 16_384
_MAX_TILE_AXIS = 1_024
_MAX_INTERVAL_MS = 86_400_000


class JellyfinAdapterError(ValueError):
    """Raised when Jellyfin metadata is ambiguous or cannot be trusted safely."""


@dataclass(frozen=True)
class JellyfinTrickplayVariant:
    """One Jellyfin Trickplay manifest for one media source and thumbnail width."""

    media_source_id: str
    width: int
    height: int
    tile_width: int
    tile_height: int
    thumbnail_count: int
    interval_ms: int
    bandwidth: int = 0

    def __post_init__(self) -> None:
        media_source_id = str(self.media_source_id or "").strip()
        if not media_source_id:
            raise JellyfinAdapterError("Trickplay metadata requires a media source id.")
        numeric = {
            "width": int(self.width),
            "height": int(self.height),
            "tile_width": int(self.tile_width),
            "tile_height": int(self.tile_height),
            "thumbnail_count": int(self.thumbnail_count),
            "interval_ms": int(self.interval_ms),
            "bandwidth": int(self.bandwidth),
        }
        if not 1 <= numeric["width"] <= _MAX_DIMENSION:
            raise JellyfinAdapterError("Trickplay thumbnail width is outside safe bounds.")
        if not 1 <= numeric["height"] <= _MAX_DIMENSION:
            raise JellyfinAdapterError("Trickplay thumbnail height is outside safe bounds.")
        if not 1 <= numeric["tile_width"] <= _MAX_TILE_AXIS:
            raise JellyfinAdapterError("Trickplay tile width is outside safe bounds.")
        if not 1 <= numeric["tile_height"] <= _MAX_TILE_AXIS:
            raise JellyfinAdapterError("Trickplay tile height is outside safe bounds.")
        if not 1 <= numeric["thumbnail_count"] <= _MAX_THUMBNAILS:
            raise JellyfinAdapterError("Trickplay thumbnail count is outside safe bounds.")
        if not 1 <= numeric["interval_ms"] <= _MAX_INTERVAL_MS:
            raise JellyfinAdapterError("Trickplay interval is outside safe bounds.")
        if numeric["bandwidth"] < 0:
            raise JellyfinAdapterError("Trickplay bandwidth cannot be negative.")
        object.__setattr__(self, "media_source_id", media_source_id)
        for key, value in numeric.items():
            object.__setattr__(self, key, value)

    @property
    def thumbnails_per_tile(self) -> int:
        return self.tile_width * self.tile_height

    @property
    def tile_count(self) -> int:
        return (self.thumbnail_count + self.thumbnails_per_tile - 1) // self.thumbnails_per_tile


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def parse_trickplay_variants(item: Mapping[str, Any]) -> tuple[JellyfinTrickplayVariant, ...]:
    """Parse only internally consistent Jellyfin Trickplay manifests.

    Jellyfin exposes BaseItemDto.Trickplay as media-source id -> width -> TrickplayInfoDto.
    Invalid variants are ignored so one corrupt width does not poison otherwise usable data.
    """
    trickplay = _as_mapping(item.get("Trickplay"))
    if not trickplay:
        return ()

    variants: list[JellyfinTrickplayVariant] = []
    for raw_media_source_id, raw_widths in trickplay.items():
        media_source_id = str(raw_media_source_id or "").strip()
        widths = _as_mapping(raw_widths)
        if not media_source_id or not widths:
            continue
        for raw_width_key, raw_info in widths.items():
            info = _as_mapping(raw_info)
            if not info:
                continue
            try:
                key_width = int(raw_width_key)
                payload_width = int(info.get("Width", key_width))
                if payload_width != key_width:
                    continue
                variant = JellyfinTrickplayVariant(
                    media_source_id=media_source_id,
                    width=payload_width,
                    height=int(info.get("Height", 0)),
                    tile_width=int(info.get("TileWidth", 0)),
                    tile_height=int(info.get("TileHeight", 0)),
                    thumbnail_count=int(info.get("ThumbnailCount", 0)),
                    interval_ms=int(info.get("Interval", 0)),
                    bandwidth=int(info.get("Bandwidth", 0) or 0),
                )
            except (TypeError, ValueError, JellyfinAdapterError):
                continue
            variants.append(variant)

    return tuple(
        sorted(
            variants,
            key=lambda value: (
                value.media_source_id,
                value.width,
                value.height,
                value.interval_ms,
            ),
        )
    )


def select_trickplay_variant(
    variants: Sequence[JellyfinTrickplayVariant],
    *,
    media_source_id: str,
) -> JellyfinTrickplayVariant | None:
    """Choose the highest-resolution manifest only within the resolved media source."""
    expected = str(media_source_id or "").strip()
    if not expected:
        return None
    matches = [value for value in variants if value.media_source_id == expected]
    if not matches:
        return None
    return max(
        matches,
        key=lambda value: (
            value.width,
            value.height,
            -value.interval_ms,
            value.thumbnail_count,
        ),
    )


def trickplay_source_signature(
    item_id: str,
    item_etag: str,
    variant: JellyfinTrickplayVariant,
) -> str:
    payload = json.dumps(
        {
            "item_id": str(item_id or "").strip(),
            "item_etag": str(item_etag or "").strip(),
            "media_source_id": variant.media_source_id,
            "width": variant.width,
            "height": variant.height,
            "tile_width": variant.tile_width,
            "tile_height": variant.tile_height,
            "thumbnail_count": variant.thumbnail_count,
            "interval_ms": variant.interval_ms,
            "bandwidth": variant.bandwidth,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "jellyfin-trickplay:" + hashlib.sha256(payload.encode("ascii")).hexdigest()


def _asset_ref(
    variant: JellyfinTrickplayVariant,
    *,
    tile_index: int,
    row: int,
    column: int,
) -> str:
    return json.dumps(
        {
            "kind": "jellyfin_trickplay",
            "media_source_id": variant.media_source_id,
            "width": variant.width,
            "tile_index": int(tile_index),
            "row": int(row),
            "column": int(column),
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def enumerate_preview_frames(
    *,
    item_id: str,
    item_etag: str,
    variant: JellyfinTrickplayVariant,
) -> tuple[PreviewFrameRef, ...]:
    """Map logical timestamps to Jellyfin tile-sheet cells without reading image bytes."""
    normalized_item_id = str(item_id or "").strip()
    if not normalized_item_id:
        raise JellyfinAdapterError("A Jellyfin item id is required for Trickplay frames.")
    signature = trickplay_source_signature(normalized_item_id, item_etag, variant)
    cells_per_tile = variant.thumbnails_per_tile
    frames: list[PreviewFrameRef] = []
    for thumbnail_index in range(variant.thumbnail_count):
        tile_index, cell_index = divmod(thumbnail_index, cells_per_tile)
        row, column = divmod(cell_index, variant.tile_width)
        frames.append(
            PreviewFrameRef(
                source_key="jellyfin",
                item_id=normalized_item_id,
                timestamp_ms=thumbnail_index * variant.interval_ms,
                asset_ref=_asset_ref(
                    variant,
                    tile_index=tile_index,
                    row=row,
                    column=column,
                ),
                source_signature=signature,
                width=variant.width,
                height=variant.height,
            )
        )
    return tuple(frames)


def external_paths_equal(first: str, second: str) -> bool:
    """Compare Jellyfin paths with source-style semantics independent of host OS."""
    try:
        left = parse_absolute_path(first)
        right = parse_absolute_path(second)
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


def _select_item_by_path(
    items: Sequence[Mapping[str, Any]],
    expected_external_path: str,
) -> Mapping[str, Any] | None:
    matches = [
        item
        for item in items
        if isinstance(item, Mapping)
        and external_paths_equal(str(item.get("Path") or ""), expected_external_path)
    ]
    if len(matches) > 1:
        raise JellyfinAdapterError(
            "More than one Jellyfin item matches the mapped media path; resolution is ambiguous."
        )
    return matches[0] if matches else None


def _select_media_source_id(
    item: Mapping[str, Any],
    expected_external_path: str,
) -> str:
    raw_sources = item.get("MediaSources")
    sources = raw_sources if isinstance(raw_sources, Sequence) and not isinstance(raw_sources, (str, bytes)) else ()
    path_matches: list[str] = []
    all_ids: list[str] = []
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        source_id = str(source.get("Id") or "").strip()
        if not source_id:
            continue
        all_ids.append(source_id)
        if external_paths_equal(str(source.get("Path") or ""), expected_external_path):
            path_matches.append(source_id)
    unique_path_matches = tuple(dict.fromkeys(path_matches))
    if len(unique_path_matches) > 1:
        raise JellyfinAdapterError(
            "More than one Jellyfin media source matches the mapped media path; resolution is ambiguous."
        )
    if unique_path_matches:
        return unique_path_matches[0]
    unique_ids = tuple(dict.fromkeys(all_ids))
    return unique_ids[0] if len(unique_ids) == 1 else ""


def resolve_media_ref(
    items: Sequence[Mapping[str, Any]],
    *,
    expected_external_path: str,
) -> ExternalMediaRef | None:
    """Resolve a bounded Jellyfin API result set by exact mapped media path only."""
    item = _select_item_by_path(items, expected_external_path)
    if item is None:
        return None
    item_id = str(item.get("Id") or "").strip()
    if not item_id:
        raise JellyfinAdapterError("The matching Jellyfin item has no stable item id.")
    provider_ids_raw = item.get("ProviderIds")
    provider_ids = {
        str(key): str(value)
        for key, value in provider_ids_raw.items()
        if str(key).strip() and str(value).strip()
    } if isinstance(provider_ids_raw, Mapping) else {}
    media_source_id = _select_media_source_id(item, expected_external_path)
    etag = str(item.get("Etag") or "").strip()
    return ExternalMediaRef(
        source_key="jellyfin",
        item_id=item_id,
        path=str(item.get("Path") or ""),
        media_source_id=media_source_id,
        provider_ids=provider_ids,
        source_signature=(f"jellyfin-item:{item_id}:{etag}" if etag else f"jellyfin-item:{item_id}"),
    )

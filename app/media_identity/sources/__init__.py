"""Read-only external media-system adapters for Episode Identity."""

from .jellyfin import (
    JellyfinAdapterError,
    JellyfinTrickplayAsset,
    JellyfinTrickplaySource,
    JellyfinTrickplayVariant,
    crop_trickplay_frame,
    enumerate_preview_frames,
    external_paths_equal,
    fetch_episode_candidates,
    fetch_item,
    fetch_trickplay_tile,
    parse_trickplay_variants,
    read_trickplay_preview,
    resolve_media_ref,
    select_trickplay_variant,
    trickplay_tile_url,
)

__all__ = [
    "JellyfinAdapterError",
    "JellyfinTrickplayAsset",
    "JellyfinTrickplaySource",
    "JellyfinTrickplayVariant",
    "crop_trickplay_frame",
    "enumerate_preview_frames",
    "external_paths_equal",
    "fetch_episode_candidates",
    "fetch_item",
    "fetch_trickplay_tile",
    "parse_trickplay_variants",
    "read_trickplay_preview",
    "resolve_media_ref",
    "select_trickplay_variant",
    "trickplay_tile_url",
]

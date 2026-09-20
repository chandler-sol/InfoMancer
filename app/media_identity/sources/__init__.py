"""Read-only external media-system adapters for Episode Identity."""

from .jellyfin import (
    JellyfinAdapterError,
    JellyfinTrickplayVariant,
    enumerate_preview_frames,
    external_paths_equal,
    parse_trickplay_variants,
    resolve_media_ref,
    select_trickplay_variant,
)

__all__ = [
    "JellyfinAdapterError",
    "JellyfinTrickplayVariant",
    "enumerate_preview_frames",
    "external_paths_equal",
    "parse_trickplay_variants",
    "resolve_media_ref",
    "select_trickplay_variant",
]

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
from typing import Any, Mapping, Sequence
import urllib.error
import urllib.parse
import urllib.request
import uuid

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
    source_read_plan,
)


_MAX_THUMBNAILS = 50_000
_MAX_DIMENSION = 16_384
_MAX_TILE_AXIS = 1_024
_MAX_INTERVAL_MS = 86_400_000
_MAX_TILE_SHEET_DIMENSION = 32_768
_MAX_TILE_SHEET_PIXELS = 64_000_000
_MAX_TILE_JPEG_BYTES = 32 * 1024 * 1024
_MAX_JSON_BYTES = 8 * 1024 * 1024
_MAX_EPISODE_CANDIDATES = 4096
_JELLYFIN_PAGE_SIZE = 256


class JellyfinAdapterError(ExternalAnalysisError):
    """Base Jellyfin adapter error retained for adapter-specific callers."""


class JellyfinSourceFailure(ExternalSourceFailure, JellyfinAdapterError):
    """Jellyfin could not be queried or returned untrustworthy source metadata."""


class JellyfinPreviewUnavailable(ExternalPreviewUnavailable, JellyfinAdapterError):
    """Optional Jellyfin Trickplay evidence is absent, stale, or unusable."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class JellyfinTrickplayAsset:
    media_source_id: str
    width: int
    height: int
    tile_width: int
    tile_height: int
    tile_index: int
    row: int
    column: int


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
        sheet_width = numeric["width"] * numeric["tile_width"]
        sheet_height = numeric["height"] * numeric["tile_height"]
        if (
            sheet_width > _MAX_TILE_SHEET_DIMENSION
            or sheet_height > _MAX_TILE_SHEET_DIMENSION
            or sheet_width * sheet_height > _MAX_TILE_SHEET_PIXELS
        ):
            raise JellyfinAdapterError(
                "Trickplay tile sheet dimensions are outside safe decode bounds."
            )
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
            "height": variant.height,
            "tile_width": variant.tile_width,
            "tile_height": variant.tile_height,
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


def _parse_trickplay_asset(frame: PreviewFrameRef) -> JellyfinTrickplayAsset:
    if str(frame.source_key or "").strip().casefold() != "jellyfin":
        raise JellyfinAdapterError("Preview frame does not belong to Jellyfin.")
    try:
        payload = json.loads(str(frame.asset_ref or ""))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise JellyfinAdapterError("Jellyfin preview asset reference is invalid.") from exc
    if not isinstance(payload, Mapping) or payload.get("kind") != "jellyfin_trickplay":
        raise JellyfinAdapterError("Jellyfin preview asset reference has an unknown kind.")
    try:
        asset = JellyfinTrickplayAsset(
            media_source_id=str(payload.get("media_source_id") or "").strip(),
            width=int(payload["width"]),
            height=int(payload["height"]),
            tile_width=int(payload["tile_width"]),
            tile_height=int(payload["tile_height"]),
            tile_index=int(payload["tile_index"]),
            row=int(payload["row"]),
            column=int(payload["column"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise JellyfinAdapterError("Jellyfin preview asset reference is incomplete.") from exc
    if not asset.media_source_id:
        raise JellyfinAdapterError("Jellyfin preview asset reference has no media source id.")
    if (
        asset.width <= 0
        or asset.height <= 0
        or asset.tile_width <= 0
        or asset.tile_height <= 0
        or asset.tile_index < 0
        or asset.row < 0
        or asset.column < 0
        or asset.row >= asset.tile_height
        or asset.column >= asset.tile_width
    ):
        raise JellyfinAdapterError("Jellyfin preview asset reference is outside tile bounds.")
    if frame.width != asset.width or frame.height != asset.height:
        raise JellyfinAdapterError("Jellyfin preview dimensions do not match the asset reference.")
    sheet_width = asset.width * asset.tile_width
    sheet_height = asset.height * asset.tile_height
    if (
        sheet_width > _MAX_TILE_SHEET_DIMENSION
        or sheet_height > _MAX_TILE_SHEET_DIMENSION
        or sheet_width * sheet_height > _MAX_TILE_SHEET_PIXELS
    ):
        raise JellyfinAdapterError("Jellyfin preview tile sheet is outside safe decode bounds.")
    return asset


def _normalize_jellyfin_server_url(value: str) -> str:
    raw = str(value or "")
    if not raw:
        raise JellyfinAdapterError("Jellyfin server URL is required.")
    if any(
        character.isspace()
        or ord(character) < 32
        or ord(character) == 127
        or ord(character) > 127
        for character in raw
    ):
        raise JellyfinAdapterError(
            "Jellyfin server URL cannot contain whitespace, control, or non-ASCII characters."
        )
    try:
        parsed = urllib.parse.urlsplit(raw)
        scheme = parsed.scheme.casefold()
        hostname = parsed.hostname
        username = parsed.username
        password = parsed.password
        _port = parsed.port
    except ValueError as exc:
        raise JellyfinAdapterError("Jellyfin server URL is invalid.") from exc
    if scheme not in {"http", "https"} or not hostname:
        raise JellyfinAdapterError("Jellyfin server URL must be a complete HTTP or HTTPS URL.")
    if username or password or parsed.query or parsed.fragment:
        raise JellyfinAdapterError(
            "Jellyfin server URL cannot contain credentials, a query string, or a fragment."
        )
    return urllib.parse.urlunsplit(
        (scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
    )


def _credential_transport_url(
    server_url: str,
    *,
    allow_insecure_http: bool = False,
) -> str:
    base = _normalize_jellyfin_server_url(server_url)
    if (
        urllib.parse.urlsplit(base).scheme.casefold() != "https"
        and not allow_insecure_http
    ):
        raise JellyfinAdapterError(
            "Jellyfin credentials will not be sent over plain HTTP. "
            "Use HTTPS or explicitly allow insecure HTTP for this integration."
        )
    return base


def _jellyfin_guid(value: str, label: str) -> str:
    raw = str(value or "").strip()
    try:
        return uuid.UUID(raw).hex
    except (ValueError, AttributeError) as exc:
        raise JellyfinAdapterError(f"{label} is not a valid Jellyfin GUID.") from exc


def trickplay_tile_url(server_url: str, frame: PreviewFrameRef) -> str:
    asset = _parse_trickplay_asset(frame)
    item_id = _jellyfin_guid(frame.item_id, "Jellyfin item id")
    media_source_id = _jellyfin_guid(
        asset.media_source_id, "Jellyfin media source id"
    )
    base = _normalize_jellyfin_server_url(server_url)
    path = (
        f"{base}/Videos/{urllib.parse.quote(item_id, safe='')}"
        f"/Trickplay/{asset.width}/{asset.tile_index}.jpg"
    )
    return path + "?" + urllib.parse.urlencode({"mediaSourceId": media_source_id})


def fetch_trickplay_tile(
    server_url: str,
    token: str,
    frame: PreviewFrameRef,
    *,
    timeout: float = 5.0,
    max_bytes: int = _MAX_TILE_JPEG_BYTES,
    allow_insecure_http: bool = False,
) -> bytes:
    credential = str(token or "").strip()
    if not credential:
        raise JellyfinAdapterError("A Jellyfin access token is required for Trickplay.")
    configured_limit = int(max_bytes)
    if configured_limit <= 0 or configured_limit > _MAX_TILE_JPEG_BYTES:
        raise JellyfinAdapterError("Jellyfin Trickplay response limit is invalid.")
    limit, budget_limited = source_read_plan(configured_limit)
    secure_base = _credential_transport_url(
        server_url,
        allow_insecure_http=allow_insecure_http,
    )
    url = trickplay_tile_url(secure_base, frame)
    visual_budget = current_visual_budget()
    cache_key = f"jellyfin-trickplay-tile:{url}"
    if visual_budget is not None:
        cached_tile = visual_budget.cached_source_asset(cache_key)
        if cached_tile is not None:
            return cached_tile
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "image/jpeg",
            "X-Emby-Token": credential,
        },
        method="GET",
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
    )
    try:
        with opener.open(
            request,
            timeout=max(1.0, min(float(timeout), 15.0)),
        ) as response:
            if response.status != 200:
                raise JellyfinSourceFailure(
                    f"Jellyfin returned HTTP {response.status} for the Trickplay tile."
                )
            headers = response.headers
            if hasattr(headers, "get_content_type"):
                content_type = headers.get_content_type()
            else:
                content_type = str(headers.get("Content-Type") or "").split(";", 1)[0].strip().casefold()
            if content_type not in {"image/jpeg", "image/jpg"}:
                raise JellyfinPreviewUnavailable(
                    "Jellyfin Trickplay response was not a JPEG image."
                )
            raw_length = headers.get("Content-Length")
            if raw_length not in {None, ""}:
                try:
                    content_length = int(raw_length)
                except (TypeError, ValueError) as exc:
                    raise JellyfinSourceFailure(
                        "Jellyfin Trickplay response had an invalid Content-Length."
                    ) from exc
                if content_length < 0 or content_length > limit:
                    raise JellyfinPreviewUnavailable(
                        "Jellyfin Trickplay tile exceeded the safe response-size limit."
                    )
            payload = response.read(limit if budget_limited else limit + 1)
            account_source_bytes(len(payload))
    except urllib.error.HTTPError as exc:
        if exc.code in {301, 302, 303, 307, 308}:
            detail = "Jellyfin redirected the Trickplay request; save the final local server URL instead."
        elif exc.code in {401, 403}:
            detail = "Jellyfin rejected the access token while reading Trickplay."
        elif exc.code == 404:
            detail = "Jellyfin did not have the requested Trickplay tile."
        else:
            detail = f"Jellyfin returned HTTP {exc.code} for the Trickplay tile."
        if exc.code == 404:
            raise JellyfinPreviewUnavailable(detail) from exc
        raise JellyfinSourceFailure(detail) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise JellyfinSourceFailure(
            f"InfoMancer could not read the Jellyfin Trickplay tile: {reason}"
        ) from exc

    if len(payload) > limit:
        raise JellyfinPreviewUnavailable(
            "Jellyfin Trickplay tile exceeded the safe response-size limit."
        )
    if not payload.startswith(b"\xff\xd8"):
        raise JellyfinPreviewUnavailable("Jellyfin Trickplay response was not a JPEG image.")
    if visual_budget is not None:
        visual_budget.cache_source_asset(cache_key, payload)
    return payload


def crop_trickplay_frame(tile_jpeg: bytes, frame: PreviewFrameRef) -> bytes:
    asset = _parse_trickplay_asset(frame)
    expected_width = asset.width * asset.tile_width
    expected_height = asset.height * asset.tile_height
    try:
        with Image.open(BytesIO(tile_jpeg)) as image:
            if image.format != "JPEG":
                raise JellyfinPreviewUnavailable(
                    "Jellyfin Trickplay tile did not decode as JPEG."
                )
            if image.size != (expected_width, expected_height):
                raise JellyfinPreviewUnavailable(
                    "Jellyfin Trickplay tile dimensions do not match its manifest."
                )
            if image.width * image.height > _MAX_TILE_SHEET_PIXELS:
                raise JellyfinPreviewUnavailable(
                    "Jellyfin Trickplay tile exceeds the safe decode-pixel limit."
                )
            image.load()
            left = asset.column * asset.width
            top = asset.row * asset.height
            right = left + asset.width
            bottom = top + asset.height
            if right > image.width or bottom > image.height:
                raise JellyfinPreviewUnavailable(
                    "Jellyfin Trickplay cell lies outside the decoded tile."
                )
            cropped = image.crop((left, top, right, bottom)).convert("RGB")
            output = BytesIO()
            cropped.save(
                output,
                format="JPEG",
                quality=95,
                subsampling=0,
            )
            return output.getvalue()
    except JellyfinAdapterError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
        raise JellyfinPreviewUnavailable(
            "Jellyfin Trickplay tile could not be decoded safely."
        ) from exc


def read_trickplay_preview(
    server_url: str,
    token: str,
    frame: PreviewFrameRef,
    *,
    timeout: float = 5.0,
    allow_insecure_http: bool = False,
) -> bytes:
    return crop_trickplay_frame(
        fetch_trickplay_tile(
            server_url,
            token,
            frame,
            timeout=timeout,
            allow_insecure_http=allow_insecure_http,
        ),
        frame,
    )


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


def _media_source_entries(
    item: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    raw_sources = item.get("MediaSources")
    if raw_sources is None:
        return ()
    if not isinstance(raw_sources, Sequence) or isinstance(
        raw_sources, (str, bytes)
    ):
        raise JellyfinSourceFailure(
            "Jellyfin returned an invalid media source list."
        )

    sources: list[Mapping[str, Any]] = []
    for source in raw_sources:
        if not isinstance(source, Mapping):
            raise JellyfinSourceFailure(
                "Jellyfin returned a malformed media source entry."
            )
        source_id = str(source.get("Id") or "").strip()
        if not source_id:
            raise JellyfinSourceFailure(
                "Jellyfin returned a media source without a stable id."
            )
        sources.append(source)
    return tuple(sources)


def _select_item_by_path(
    items: Sequence[Mapping[str, Any]],
    expected_external_path: str,
) -> Mapping[str, Any] | None:
    matches: list[Mapping[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise JellyfinSourceFailure(
                "Jellyfin returned a malformed episode candidate."
            )
        paths = [str(item.get("Path") or "").strip()]
        paths.extend(
            str(source.get("Path") or "").strip()
            for source in _media_source_entries(item)
        )
        if any(
            path and external_paths_equal(path, expected_external_path)
            for path in paths
        ):
            matches.append(item)

    if len(matches) > 1:
        raise JellyfinSourceFailure(
            "More than one Jellyfin item matches the mapped media path; resolution is ambiguous."
        )
    return matches[0] if matches else None


def _select_media_source_id(
    item: Mapping[str, Any],
    expected_external_path: str,
) -> str:
    sources = _media_source_entries(item)
    path_matches: list[str] = []
    all_ids: list[str] = []
    has_declared_path = False
    for source in sources:
        source_path = str(source.get("Path") or "").strip()
        if source_path:
            has_declared_path = True
        source_id = str(source.get("Id") or "").strip()
        all_ids.append(source_id)
        if external_paths_equal(source_path, expected_external_path):
            path_matches.append(source_id)
    unique_path_matches = tuple(dict.fromkeys(path_matches))
    if len(unique_path_matches) > 1:
        raise JellyfinSourceFailure(
            "More than one Jellyfin media source matches the mapped media path; resolution is ambiguous."
        )
    if unique_path_matches:
        return unique_path_matches[0]
    unique_ids = tuple(dict.fromkeys(all_ids))
    return unique_ids[0] if len(unique_ids) == 1 and not has_declared_path else ""


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
        raise JellyfinSourceFailure("The matching Jellyfin item has no stable item id.")
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
        path=expected_external_path,
        media_source_id=media_source_id,
        provider_ids=provider_ids,
        source_signature=(f"jellyfin-item:{item_id}:{etag}" if etag else f"jellyfin-item:{item_id}"),
    )


def _read_jellyfin_json(
    server_url: str,
    token: str,
    path: str,
    *,
    query: Mapping[str, Any] | None = None,
    timeout: float = 5.0,
    max_bytes: int = _MAX_JSON_BYTES,
    allow_insecure_http: bool = False,
) -> Mapping[str, Any]:
    credential = str(token or "").strip()
    if not credential:
        raise JellyfinAdapterError("A Jellyfin access token is required.")
    configured_limit = int(max_bytes)
    if configured_limit <= 0 or configured_limit > _MAX_JSON_BYTES:
        raise JellyfinAdapterError("Jellyfin JSON response limit is invalid.")
    limit, budget_limited = source_read_plan(configured_limit)

    base = _credential_transport_url(
        server_url,
        allow_insecure_http=allow_insecure_http,
    )
    route = "/" + str(path or "").lstrip("/")
    url = base + route
    if query:
        url += "?" + urllib.parse.urlencode(
            [(str(key), str(value)) for key, value in query.items()]
        )

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "X-Emby-Token": credential,
        },
        method="GET",
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
    )
    try:
        with opener.open(
            request,
            timeout=max(1.0, min(float(timeout), 15.0)),
        ) as response:
            if response.status != 200:
                raise JellyfinSourceFailure(
                    f"Jellyfin returned HTTP {response.status} while reading library metadata."
                )
            raw_length = response.headers.get("Content-Length")
            if raw_length not in {None, ""}:
                try:
                    content_length = int(raw_length)
                except (TypeError, ValueError) as exc:
                    raise JellyfinSourceFailure(
                        "Jellyfin metadata response had an invalid Content-Length."
                    ) from exc
                if content_length < 0 or content_length > limit:
                    raise JellyfinSourceFailure(
                        "Jellyfin metadata response exceeded the safe response-size limit."
                    )
            payload = response.read(limit if budget_limited else limit + 1)
            account_source_bytes(len(payload))
    except urllib.error.HTTPError as exc:
        if exc.code in {301, 302, 303, 307, 308}:
            detail = (
                "Jellyfin redirected the metadata request; save the final local server URL instead."
            )
        elif exc.code in {401, 403}:
            detail = "Jellyfin rejected the access token while reading library metadata."
        elif exc.code == 404:
            detail = "Jellyfin did not have the requested library item."
        else:
            detail = f"Jellyfin returned HTTP {exc.code} while reading library metadata."
        raise JellyfinSourceFailure(detail) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise JellyfinSourceFailure(
            f"InfoMancer could not read Jellyfin library metadata: {reason}"
        ) from exc

    if len(payload) > limit:
        raise JellyfinSourceFailure(
            "Jellyfin metadata response exceeded the safe response-size limit."
        )
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JellyfinSourceFailure(
            "Jellyfin returned malformed JSON library metadata."
        ) from exc
    if not isinstance(parsed, Mapping):
        raise JellyfinSourceFailure(
            "Jellyfin returned an unexpected library metadata payload."
        )
    return parsed


def fetch_episode_candidates(
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
        raise JellyfinAdapterError(
            "Jellyfin episode coordinates cannot be negative."
        )

    items: list[Mapping[str, Any]] = []
    expected_total: int | None = None
    start_index = 0
    while True:
        payload = _read_jellyfin_json(
            server_url,
            token,
            "/Items",
            query={
                "recursive": "true",
                "includeItemTypes": "Episode",
                "parentIndexNumber": season_number,
                "indexNumber": episode_number,
                "fields": "Path,ProviderIds,MediaSources",
                "enableImages": "false",
                "enableUserData": "false",
                "startIndex": start_index,
                "limit": _JELLYFIN_PAGE_SIZE,
            },
            timeout=timeout,
            allow_insecure_http=allow_insecure_http,
        )
        raw_items = payload.get("Items")
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
            raise JellyfinSourceFailure(
                "Jellyfin episode search returned an invalid item list."
            )
        if any(not isinstance(item, Mapping) for item in raw_items):
            raise JellyfinSourceFailure(
                "Jellyfin episode search returned a malformed candidate entry."
            )
        if "TotalRecordCount" not in payload:
            raise JellyfinSourceFailure(
                "Jellyfin episode search did not report a complete result count."
            )
        try:
            total = int(payload["TotalRecordCount"])
            returned_start = int(payload.get("StartIndex", start_index))
        except (TypeError, ValueError) as exc:
            raise JellyfinSourceFailure(
                "Jellyfin episode search returned invalid pagination metadata."
            ) from exc
        if total < 0 or total > _MAX_EPISODE_CANDIDATES:
            raise JellyfinSourceFailure(
                "Jellyfin returned too many episode candidates to resolve safely."
            )
        if returned_start != start_index:
            raise JellyfinSourceFailure(
                "Jellyfin episode search returned an unexpected result offset."
            )
        if len(raw_items) > _JELLYFIN_PAGE_SIZE:
            raise JellyfinSourceFailure(
                "Jellyfin episode search returned more items than the requested page size."
            )
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise JellyfinSourceFailure(
                "Jellyfin episode search changed while candidates were being paged."
            )
        if len(items) + len(raw_items) > _MAX_EPISODE_CANDIDATES:
            raise JellyfinSourceFailure(
                "Jellyfin returned too many episode candidates to resolve safely."
            )
        items.extend(raw_items)

        if len(items) > expected_total:
            raise JellyfinSourceFailure(
                "Jellyfin episode search returned more candidates than declared."
            )
        if len(items) == expected_total:
            break
        if not raw_items:
            raise JellyfinSourceFailure(
                "Jellyfin episode search ended before all candidates were returned."
            )
        start_index += len(raw_items)

    return tuple(items)


def fetch_item(
    server_url: str,
    token: str,
    item_id: str,
    *,
    timeout: float = 5.0,
    allow_insecure_http: bool = False,
) -> Mapping[str, Any]:
    normalized_id = _jellyfin_guid(item_id, "Jellyfin item id")
    item = _read_jellyfin_json(
        server_url,
        token,
        f"/Items/{urllib.parse.quote(normalized_id, safe='')}",
        timeout=timeout,
        allow_insecure_http=allow_insecure_http,
    )
    returned_id = str(item.get("Id") or "").strip()
    if not returned_id:
        raise JellyfinSourceFailure(
            "Jellyfin item lookup returned no stable item id."
        )
    try:
        normalized_returned_id = _jellyfin_guid(
            returned_id,
            "Jellyfin returned item id",
        )
    except JellyfinAdapterError as exc:
        raise JellyfinSourceFailure(
            "Jellyfin item lookup returned an invalid item id."
        ) from exc
    if normalized_returned_id != normalized_id:
        raise JellyfinSourceFailure(
            "Jellyfin returned a different item than the one requested."
        )
    return item


class JellyfinTrickplaySource:
    """Configured read-only Jellyfin Trickplay source for Episode Identity."""

    source_key = "jellyfin"
    version = "0.9-pr-f"

    def __init__(
        self,
        server_url: str,
        token: str,
        mapper: ExternalPathMapper,
        *,
        enabled: bool = True,
        last_test_status: str = "",
        allow_insecure_http: bool = False,
        advertise_preview_frames: bool = False,
    ) -> None:
        self.server_url = (
            _normalize_jellyfin_server_url(server_url)
            if str(server_url or "").strip()
            else ""
        )
        self._token = str(token or "").strip()
        self.mapper = mapper
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
                    "Jellyfin uses plain HTTP. Use HTTPS or explicitly allow insecure "
                    "HTTP before InfoMancer sends the access token."
                ),
            )
        if not self.mapper.mappings_for(self.source_key):
            return ExternalSourceStatus(
                source_key=self.source_key,
                available=False,
                detail="Add at least one Jellyfin-to-InfoMancer path mapping.",
            )
        capabilities = (
            frozenset({ExternalCapability.PREVIEW_FRAMES})
            if self.advertise_preview_frames
            else frozenset()
        )
        detail = (
            "Jellyfin Trickplay preview frames are ready for Episode Identity."
            if self.advertise_preview_frames
            else (
                "Jellyfin Trickplay adapter is configured and validated. "
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
            raise JellyfinSourceFailure(
                f"Jellyfin path mapping could not resolve this media unambiguously: {exc}"
            ) from exc
        if translation is None:
            return None
        season = context.claimed_identity.season
        episode = context.claimed_identity.episode
        if season is None or episode is None:
            return None

        candidates = fetch_episode_candidates(
            self.server_url,
            self._token,
            season=season,
            episode=episode,
            allow_insecure_http=self.allow_insecure_http,
        )
        matched = _select_item_by_path(candidates, translation.external_path)
        if matched is None:
            return None
        item_id = str(matched.get("Id") or "").strip()
        if not item_id:
            raise JellyfinSourceFailure(
                "The matching Jellyfin episode has no stable item id."
            )

        detail = fetch_item(
            self.server_url,
            self._token,
            item_id,
            allow_insecure_http=self.allow_insecure_http,
        )
        resolved = resolve_media_ref(
            (detail,),
            expected_external_path=translation.external_path,
        )
        if (
            resolved is None
            or _jellyfin_guid(resolved.item_id, "Jellyfin item id")
            != _jellyfin_guid(item_id, "Jellyfin item id")
        ):
            raise JellyfinSourceFailure(
                "The Jellyfin episode changed while it was being resolved."
            )
        return resolved

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

        item = fetch_item(
            self.server_url,
            self._token,
            media.item_id,
            allow_insecure_http=self.allow_insecure_http,
        )
        current = resolve_media_ref(
            (item,),
            expected_external_path=media.path,
        )
        if current is None:
            return ()
        try:
            same_item = (
                _jellyfin_guid(current.item_id, "Jellyfin item id")
                == _jellyfin_guid(media.item_id, "Jellyfin item id")
            )
            same_media_source = (
                _jellyfin_guid(current.media_source_id, "Jellyfin media source id")
                == _jellyfin_guid(media.media_source_id, "Jellyfin media source id")
            )
        except JellyfinAdapterError:
            return ()
        if not same_item or not same_media_source:
            return ()

        variant = select_trickplay_variant(
            parse_trickplay_variants(item),
            media_source_id=current.media_source_id,
        )
        if variant is None:
            return ()
        return enumerate_preview_frames(
            item_id=current.item_id,
            item_etag=str(item.get("Etag") or "").strip(),
            variant=variant,
        )

    def read_preview(self, frame: PreviewFrameRef) -> bytes:
        if not self.status().available:
            raise JellyfinSourceFailure(
                "Jellyfin Trickplay preview reuse is not currently available."
            )
        if str(frame.source_key or "").strip().casefold() != self.source_key:
            raise JellyfinAdapterError("Preview frame does not belong to Jellyfin.")

        asset = _parse_trickplay_asset(frame)
        item = fetch_item(
            self.server_url,
            self._token,
            frame.item_id,
            allow_insecure_http=self.allow_insecure_http,
        )

        source_ids = []
        for source in _media_source_entries(item):
            try:
                source_ids.append(
                    _jellyfin_guid(
                        str(source.get("Id") or ""),
                        "Jellyfin media source id",
                    )
                )
            except JellyfinAdapterError as exc:
                raise JellyfinSourceFailure(
                    "Jellyfin returned an invalid media source while revalidating Trickplay."
                ) from exc
        expected_source_id = _jellyfin_guid(
            asset.media_source_id,
            "Jellyfin media source id",
        )
        if source_ids.count(expected_source_id) != 1:
            raise JellyfinPreviewUnavailable(
                "Jellyfin media source changed after preview enumeration."
            )

        variant = select_trickplay_variant(
            parse_trickplay_variants(item),
            media_source_id=asset.media_source_id,
        )
        if variant is None:
            raise JellyfinPreviewUnavailable(
                "Jellyfin Trickplay manifest is no longer available for this media source."
            )
        if (
            asset.width != variant.width
            or asset.height != variant.height
            or asset.tile_width != variant.tile_width
            or asset.tile_height != variant.tile_height
        ):
            raise JellyfinPreviewUnavailable(
                "Jellyfin Trickplay manifest changed after preview enumeration."
            )

        thumbnail_index = (
            asset.tile_index * variant.thumbnails_per_tile
            + asset.row * variant.tile_width
            + asset.column
        )
        if asset.tile_index >= variant.tile_count or thumbnail_index >= variant.thumbnail_count:
            raise JellyfinPreviewUnavailable(
                "Jellyfin Trickplay frame no longer exists in the current manifest."
            )
        expected_timestamp = thumbnail_index * variant.interval_ms
        if expected_timestamp != int(frame.timestamp_ms):
            raise JellyfinPreviewUnavailable(
                "Jellyfin Trickplay timing changed after preview enumeration."
            )

        current_signature = trickplay_source_signature(
            frame.item_id,
            str(item.get("Etag") or "").strip(),
            variant,
        )
        if current_signature != str(frame.source_signature or ""):
            raise JellyfinPreviewUnavailable(
                "Jellyfin item or Trickplay manifest changed after preview enumeration."
            )

        return read_trickplay_preview(
            self.server_url,
            self._token,
            frame,
            allow_insecure_http=self.allow_insecure_http,
        )

    def subtitles(self, media: ExternalMediaRef):
        return ()

    def read_subtitle(self, subtitle):
        raise JellyfinAdapterError(
            "Jellyfin subtitle ingestion is not implemented by the Trickplay adapter."
        )

    def media_metadata(self, media: ExternalMediaRef):
        return {}

    def fingerprints(self, media: ExternalMediaRef):
        return ()

    def known_identity(self, media: ExternalMediaRef):
        return None

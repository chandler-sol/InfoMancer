from __future__ import annotations

from typing import Protocol
from urllib.parse import quote


class TVDBOrderTransport(Protocol):
    """Minimal authenticated TVDB transport used by the identity provider adapter."""

    def _get(
        self, path: str, params: dict | None = None, *,
        allow_not_found: bool = False, _retry_auth: bool = True,
    ) -> dict:
        ...


def _season_type_records(record: dict) -> list[dict]:
    """Collect season-type descriptors from both TVDB v4 response shapes."""
    records: list[dict] = []

    # Current TVDB schema material still advertises a top-level seasonTypes list.
    for raw in record.get("seasonTypes") or record.get("season_types") or []:
        if isinstance(raw, dict):
            records.append(raw)

    # Real series payloads and the official client examples also expose each
    # season's order descriptor under seasons[*].type. A series may repeat the
    # same type for many numbered seasons, so callers must deduplicate it.
    for season in record.get("seasons") or []:
        if not isinstance(season, dict):
            continue
        raw = season.get("type")
        if isinstance(raw, dict):
            records.append(raw)
        elif isinstance(raw, str) and raw.strip():
            records.append({"type": raw})

    return records


def episode_orders(client: TVDBOrderTransport, series_id: int) -> dict:
    """Return deterministic TVDB order namespaces advertised for one series."""
    record = client._get(f"/series/{int(series_id)}/extended").get("data") or {}
    default_type_id = record.get("defaultSeasonType") or record.get("default_season_type")

    discovered: dict[str, dict] = {}
    for raw in _season_type_records(record):
        namespace = str(raw.get("type") or "").strip().casefold()
        if not namespace or namespace == "default":
            continue
        raw_id = raw.get("id")
        name = str(raw.get("alternateName") or raw.get("name") or namespace).strip()
        is_default = bool(
            default_type_id is not None
            and raw_id is not None
            and str(raw_id) == str(default_type_id)
        )
        existing = discovered.get(namespace)
        if existing is None:
            discovered[namespace] = {
                "namespace": namespace,
                "name": name,
                "default": is_default,
                "provider_type_id": raw_id,
            }
            continue

        # Merge repeated descriptors deterministically. Nested seasons commonly
        # repeat one type across multiple season numbers.
        existing["default"] = bool(existing["default"] or is_default)
        if existing.get("provider_type_id") is None and raw_id is not None:
            existing["provider_type_id"] = raw_id
        if existing.get("name") in {"", namespace} and name not in {"", namespace}:
            existing["name"] = name

    # TVDB's documented `default` endpoint remains a useful alias even when the
    # concrete default season type is also advertised (normally official/aired).
    orders = [{
        "namespace": "default",
        "name": "Default",
        "default": True,
        "provider_type_id": default_type_id,
    }]
    orders.extend(sorted(
        discovered.values(),
        key=lambda item: (not item["default"], item["namespace"]),
    ))
    return {
        "provider_updated_at": str(record.get("lastUpdated") or record.get("last_updated") or "").strip(),
        "orders": orders,
    }


def episodes_for_order(
    client: TVDBOrderTransport, series_id: int, order_namespace: str,
    language: str = "eng",
) -> list[dict]:
    """Fetch every page for one TVDB order; an unsupported order is empty evidence."""
    namespace = str(order_namespace or "").strip().casefold()
    if not namespace:
        raise ValueError("TVDB episode order namespace is required")
    language = str(language or "eng").strip().casefold() or "eng"
    encoded_namespace = quote(namespace, safe="")
    encoded_language = quote(language, safe="")

    episodes: list[dict] = []
    page = 0
    while True:
        payload = client._get(
            f"/series/{int(series_id)}/episodes/{encoded_namespace}/{encoded_language}",
            {"page": page},
            allow_not_found=True,
        )
        if not payload:
            return []
        data = payload.get("data") or {}
        if isinstance(data, dict):
            episodes.extend(data.get("episodes") or [])
        elif isinstance(data, list):
            episodes.extend(data)
        links = payload.get("links") or {}
        if not links.get("next"):
            break
        page += 1
        if page > 10_000:
            raise RuntimeError("TVDB episode pagination exceeded its safety bound")
    return episodes

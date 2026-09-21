from __future__ import annotations

from typing import Protocol
from urllib.parse import quote

TVDB_ORDER_RESPONSE_MAX_BYTES = 2 * 1024 * 1024
TVDB_ORDER_MAX_PAGES = 512
TVDB_ORDER_MAX_EPISODES = 10_000


class TVDBOrderError(RuntimeError):
    """Raised when an order response cannot be trusted as complete and bounded."""


class TVDBOrderTransport(Protocol):
    """Minimal authenticated TVDB transport used by the identity provider adapter."""

    def _get(
        self, path: str, params: dict | None = None, *,
        allow_not_found: bool = False, _retry_auth: bool = True,
    ) -> dict:
        ...


def _bounded_get(
    client: TVDBOrderTransport,
    path: str,
    params: dict | None = None,
    *,
    allow_not_found: bool = False,
    max_response_bytes: int = TVDB_ORDER_RESPONSE_MAX_BYTES,
) -> dict:
    bounded = getattr(client, "_get_bounded", None)
    if callable(bounded):
        return bounded(
            path,
            params,
            allow_not_found=allow_not_found,
            max_bytes=int(max_response_bytes),
        )
    return client._get(
        path,
        params,
        allow_not_found=allow_not_found,
    )



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


def episode_orders(
    client: TVDBOrderTransport,
    series_id: int,
    *,
    max_response_bytes: int = TVDB_ORDER_RESPONSE_MAX_BYTES,
) -> dict:
    """Return deterministic TVDB order namespaces advertised for one series."""
    payload = _bounded_get(
        client,
        f"/series/{int(series_id)}/extended",
        max_response_bytes=max_response_bytes,
    )
    record = payload.get("data") or {}
    if not isinstance(record, dict):
        raise TVDBOrderError("TVDB returned an invalid series-order payload.")
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
    language: str = "eng", *,
    max_response_bytes: int = TVDB_ORDER_RESPONSE_MAX_BYTES,
    max_pages: int = TVDB_ORDER_MAX_PAGES,
    max_episodes: int = TVDB_ORDER_MAX_EPISODES,
    allow_missing_first_page: bool = True,
) -> list[dict]:
    """Fetch one complete bounded TVDB order snapshot.

    Callers doing speculative namespace discovery may treat a missing first page
    as unsupported. Provider-cache refreshes pass allow_missing_first_page=False
    because every requested namespace was just advertised and must be complete.
    Once TVDB advertises a continuation, a missing or malformed later page is
    always a refresh failure.
    """
    namespace = str(order_namespace or "").strip().casefold()
    if not namespace:
        raise ValueError("TVDB episode order namespace is required")
    language = str(language or "eng").strip().casefold() or "eng"
    if int(max_pages) <= 0 or int(max_episodes) <= 0:
        raise ValueError("TVDB episode order limits must be positive")
    encoded_namespace = quote(namespace, safe="")
    encoded_language = quote(language, safe="")

    episodes: list[dict] = []
    page = 0
    continuation_expected = False
    while True:
        if page >= int(max_pages):
            raise TVDBOrderError(
                "TVDB episode pagination exceeded the Episode Identity page limit."
            )
        payload = _bounded_get(
            client,
            f"/series/{int(series_id)}/episodes/{encoded_namespace}/{encoded_language}",
            {"page": page},
            allow_not_found=True,
            max_response_bytes=max_response_bytes,
        )
        if not payload:
            if continuation_expected:
                raise TVDBOrderError(
                    "TVDB omitted an advertised episode-order continuation page."
                )
            if page == 0 and not allow_missing_first_page:
                raise TVDBOrderError(
                    "TVDB omitted the first page for an advertised episode-order namespace."
                )
            return []

        data = payload.get("data")
        if isinstance(data, dict):
            page_rows = data.get("episodes") or []
        elif isinstance(data, list):
            page_rows = data
        else:
            raise TVDBOrderError("TVDB returned an invalid episode-order page.")
        if not isinstance(page_rows, list) or any(
            not isinstance(item, dict) for item in page_rows
        ):
            raise TVDBOrderError("TVDB returned malformed episode-order rows.")
        if continuation_expected and not page_rows:
            raise TVDBOrderError(
                "TVDB returned an empty advertised episode-order continuation page."
            )
        if len(episodes) + len(page_rows) > int(max_episodes):
            raise TVDBOrderError(
                "TVDB episode order exceeded the Episode Identity record limit."
            )
        episodes.extend(page_rows)

        links = payload.get("links") or {}
        if not isinstance(links, dict):
            raise TVDBOrderError("TVDB returned invalid episode pagination metadata.")
        continuation_expected = bool(links.get("next"))
        if not page_rows and continuation_expected:
            raise TVDBOrderError(
                "TVDB advertised episode-order continuation from an empty page."
            )
        if not continuation_expected:
            break
        page += 1
    return episodes

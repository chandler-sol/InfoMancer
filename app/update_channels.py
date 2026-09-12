from __future__ import annotations

import re
from typing import Iterable


UPDATE_CHANNELS = ("standard", "beta", "dev")
CHANNEL_LABELS = {
    "standard": "Standard",
    "beta": "Beta",
    "dev": "Dev",
}
CHANNEL_DESCRIPTIONS = {
    "standard": "Production releases intended for normal installations.",
    "beta": "Qualified preview releases plus everything in Standard.",
    "dev": "Latest qualified development builds plus Beta and Standard releases.",
}
CHANNEL_RANK = {"standard": 0, "beta": 1, "dev": 2}
SEMVER_PATTERN = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)
DEV_MARKERS = {"dev", "alpha", "nightly", "canary", "preview"}
BETA_MARKERS = {"beta", "rc"}


def normalize_channel(value: str) -> str:
    channel = (value or "").strip().casefold()
    if channel not in UPDATE_CHANNELS:
        raise ValueError("Choose Standard, Beta, or Dev as the update channel.")
    return channel


def channel_label(value: str) -> str:
    return CHANNEL_LABELS[normalize_channel(value)]


def channel_transition(current: str, requested: str) -> str:
    old = normalize_channel(current)
    new = normalize_channel(requested)
    if old == new:
        return "same"
    return "less_stable" if CHANNEL_RANK[new] > CHANNEL_RANK[old] else "more_stable"


def _prerelease_tokens(value: str) -> tuple[str, ...]:
    match = SEMVER_PATTERN.fullmatch(value.strip())
    if not match or not match.group("prerelease"):
        return ()
    return tuple(part.casefold() for part in match.group("prerelease").split("."))


def release_channel(tag: str, prerelease: bool = False) -> str:
    tokens = _prerelease_tokens(tag)
    words = {token for token in tokens if not token.isdigit()}
    if words.intersection(DEV_MARKERS):
        return "dev"
    if words.intersection(BETA_MARKERS):
        return "beta"
    if tokens or prerelease:
        # An explicitly marked GitHub prerelease with an unfamiliar suffix is
        # treated as Beta rather than accidentally leaking into Standard.
        return "beta"
    return "standard"


def _token_key(token: str) -> tuple[int, object]:
    if token.isdigit():
        return (1, int(token))
    return (0, token.casefold())


def version_key(value: str) -> tuple:
    match = SEMVER_PATTERN.fullmatch((value or "").strip())
    if not match:
        return (-1, -1, -1, -1, ())
    base = (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )
    prerelease = match.group("prerelease")
    if not prerelease:
        return base + (3, ())
    channel = release_channel(value, prerelease=True)
    stage = 1 if channel == "dev" else 2
    tokens = tuple(_token_key(part) for part in prerelease.split("."))
    return base + (stage, tokens)


def channel_allows(selected: str, candidate: str) -> bool:
    selected = normalize_channel(selected)
    candidate = normalize_channel(candidate)
    return CHANNEL_RANK[candidate] <= CHANNEL_RANK[selected]


def select_release(releases: Iterable[dict], selected_channel: str) -> dict | None:
    selected = normalize_channel(selected_channel)
    candidates: list[dict] = []
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        tag = str(release.get("tag_name") or "").strip()
        if not SEMVER_PATTERN.fullmatch(tag):
            continue
        candidate_channel = release_channel(tag, bool(release.get("prerelease")))
        if not channel_allows(selected, candidate_channel):
            continue
        item = dict(release)
        item["infomancer_channel"] = candidate_channel
        candidates.append(item)
    if not candidates:
        return None
    return max(candidates, key=lambda item: version_key(str(item.get("tag_name") or "")))


def update_state(installed_version: str, latest_version: str) -> str:
    installed = version_key(installed_version)
    latest = version_key(latest_version)
    if latest > installed:
        return "available"
    if latest < installed:
        return "waiting_for_channel"
    return "current"

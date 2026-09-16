from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse


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
SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
DEV_MARKERS = {"dev", "alpha", "nightly", "canary", "preview"}
BETA_MARKERS = {"beta", "rc"}
SCHEMA_DOWNGRADE_POLICIES = {"compatible", "read_only", "restore_required"}


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


def channel_preferences_path(database_path: Path) -> Path:
    return database_path.parent / "update-channel.json"


def read_update_channel(database_path: Path) -> str:
    path = channel_preferences_path(database_path)
    if not path.exists():
        return "standard"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return "standard"
        return normalize_channel(str(value.get("channel") or "standard"))
    except (OSError, json.JSONDecodeError, ValueError):
        # A damaged preference file must never make Settings or startup fail.
        # Standard is the conservative fallback and does not install anything.
        return "standard"


def write_update_channel(database_path: Path, channel: str) -> Path:
    selected = normalize_channel(channel)
    path = channel_preferences_path(database_path)
    temporary = path.with_suffix(".tmp")
    payload = {"format": "infomancer-update-channel", "version": 1, "channel": selected}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    return path


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


def validate_database_schema_contract(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Update channel manifest is missing its database schema contract.")
    current = value.get("current")
    minimum_reader = value.get("minimum_reader_schema")
    minimum_writer = value.get("minimum_writer_schema")
    policy = str(value.get("downgrade_policy") or "").strip()
    if not isinstance(current, int) or current < 1:
        raise ValueError("Update channel manifest contains an invalid current database schema.")
    if not isinstance(minimum_reader, int) or not 1 <= minimum_reader <= current:
        raise ValueError("Update channel manifest contains an invalid minimum reader schema.")
    if minimum_writer is not None and (
        not isinstance(minimum_writer, int)
        or not minimum_reader <= minimum_writer <= current
    ):
        raise ValueError("Update channel manifest contains an invalid minimum writer schema.")
    if policy not in SCHEMA_DOWNGRADE_POLICIES:
        raise ValueError("Update channel manifest contains an invalid schema downgrade policy.")
    if policy == "compatible" and minimum_writer is None:
        raise ValueError("Compatible schema contracts must identify a minimum writer schema.")
    if policy == "read_only" and minimum_writer is not None:
        raise ValueError("Read-only schema contracts must omit a minimum writer schema.")
    return {
        "current": current,
        "minimum_reader_schema": minimum_reader,
        "minimum_writer_schema": minimum_writer,
        "downgrade_policy": policy,
    }


def _validate_promotion(value: object, target_channel: str, version: str, build_id: str, commit_sha: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Update channel manifest contains invalid promotion provenance.")
    source_channel = normalize_channel(str(value.get("source_channel") or ""))
    if target_channel not in {"beta", "standard"} or CHANNEL_RANK[source_channel] <= CHANNEL_RANK[target_channel]:
        raise ValueError("Update channel promotion must move from a less stable channel to a more stable channel.")

    source_version = str(value.get("source_version") or "").strip().lstrip("v")
    if version_key(source_version)[0] < 0:
        raise ValueError("Update channel promotion contains an invalid source version.")
    if release_channel(source_version, bool(_prerelease_tokens(source_version))) != source_channel:
        raise ValueError("Update channel promotion source version does not match its source channel.")
    if release_channel(version, bool(_prerelease_tokens(version))) != target_channel:
        raise ValueError("Promoted version does not match the target update channel.")

    source_build_id = str(value.get("source_build_id") or "").strip()
    if source_build_id != build_id:
        raise ValueError("Promoted manifests must preserve the immutable source build id.")
    source_commit_sha = str(value.get("source_commit_sha") or "").strip().casefold()
    if source_commit_sha != commit_sha:
        raise ValueError("Promoted manifests must preserve the qualified source commit SHA.")
    promoted_at = str(value.get("promoted_at") or "").strip()
    if not promoted_at:
        raise ValueError("Update channel promotion is missing its promotion timestamp.")

    return {
        "source_channel": source_channel,
        "source_version": source_version,
        "source_build_id": source_build_id,
        "source_commit_sha": source_commit_sha,
        "promoted_at": promoted_at,
    }


def validate_channel_manifest(value: object, expected_channel: str) -> dict:
    channel = normalize_channel(expected_channel)
    if not isinstance(value, dict):
        raise ValueError("Update channel manifest must be a JSON object.")
    if value.get("schema_version") != 1:
        raise ValueError("Update channel manifest schema version is not supported.")
    manifest_channel = normalize_channel(str(value.get("channel") or ""))
    if manifest_channel != channel:
        raise ValueError("Update channel manifest does not match the selected channel.")

    version = str(value.get("version") or "").strip().lstrip("v")
    if version_key(version)[0] < 0:
        raise ValueError("Update channel manifest contains an invalid version.")
    build_id = str(value.get("build_id") or "").strip()
    if not build_id:
        raise ValueError("Update channel manifest is missing its immutable build id.")
    commit_sha = str(value.get("commit_sha") or "").strip().casefold()
    if not SHA_PATTERN.fullmatch(commit_sha):
        raise ValueError("Update channel manifest contains an invalid commit SHA.")
    qualified_at = str(value.get("qualified_at") or "").strip()
    if not qualified_at:
        raise ValueError("Update channel manifest is missing its qualification timestamp.")

    qualification = value.get("qualification")
    if not isinstance(qualification, dict) or qualification.get("status") != "passed":
        raise ValueError("Update channel manifest is not backed by a passed qualification.")
    if not str(qualification.get("workflow") or "").strip():
        raise ValueError("Update channel manifest is missing its qualification workflow.")
    run_id = qualification.get("run_id")
    if not isinstance(run_id, int) or run_id < 1:
        raise ValueError("Update channel manifest contains an invalid qualification run id.")
    gates = qualification.get("gates")
    if not isinstance(gates, list) or not gates or not all(
        isinstance(gate, str) and gate.strip() for gate in gates
    ):
        raise ValueError("Update channel manifest must record its passed qualification gates.")

    database_schema = validate_database_schema_contract(value.get("database_schema"))

    artifacts = value.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("Update channel manifest artifacts must be an object.")
    for platform, artifact in artifacts.items():
        if not isinstance(platform, str) or not platform.strip() or not isinstance(artifact, dict):
            raise ValueError("Update channel manifest contains an invalid platform artifact.")
        kind = str(artifact.get("kind") or "").strip()
        url = str(artifact.get("url") or "").strip()
        parsed_url = urlparse(url)
        digest = str(artifact.get("sha256") or "").strip()
        if (
            not kind
            or parsed_url.scheme.casefold() != "https"
            or not parsed_url.netloc
            or not SHA256_PATTERN.fullmatch(digest)
        ):
            raise ValueError("Update channel manifest contains invalid artifact metadata.")

    normalized = dict(value)
    normalized["channel"] = manifest_channel
    normalized["version"] = version
    normalized["commit_sha"] = commit_sha
    normalized["database_schema"] = database_schema
    if "promotion" in value:
        normalized["promotion"] = _validate_promotion(
            value.get("promotion"), manifest_channel, version, build_id, commit_sha,
        )
    return normalized


def update_state(installed_version: str, latest_version: str) -> str:
    installed = version_key(installed_version)
    latest = version_key(latest_version)
    if latest > installed:
        return "available"
    if latest < installed:
        return "waiting_for_channel"
    return "current"

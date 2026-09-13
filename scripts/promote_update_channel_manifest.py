#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
UPDATE_CHANNELS_PATH = ROOT / "app" / "update_channels.py"
SPEC = importlib.util.spec_from_file_location("infomancer_update_channels_promotion", UPDATE_CHANNELS_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load InfoMancer update-channel helpers.")
UPDATE_CHANNELS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UPDATE_CHANNELS)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_artifact_spec(value: str, server_tag: str = "") -> tuple[str, dict[str, Any]]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) not in {3, 4}:
        raise ValueError(
            "Artifacts must use platform=kind,url,path[,signature]."
        )
    platform_and_kind, url, local_path, *signature = parts
    if "=" not in platform_and_kind:
        raise ValueError("Artifact platform and kind must use platform=kind.")
    platform, kind = (part.strip() for part in platform_and_kind.split("=", 1))
    if not platform or not kind:
        raise ValueError("Artifact platform and kind cannot be empty.")
    if not url.startswith(("https://", "http://")):
        raise ValueError("Artifact URL must use HTTP or HTTPS.")
    path = Path(local_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Artifact file does not exist: {path}")
    artifact: dict[str, Any] = {
        "kind": kind,
        "url": url,
        "sha256": _sha256(path),
    }
    if signature and signature[0]:
        artifact["signature"] = signature[0]
    if platform == "server" and server_tag:
        artifact["tag"] = server_tag
    return platform, artifact


def promote_manifest(
    source: dict[str, Any],
    *,
    target_channel: str,
    target_version: str,
    artifacts: dict[str, dict[str, Any]],
    promoted_at: str,
    release_notes_url: str = "",
) -> dict[str, Any]:
    source_channel = UPDATE_CHANNELS.normalize_channel(str(source.get("channel") or ""))
    source = UPDATE_CHANNELS.validate_channel_manifest(source, source_channel)
    target_channel = UPDATE_CHANNELS.normalize_channel(target_channel)
    if target_channel not in {"beta", "standard"}:
        raise ValueError("Qualified builds may only be promoted to Beta or Standard.")
    if UPDATE_CHANNELS.CHANNEL_RANK[source_channel] <= UPDATE_CHANNELS.CHANNEL_RANK[target_channel]:
        raise ValueError("Promotion must move from a less stable channel to a more stable channel.")

    version = target_version.strip().lstrip("v")
    if UPDATE_CHANNELS.version_key(version)[0] < 0:
        raise ValueError("Target version is not a valid InfoMancer version.")
    target_version_channel = UPDATE_CHANNELS.release_channel(
        version, bool(UPDATE_CHANNELS._prerelease_tokens(version))
    )
    if target_version_channel != target_channel:
        raise ValueError(
            f"Target version {version} belongs to {target_version_channel}, not {target_channel}."
        )
    if not artifacts:
        raise ValueError(
            "Promotion requires freshly verified target-version artifact metadata. "
            "Versioned signed packages are never silently reused."
        )

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "channel": target_channel,
        "version": version,
        "build_id": source["build_id"],
        "commit_sha": source["commit_sha"],
        "qualified_at": source["qualified_at"],
        "qualification": source["qualification"],
        "database_schema": source["database_schema"],
        "artifacts": artifacts,
        "promotion": {
            "source_channel": source_channel,
            "source_version": source["version"],
            "source_build_id": source["build_id"],
            "source_commit_sha": source["commit_sha"],
            "promoted_at": promoted_at,
        },
    }
    notes = release_notes_url.strip() or str(source.get("release_notes_url") or "").strip()
    if notes:
        manifest["release_notes_url"] = notes
    return UPDATE_CHANNELS.validate_channel_manifest(manifest, target_channel)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Promote an already-qualified InfoMancer build to Beta or Standard while "
            "preserving immutable build identity and qualification provenance."
        )
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--target-channel", required=True, choices=("beta", "standard"))
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--server-tag", default="")
    parser.add_argument("--release-notes-url", default="")
    parser.add_argument("--promoted-at", default="")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    try:
        source = json.loads(args.source.read_text(encoding="utf-8"))
        artifacts: dict[str, dict[str, Any]] = {}
        for spec in args.artifact:
            platform, artifact = parse_artifact_spec(spec, args.server_tag.strip())
            if platform in artifacts:
                raise ValueError(f"Duplicate artifact platform: {platform}")
            artifacts[platform] = artifact
        promoted_at = args.promoted_at.strip() or datetime.now(timezone.utc).isoformat()
        manifest = promote_manifest(
            source,
            target_channel=args.target_channel,
            target_version=args.target_version,
            artifacts=artifacts,
            promoted_at=promoted_at,
            release_notes_url=args.release_notes_url,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

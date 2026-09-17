#!/usr/bin/env python3
"""Build a deterministic InfoMancer update-channel manifest.

The caller is responsible for running qualification and packaging first. This
script refuses any qualification status except ``passed`` so it cannot be used
to publish a failed or incomplete build as an eligible channel candidate.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_update_channels = load_module(
    "infomancer_update_channels", ROOT / "app" / "update_channels.py"
)
_migrations = load_module(
    "app.migrations", ROOT / "app" / "migrations.py"
)
normalize_channel = _update_channels.normalize_channel
version_key = _update_channels.version_key
schema_contract = _migrations.schema_contract


GATES_DEFAULT = (
    "python-windows",
    "python-macos",
    "python-linux",
    "security-audit",
    "browser-acceptance",
)


def valid_https_url(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme.casefold() == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def parse_artifact(value: str) -> tuple[str, dict]:
    """Parse platform=kind,url,path[,signature] into a manifest artifact."""
    if "=" not in value:
        raise ValueError("Artifact must use platform=kind,url,path[,signature].")
    platform, details = value.split("=", 1)
    platform = platform.strip().casefold()
    fields = [field.strip() for field in details.split(",")]
    if not platform or len(fields) not in {3, 4}:
        raise ValueError("Artifact must use platform=kind,url,path[,signature].")
    kind, url, file_name = fields[:3]
    signature = fields[3] if len(fields) == 4 else ""
    if not kind or not valid_https_url(url):
        raise ValueError("Artifact kind and credential-free HTTPS URL are required.")
    path = Path(file_name)
    if not path.is_file():
        raise ValueError(f"Artifact file does not exist: {path}")
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    artifact = {"kind": kind, "url": url, "sha256": sha256}
    if signature:
        artifact["signature"] = signature
    return platform, artifact


def build_manifest(arguments: argparse.Namespace) -> dict:
    channel = normalize_channel(arguments.channel)
    if version_key(arguments.version)[0] < 0:
        raise ValueError("Version must be a valid InfoMancer semantic version.")
    commit = arguments.commit_sha.strip().casefold()
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ValueError("Commit SHA must contain exactly 40 hexadecimal characters.")
    if arguments.qualification_status != "passed":
        raise ValueError("Only a passed qualification may produce a channel manifest.")
    if arguments.run_id < 1:
        raise ValueError("Qualification run id must be positive.")

    qualified_at = arguments.qualified_at or datetime.now(timezone.utc).isoformat()
    gates = tuple(dict.fromkeys(arguments.gate or GATES_DEFAULT))
    if not gates:
        raise ValueError("At least one qualification gate is required.")

    artifacts: dict[str, dict] = {}
    for value in arguments.artifact:
        platform, artifact = parse_artifact(value)
        if platform in artifacts:
            raise ValueError(f"Artifact platform appears more than once: {platform}")
        artifacts[platform] = artifact

    manifest = {
        "schema_version": 1,
        "channel": channel,
        "version": arguments.version.lstrip("v"),
        "build_id": arguments.build_id,
        "commit_sha": commit,
        "qualified_at": qualified_at,
        "qualification": {
            "status": "passed",
            "workflow": arguments.workflow,
            "run_id": arguments.run_id,
            "gates": list(gates),
        },
        "database_schema": schema_contract(),
        "artifacts": artifacts,
    }
    if arguments.run_url:
        if not valid_https_url(arguments.run_url):
            raise ValueError("Qualification run URL must use credential-free HTTPS.")
        manifest["qualification"]["run_url"] = arguments.run_url
    if arguments.release_notes_url:
        if not valid_https_url(arguments.release_notes_url):
            raise ValueError("Release notes URL must use credential-free HTTPS.")
        manifest["release_notes_url"] = arguments.release_notes_url
    return manifest


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Build an InfoMancer update-channel manifest")
    value.add_argument("--channel", required=True, choices=("standard", "beta", "dev"))
    value.add_argument("--version", required=True)
    value.add_argument("--build-id", required=True)
    value.add_argument("--commit-sha", required=True)
    value.add_argument("--workflow", default="Tests")
    value.add_argument("--run-id", required=True, type=int)
    value.add_argument("--run-url", default="")
    value.add_argument("--qualified-at", default="")
    value.add_argument("--qualification-status", default="passed")
    value.add_argument("--gate", action="append", default=[])
    value.add_argument("--artifact", action="append", default=[])
    value.add_argument("--release-notes-url", default="")
    value.add_argument("--output", type=Path, required=True)
    return value


def main() -> int:
    arguments = parser().parse_args()
    try:
        manifest = build_manifest(arguments)
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError) as exc:
        print(f"Could not build update manifest: {exc}")
        return 2
    print(arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

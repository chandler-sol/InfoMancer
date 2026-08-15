#!/usr/bin/env python3
"""Restricted host-side release updater for InfoMancer.

The web application can only write a small request file. This separately-run
helper validates that request, checks out a signed-off release tag, rebuilds
the configured Compose project, verifies health, and rolls back on failure.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


TAG_PATTERN = re.compile(r"v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?")


class UpdateError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def run(command: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        command, cwd=cwd, text=True, capture_output=True, check=False,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise UpdateError(detail or f"{command[0]} exited unsuccessfully.")
    return completed.stdout.strip()


def verify_release_tag(
    tag: str, repository: Path, trusted_signing_keys: set[str] | None = None,
) -> None:
    completed = subprocess.run(
        ["git", "verify-tag", "--raw", tag], cwd=repository, text=True,
        capture_output=True, check=False,
    )
    status = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    if completed.returncode:
        raise UpdateError(
            "The release tag does not have a valid cryptographic signature. "
            "The update was stopped before any checkout occurred."
        )
    fingerprints = {
        match.upper() for match in re.findall(r"\[GNUPG:\] VALIDSIG ([0-9A-Fa-f]{40,64})", status)
    }
    trusted = {value.replace(" ", "").upper() for value in (trusted_signing_keys or set()) if value}
    if trusted and fingerprints.isdisjoint(trusted):
        raise UpdateError(
            "The release tag was signed, but not by a configured trusted InfoMancer release key."
        )


def compose_command(files: list[str]) -> list[str]:
    command = ["docker", "compose", "-p", "infomancer"]
    for value in files:
        command.extend(["-f", value])
    return command


def wait_for_health(url: str, seconds: int) -> None:
    deadline = time.monotonic() + seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=4) as response:
                if response.status == 200:
                    return
                last_error = f"HTTP {response.status}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = str(exc)
        time.sleep(3)
    raise UpdateError(
        "The rebuilt application did not become healthy in time"
        + (f": {last_error}" if last_error else ".")
    )


def process_request(
    repository: Path, data_directory: Path, files: list[str],
    health_url: str, health_timeout: int, trusted_signing_keys: set[str] | None = None,
) -> bool:
    request_path = data_directory / "update-request.json"
    status_path = data_directory / "update-status.json"
    if not request_path.exists():
        return False
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        tag = str(request.get("tag", "")).strip()
        if not TAG_PATTERN.fullmatch(tag):
            raise UpdateError("The queued release tag is not valid.")
        if not (repository / ".git").exists() or not (repository / "compose.yaml").exists():
            raise UpdateError(
                "The updater is not pointed at an InfoMancer Git checkout."
            )
        for compose_file in files:
            if not (repository / compose_file).is_file():
                raise UpdateError(
                    f"The configured Compose file does not exist: {compose_file}"
                )

        write_json(status_path, {
            "status": "running", "latest_version": tag,
            "message": f"Updating InfoMancer to {tag}.",
            "started_at": utc_now(),
        })
        if run(["git", "status", "--porcelain", "--untracked-files=no"], repository):
            raise UpdateError(
                "The InfoMancer source has local edits. The updater stopped "
                "so those changes would not be overwritten."
            )
        previous_commit = run(["git", "rev-parse", "HEAD"], repository)
        run(["git", "fetch", "--tags", "origin"], repository)
        verify_release_tag(tag, repository, trusted_signing_keys)
        target_commit = run(
            ["git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"],
            repository,
        )
        run(["git", "checkout", "--detach", target_commit], repository)
        compose = compose_command(files)
        try:
            run(compose + ["up", "-d", "--build", "--remove-orphans"], repository)
            wait_for_health(health_url, health_timeout)
        except Exception as update_exc:
            run(["git", "checkout", "--detach", previous_commit], repository)
            try:
                run(compose + ["up", "-d", "--build", "--remove-orphans"], repository)
                wait_for_health(health_url, health_timeout)
            except Exception as rollback_exc:
                raise UpdateError(
                    "The update failed and the previous release could not be "
                    f"started automatically. Update error: {update_exc}. "
                    f"Rollback error: {rollback_exc}"
                ) from rollback_exc
            write_json(status_path, {
                "status": "rolled_back", "latest_version": tag,
                "message": (
                    "The update did not start correctly, so InfoMancer "
                    "returned to the previous release."
                ),
                "finished_at": utc_now(),
            })
            request_path.unlink(missing_ok=True)
            return True

        write_json(status_path, {
            "status": "success", "current_version": tag,
            "latest_version": tag,
            "message": f"InfoMancer was updated successfully to {tag}.",
            "finished_at": utc_now(),
        })
        request_path.unlink(missing_ok=True)
        return True
    except (OSError, json.JSONDecodeError, UpdateError) as exc:
        write_json(status_path, {
            "status": "error",
            "message": (
                "The update could not be completed. The installed version "
                f"was left in place. Reason: {exc}"
            ),
            "finished_at": utc_now(),
        })
        request_path.unlink(missing_ok=True)
        return True


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="InfoMancer host updater")
    value.add_argument(
        "--repository", type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    value.add_argument("--data-directory", type=Path, default=Path("data"))
    value.add_argument("--compose-file", action="append", dest="compose_files")
    value.add_argument("--health-url", default="http://127.0.0.1:8787/health")
    value.add_argument("--health-timeout", type=int, default=120)
    value.add_argument(
        "--trusted-signing-key", action="append", default=[],
        help="Allowed GPG signing-key fingerprint. May be supplied more than once.",
    )
    value.add_argument("--watch", action="store_true")
    value.add_argument("--poll-seconds", type=int, default=5)
    return value


def main() -> int:
    arguments = parser().parse_args()
    repository = arguments.repository.resolve()
    data_directory = arguments.data_directory
    if not data_directory.is_absolute():
        data_directory = repository / data_directory
    files = arguments.compose_files or ["compose.yaml"]
    while True:
        handled = process_request(
            repository, data_directory, files,
            arguments.health_url, max(15, arguments.health_timeout),
            {value for value in arguments.trusted_signing_key if value.strip()},
        )
        if not arguments.watch:
            return 0
        time.sleep(max(2, arguments.poll_seconds))


if __name__ == "__main__":
    sys.exit(main())

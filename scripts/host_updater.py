#!/usr/bin/env python3
"""Restricted host-side release updater for InfoMancer.

The web application can only write a small request file. This separately-run
helper validates that request, verifies a cryptographically signed release tag,
rebuilds the configured Compose project, verifies health, and rolls back on
failure.
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
FINGERPRINT_PATTERN = re.compile(r"^[0-9A-Fa-f]{40,64}$")
COMMIT_SHA_PATTERN = re.compile(r"^[0-9A-Fa-f]{40}$")
MAX_RELEASE_METADATA_BYTES = 64 * 1024
MAX_HISTORY_ENTRIES = 100
RELEASE_METADATA_FIELDS = {
    "channel",
    "latest_version",
    "server_tag",
    "build_id",
    "commit_sha",
    "qualified_at",
    "qualification_status",
    "qualification_workflow",
    "qualification_run_id",
    "qualification_run_url",
    "qualification_gates",
    "database_schema",
    "schema_assessment",
    "metadata_source",
    "manifest_url",
    "release_notes_url",
}


class UpdateError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: object) -> None:
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


def verified_signature_fingerprints(status: str) -> set[str]:
    """Return both signing-subkey and primary-key fingerprints from VALIDSIG."""
    fingerprints: set[str] = set()
    for line in status.splitlines():
        marker = "[GNUPG:] VALIDSIG "
        if marker not in line:
            continue
        fields = line.split(marker, 1)[1].split()
        if fields and FINGERPRINT_PATTERN.fullmatch(fields[0]):
            fingerprints.add(fields[0].upper())
        # GnuPG's VALIDSIG status ends with the primary-key fingerprint. This
        # differs from fields[0] when the tag is signed by a signing subkey.
        if fields and FINGERPRINT_PATTERN.fullmatch(fields[-1]):
            fingerprints.add(fields[-1].upper())
    return fingerprints


def normalize_trusted_signing_keys(values: set[str] | None) -> set[str]:
    """Normalize and validate the explicit release-signing trust boundary."""
    trusted = {
        value.replace(" ", "").upper()
        for value in (values or set())
        if value and FINGERPRINT_PATTERN.fullmatch(value.replace(" ", ""))
    }
    if not trusted:
        raise UpdateError(
            "At least one trusted InfoMancer release signing-key fingerprint is required."
        )
    return trusted


def verify_release_tag(
    tag: str, repository: Path, trusted_signing_keys: set[str] | None = None,
) -> None:
    trusted = normalize_trusted_signing_keys(trusted_signing_keys)
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
    fingerprints = verified_signature_fingerprints(status)
    if not fingerprints:
        raise UpdateError(
            "Git accepted the release tag signature, but InfoMancer could not identify "
            "the signing key fingerprint. The update was stopped."
        )
    if fingerprints.isdisjoint(trusted):
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


def release_metadata(request: dict) -> dict:
    value = request.get("release") or {}
    if not isinstance(value, dict):
        raise UpdateError("The queued release identity is invalid.")
    release = {
        key: value[key]
        for key in RELEASE_METADATA_FIELDS
        if key in value and value[key] not in (None, "", [], {})
    }
    try:
        encoded = json.dumps(release, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise UpdateError("The queued release identity is not valid JSON metadata.") from exc
    if len(encoded.encode("utf-8")) > MAX_RELEASE_METADATA_BYTES:
        raise UpdateError("The queued release identity is unexpectedly large.")
    commit_sha = str(release.get("commit_sha") or "").strip()
    if commit_sha and not COMMIT_SHA_PATTERN.fullmatch(commit_sha):
        raise UpdateError("The queued release identity contains an invalid commit SHA.")
    return release


def append_history(data_directory: Path, entry: dict) -> None:
    """Retain bounded updater outcomes for audit and recovery guidance."""
    path = data_directory / "update-history.json"
    history: list[dict] = []
    if path.exists():
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(candidate, list):
                history = [item for item in candidate if isinstance(item, dict)]
        except (OSError, json.JSONDecodeError):
            history = []
    history.append(entry)
    write_json(path, history[-MAX_HISTORY_ENTRIES:])


def _status_with_release(status: dict, release: dict, requested_by: str) -> dict:
    value = dict(status)
    if release:
        value["release"] = release
    if requested_by:
        value["requested_by"] = requested_by
    return value


def process_request(
    repository: Path, data_directory: Path, files: list[str],
    health_url: str, health_timeout: int, trusted_signing_keys: set[str] | None = None,
) -> bool:
    request_path = data_directory / "update-request.json"
    status_path = data_directory / "update-status.json"
    if not request_path.exists():
        return False

    tag = ""
    requested_by = ""
    release: dict = {}
    previous_commit = ""
    target_commit = ""
    started_at = utc_now()
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise UpdateError("The queued updater request is invalid.")
        tag = str(request.get("tag", "")).strip()
        requested_by = str(request.get("requested_by") or "").strip()
        release = release_metadata(request)
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

        write_json(status_path, _status_with_release({
            "status": "running", "latest_version": tag,
            "message": f"Updating InfoMancer to {tag}.",
            "started_at": started_at,
        }, release, requested_by))
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
        qualified_commit = str(release.get("commit_sha") or "").strip().casefold()
        if qualified_commit and target_commit.casefold() != qualified_commit:
            raise UpdateError(
                "The signed release tag does not point to the commit recorded by the qualified update manifest. The update was stopped before checkout."
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
            finished_at = utc_now()
            status = _status_with_release({
                "status": "rolled_back", "latest_version": tag,
                "previous_commit": previous_commit,
                "target_commit": target_commit,
                "message": (
                    "The update did not start correctly, so InfoMancer "
                    "returned to the previous release."
                ),
                "started_at": started_at,
                "finished_at": finished_at,
            }, release, requested_by)
            write_json(status_path, status)
            append_history(data_directory, status)
            request_path.unlink(missing_ok=True)
            return True

        finished_at = utc_now()
        status = _status_with_release({
            "status": "success", "current_version": tag,
            "latest_version": tag,
            "previous_commit": previous_commit,
            "target_commit": target_commit,
            "message": f"InfoMancer was updated successfully to {tag}.",
            "started_at": started_at,
            "finished_at": finished_at,
        }, release, requested_by)
        write_json(status_path, status)
        append_history(data_directory, status)
        request_path.unlink(missing_ok=True)
        return True
    except (OSError, json.JSONDecodeError, UpdateError) as exc:
        finished_at = utc_now()
        status = _status_with_release({
            "status": "error",
            "latest_version": tag,
            "previous_commit": previous_commit,
            "target_commit": target_commit,
            "message": (
                "The update could not be completed. The installed version "
                f"was left in place. Reason: {exc}"
            ),
            "started_at": started_at,
            "finished_at": finished_at,
        }, release, requested_by)
        write_json(status_path, status)
        try:
            append_history(data_directory, status)
        except OSError:
            pass
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
        help="Required trusted primary or signing-subkey GPG fingerprint. May be supplied more than once.",
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
    try:
        trusted_signing_keys = normalize_trusted_signing_keys(
            {value for value in arguments.trusted_signing_key if value.strip()}
        )
    except UpdateError as exc:
        print(f"Updater configuration error: {exc}", file=sys.stderr)
        return 2
    while True:
        handled = process_request(
            repository, data_directory, files,
            arguments.health_url, max(15, arguments.health_timeout),
            trusted_signing_keys,
        )
        if not arguments.watch:
            return 0
        time.sleep(max(2, arguments.poll_seconds))


if __name__ == "__main__":
    sys.exit(main())

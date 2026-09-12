from __future__ import annotations

import json
import os
from pathlib import Path

from .maintenance import MaintenanceError, update_request_path, write_update_request


MAX_RELEASE_METADATA_BYTES = 64 * 1024
_RELEASE_FIELDS = (
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
)


def release_identity_from_status(status: dict) -> dict:
    """Copy only trusted update identity fields into a host-updater request.

    This metadata is audit and consistency information. The host updater still owns
    the cryptographic release-tag trust decision and may additionally require a
    manifest commit SHA to match the commit referenced by that signed tag.
    """
    identity = {
        key: status[key]
        for key in _RELEASE_FIELDS
        if key in status and status[key] not in (None, "", [], {})
    }
    encoded = json.dumps(identity, separators=(",", ":"), sort_keys=True)
    if len(encoded.encode("utf-8")) > MAX_RELEASE_METADATA_BYTES:
        raise MaintenanceError("The selected update metadata is unexpectedly large.")
    return identity


def write_qualified_update_request(
    database_path: Path,
    tag: str,
    requested_by: str,
    release_identity: dict,
) -> Path:
    """Write the ordinary request plus the qualified identity as one final payload."""
    path = write_update_request(database_path, tag, requested_by)
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise ValueError("Updater request is not an object.")
        identity = release_identity_from_status(release_identity)
        request["release"] = identity
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(request, indent=2), encoding="utf-8")
        os.replace(temporary, path)
        return path
    except (OSError, ValueError, json.JSONDecodeError, MaintenanceError) as exc:
        try:
            path.unlink(missing_ok=True)
            path.with_suffix(".tmp").unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(exc, MaintenanceError):
            raise
        raise MaintenanceError(
            "InfoMancer could not preserve the qualified build identity in the updater request. The update was not queued."
        ) from exc

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Mapping


DECISION_SNAPSHOT_VERSION = 1


class DecisionSnapshotError(RuntimeError):
    """Raised when a result cannot be sealed against its persisted inputs."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _json_object(value: Any) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _without_snapshot(claimed: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(claimed)
    value.pop("decision_snapshot", None)
    return value


def result_revision(scan_or_claimed: Mapping[str, Any]) -> int:
    if "claimed_identity_json" in scan_or_claimed:
        claimed = _json_object(scan_or_claimed.get("claimed_identity_json"))
    else:
        claimed = dict(scan_or_claimed)
    try:
        revision = int(claimed.get("result_revision") or 0)
    except (TypeError, ValueError):
        return 0
    return revision if revision > 0 else 0


def _collect_artifact_refs(value: Any) -> set[int]:
    found: set[int] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "artifact_id":
                try:
                    artifact_id = int(item)
                except (TypeError, ValueError):
                    artifact_id = 0
                if artifact_id > 0:
                    found.add(artifact_id)
                continue
            if key == "artifact_ids" and isinstance(item, list):
                for raw in item:
                    try:
                        artifact_id = int(raw)
                    except (TypeError, ValueError):
                        continue
                    if artifact_id > 0:
                        found.add(artifact_id)
                continue
            found.update(_collect_artifact_refs(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_collect_artifact_refs(item))
    return found


def _candidate_rows(
    conn: sqlite3.Connection,
    scan_id: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT scan_id,candidate_key,identity_kind,provider,provider_item_id,
                  expected_episode_id,order_namespace,season,episode,display_name,
                  rank,score,support_strength,conflict_strength,
                  independent_categories,details_json
           FROM media_identity_candidates
           WHERE scan_id=?
           ORDER BY rank,candidate_key""",
        (int(scan_id),),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["details"] = _json_object(item.pop("details_json", "{}"))
        result.append(item)
    return result


def _evidence_rows(
    conn: sqlite3.Connection,
    scan_id: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT id,scan_id,candidate_key,analyzer_key,analyzer_version,
                  evidence_category,correlation_group,relation,strength,
                  source_kind,source_ref,timestamp_ms,value_text,details_json,
                  cache_key,profile
           FROM media_identity_evidence
           WHERE scan_id=?
           ORDER BY id""",
        (int(scan_id),),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["details"] = _json_object(item.pop("details_json", "{}"))
        result.append(item)
    return result


def _artifact_rows(
    conn: sqlite3.Connection,
    file_id: int,
    artifact_ids: set[int],
    cache_keys: set[str],
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = [int(file_id)]
    if artifact_ids:
        clauses.append("id IN (" + ",".join("?" for _ in artifact_ids) + ")")
        params.extend(sorted(artifact_ids))
    if cache_keys:
        clauses.append("cache_key IN (" + ",".join("?" for _ in cache_keys) + ")")
        params.extend(sorted(cache_keys))
    if not clauses:
        return []

    rows = conn.execute(
        f"""SELECT id,file_id,artifact_type,analyzer_key,analyzer_version,
                   cache_key,status,profile,source_kind,source_ref,
                   source_signature,file_size_bytes,file_modified_at,start_ms,
                   end_ms,text_value,cache_path,payload_json,error
            FROM media_identity_artifacts
            WHERE file_id=? AND ({' OR '.join(clauses)})
            ORDER BY id""",
        tuple(params),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["payload"] = _json_object(item.pop("payload_json", "{}"))
        result.append(item)
    return result


def decision_snapshot_payload(
    conn: sqlite3.Connection,
    scan_id: int,
) -> dict[str, Any]:
    scan_row = conn.execute(
        """SELECT id,file_id,identity_kind,requested_profile,completed_profile,
                  status,stage,claimed_identity_json,file_size_bytes,
                  file_modified_at,file_sha256,metadata_signature,result_state,
                  best_candidate_key
           FROM media_identity_scans
           WHERE id=?""",
        (int(scan_id),),
    ).fetchone()
    if not scan_row:
        raise DecisionSnapshotError("Episode Identity scan was not found.")

    scan = dict(scan_row)
    claimed = _json_object(scan["claimed_identity_json"])
    candidates = _candidate_rows(conn, int(scan_id))
    evidence = _evidence_rows(conn, int(scan_id))

    artifact_ids = _collect_artifact_refs(claimed)
    required_cache_keys: set[str] = set()
    referenced_cache_keys: set[str] = set()
    for item in evidence:
        artifact_ids.update(_collect_artifact_refs(item.get("details")))
        cache_key = str(item.get("cache_key") or "")
        if cache_key:
            referenced_cache_keys.add(cache_key)
            if (
                str(item.get("analyzer_key") or "") == "subtitle-synopsis"
                and str(item.get("source_kind") or "") == "sidecar_subtitle"
            ):
                required_cache_keys.add(cache_key)

    artifacts = _artifact_rows(
        conn,
        int(scan["file_id"]),
        artifact_ids,
        referenced_cache_keys,
    )
    found_ids = {int(item["id"]) for item in artifacts}
    missing_ids = sorted(artifact_ids - found_ids)
    if missing_ids:
        raise DecisionSnapshotError(
            "Episode Identity result references missing artifact rows."
        )

    found_cache_keys = {
        str(item.get("cache_key") or "")
        for item in artifacts
        if str(item.get("cache_key") or "")
    }
    if required_cache_keys - found_cache_keys:
        raise DecisionSnapshotError(
            "Episode Identity result references missing subtitle artifacts."
        )

    return {
        "version": DECISION_SNAPSHOT_VERSION,
        "scan": {
            "id": int(scan["id"]),
            "file_id": int(scan["file_id"]),
            "identity_kind": str(scan["identity_kind"] or ""),
            "requested_profile": str(scan["requested_profile"] or ""),
            "completed_profile": str(scan["completed_profile"] or ""),
            "status": str(scan["status"] or ""),
            "stage": str(scan["stage"] or ""),
            "claimed_identity": _without_snapshot(claimed),
            "file_size_bytes": int(scan["file_size_bytes"] or 0),
            "file_modified_at": scan["file_modified_at"],
            "file_sha256": str(scan["file_sha256"] or ""),
            "metadata_signature": str(scan["metadata_signature"] or ""),
            "result_state": str(scan["result_state"] or ""),
            "best_candidate_key": str(scan["best_candidate_key"] or ""),
        },
        "candidates": candidates,
        "evidence": evidence,
        "referenced_artifact_ids": sorted(artifact_ids),
        "referenced_cache_keys": sorted(referenced_cache_keys),
        "artifacts": artifacts,
    }


def decision_snapshot_sha256(
    conn: sqlite3.Connection,
    scan_id: int,
) -> str:
    payload = decision_snapshot_payload(conn, int(scan_id))
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def seal_decision_snapshot(
    conn: sqlite3.Connection,
    scan_id: int,
    *,
    revision: int,
) -> str:
    if int(revision) <= 0:
        raise DecisionSnapshotError("Episode Identity result revision must be positive.")
    row = conn.execute(
        "SELECT claimed_identity_json FROM media_identity_scans WHERE id=?",
        (int(scan_id),),
    ).fetchone()
    if not row:
        raise DecisionSnapshotError("Episode Identity scan was not found.")

    claimed = _json_object(row["claimed_identity_json"])
    current_revision = result_revision(claimed)
    existing_snapshot = claimed.get("decision_snapshot")
    if isinstance(existing_snapshot, Mapping) and int(revision) <= current_revision:
        raise DecisionSnapshotError(
            "Episode Identity result revisions are immutable and must increase."
        )

    claimed["result_revision"] = int(revision)
    claimed.pop("decision_snapshot", None)
    conn.execute(
        "UPDATE media_identity_scans SET claimed_identity_json=? WHERE id=?",
        (_canonical_json(claimed), int(scan_id)),
    )

    digest = decision_snapshot_sha256(conn, int(scan_id))
    claimed["decision_snapshot"] = {
        "version": DECISION_SNAPSHOT_VERSION,
        "revision": int(revision),
        "sha256": digest,
    }
    conn.execute(
        "UPDATE media_identity_scans SET claimed_identity_json=? WHERE id=?",
        (_canonical_json(claimed), int(scan_id)),
    )
    return digest


def decision_snapshot_matches(
    conn: sqlite3.Connection,
    scan: Mapping[str, Any],
) -> bool:
    claimed = _json_object(scan.get("claimed_identity_json"))
    snapshot = claimed.get("decision_snapshot")
    if not isinstance(snapshot, Mapping):
        return False
    try:
        version = int(snapshot.get("version") or 0)
        revision = int(snapshot.get("revision") or 0)
    except (TypeError, ValueError):
        return False
    digest = str(snapshot.get("sha256") or "")
    if (
        version != DECISION_SNAPSHOT_VERSION
        or revision <= 0
        or revision != result_revision(claimed)
        or len(digest) != 64
    ):
        return False
    try:
        current = decision_snapshot_sha256(conn, int(scan["id"]))
    except (DecisionSnapshotError, KeyError, TypeError, ValueError, sqlite3.Error):
        return False
    return current == digest

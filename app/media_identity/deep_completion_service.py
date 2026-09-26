from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from ..db import Database
from .deep import deep_plan_metadata_is_current
from .deep_evidence import deep_evidence_metadata_is_current
from .decision_snapshot import result_revision, seal_decision_snapshot
from .models import IdentityProfile, IdentityResultState
from .service import MediaIdentityDecisionService


class DeepCompletionError(RuntimeError):
    """A staged Deep resolver result cannot be finalized safely."""


@dataclass(frozen=True)
class DeepCompletionRun:
    scan_id: int
    resolved_revision: int
    completed_revision: int


def _json_object(value: object) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


class DeepCompletionService:
    """Finalize a resolved staged Deep candidate/evidence revision."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def finalize(self, scan_id: int) -> DeepCompletionRun:
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            scan, candidates, evidence = (
                MediaIdentityDecisionService._scan_snapshot(
                    conn,
                    int(scan_id),
                )
            )
            claimed = _json_object(
                scan.get("claimed_identity_json")
            )
            completed_profile = str(
                scan.get("completed_profile") or ""
            )
            normal_attempted_fast = (
                completed_profile == IdentityProfile.FAST.value
                and isinstance(
                    claimed.get("normal_ocr"),
                    Mapping,
                )
                and isinstance(
                    claimed.get("normal_speech"),
                    Mapping,
                )
            )
            result_state = str(
                scan.get("result_state") or ""
            )
            best_candidate_key = str(
                scan.get("best_candidate_key") or ""
            )
            if (
                scan.get("status") != "complete"
                or str(scan.get("requested_profile") or "")
                != IdentityProfile.DEEP.value
                or (
                    completed_profile
                    != IdentityProfile.NORMAL.value
                    and not normal_attempted_fast
                )
                or not result_state
                or (
                    not best_candidate_key
                    and result_state
                    != IdentityResultState.INCONCLUSIVE.value
                )
            ):
                raise DeepCompletionError(
                    "Deep completion requires one resolved staged Deep upgrade."
                )
            current, file_row = (
                MediaIdentityDecisionService._scan_snapshot_is_current(
                    conn,
                    scan,
                    evidence,
                )
            )
            if not current or file_row is None:
                raise DeepCompletionError(
                    "The staged Deep resolver snapshot is stale."
                )
            resolved_revision = result_revision(scan)
            if resolved_revision < 1:
                raise DeepCompletionError(
                    "Deep completion requires a sealed resolved revision."
                )

            deep_identity = claimed.get("deep_identity")
            if not isinstance(deep_identity, Mapping):
                raise DeepCompletionError(
                    "Deep candidate/correlation provenance is missing."
                )
            full_file = conn.execute(
                """SELECT f.*,t.kind title_kind
                   FROM files f
                   JOIN titles t ON t.id=f.title_id
                   WHERE f.id=?""",
                (int(scan["file_id"]),),
            ).fetchone()
            if full_file is None:
                raise DeepCompletionError(
                    "Deep completion media file was not found."
                )
            full_file = dict(full_file)
            language = str(
                claimed.get("scan_language") or "eng"
            ).strip().casefold() or "eng"
            if not deep_plan_metadata_is_current(
                conn,
                file_id=int(scan["file_id"]),
                title_id=int(full_file["title_id"]),
                season=int(full_file["season"]),
                episode_start=int(full_file["episode_start"]),
                episode_end=int(
                    full_file["episode_end"]
                    or full_file["episode_start"]
                ),
                language=language,
                metadata=deep_identity,
            ):
                raise DeepCompletionError(
                    "Deep candidate/correlation plan changed before completion."
                )
            raw_expected_keys = deep_identity.get(
                "candidate_keys"
            )
            if (
                not isinstance(raw_expected_keys, list)
                or any(
                    not isinstance(value, str) or not value
                    for value in raw_expected_keys
                )
                or len(set(raw_expected_keys))
                != len(raw_expected_keys)
            ):
                raise DeepCompletionError(
                    "Deep candidate provenance is malformed."
                )
            persisted_keys = [
                str(item["candidate_key"])
                for item in candidates
            ]
            if (
                len(persisted_keys) != len(raw_expected_keys)
                or set(persisted_keys) != set(raw_expected_keys)
            ):
                raise DeepCompletionError(
                    "Persisted resolver candidates no longer match "
                    "the Deep candidate plan."
                )
            if not deep_evidence_metadata_is_current(
                claimed.get("deep_evidence"),
                evidence,
                file_id=int(scan["file_id"]),
            ):
                raise DeepCompletionError(
                    "Deep evidence provenance is stale or malformed."
                )

            deep_evidence = claimed.get("deep_evidence")
            assert isinstance(deep_evidence, Mapping)
            try:
                baseline_revision = int(
                    deep_evidence.get("baseline_revision") or 0
                )
            except (TypeError, ValueError) as exc:
                raise DeepCompletionError(
                    "Deep evidence baseline revision is malformed."
                ) from exc
            if (
                baseline_revision < 1
                or resolved_revision <= baseline_revision
            ):
                raise DeepCompletionError(
                    "Deep resolver revision did not advance beyond its baseline."
                )

            completed_revision = resolved_revision + 1
            conn.execute(
                """UPDATE media_identity_scans
                   SET completed_profile='deep',
                       stage='deep_resolved',
                       completed_at=CURRENT_TIMESTAMP,
                       error=''
                   WHERE id=?""",
                (int(scan_id),),
            )
            seal_decision_snapshot(
                conn,
                int(scan_id),
                revision=completed_revision,
            )

            final_scan, _, final_evidence = (
                MediaIdentityDecisionService._scan_snapshot(
                    conn,
                    int(scan_id),
                )
            )
            final_current, _ = (
                MediaIdentityDecisionService._scan_snapshot_is_current(
                    conn,
                    final_scan,
                    final_evidence,
                )
            )
            if (
                not final_current
                or str(final_scan.get("completed_profile") or "")
                != IdentityProfile.DEEP.value
                or result_revision(final_scan)
                != completed_revision
            ):
                raise DeepCompletionError(
                    "The finalized Deep snapshot failed its own freshness check."
                )

        return DeepCompletionRun(
            scan_id=int(scan_id),
            resolved_revision=resolved_revision,
            completed_revision=completed_revision,
        )

from __future__ import annotations

from itertools import combinations
from typing import Any

from .db import Database
from .file_hashes import MediaHashService


DECISIONS = {"active", "ignored", "not_duplicate"}


def _signature(row: dict[str, Any]) -> str:
    return f"{int(row.get('size_bytes') or 0)}:{row.get('modified_at') or ''}"


def _quality_score(row: dict[str, Any]) -> tuple:
    pixels = int(row.get("width") or 0) * int(row.get("height") or 0)
    dynamic_range = str(row.get("dynamic_range") or "").upper()
    hdr = 1 if dynamic_range and dynamic_range != "SDR" else 0
    return (
        pixels,
        hdr,
        int(row.get("bitrate") or 0),
        int(row.get("size_bytes") or 0),
    )


def _format_resolution(row: dict[str, Any]) -> str:
    if row.get("width") and row.get("height"):
        return f"{row['width']}x{row['height']}"
    return "Not inspected"


class DuplicateService:
    """Find and explain duplicate candidates without changing media files."""

    def __init__(self, database: Database, hashes: MediaHashService | None = None):
        self.database = database
        self.hashes = hashes or MediaHashService(database)

    def candidates(self, *, status: str = "active") -> list[dict[str, Any]]:
        status = status if status in DECISIONS else "active"
        with self.database.connect() as conn:
            rows = [dict(row) for row in conn.execute(
                """SELECT f.*,t.kind,COALESCE(t.metadata_title,t.title) title_name,
                          t.year,t.metadata_year,r.id root_id,r.label root_label
                   FROM files f
                   JOIN titles t ON t.id=f.title_id
                   JOIN roots r ON r.id=t.root_id
                   ORDER BY f.title_id,f.season,f.episode_start,f.id"""
            )]
            reviews = {
                (row["file_a_id"], row["file_b_id"]): dict(row)
                for row in conn.execute("SELECT * FROM duplicate_reviews")
            }
            hash_records = self.hashes.records()

        by_title: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            by_title.setdefault(int(row["title_id"]), []).append(row)

        candidates: list[dict[str, Any]] = []
        for title_rows in by_title.values():
            for left, right in self._candidate_pairs(title_rows):
                pair = (min(left["id"], right["id"]), max(left["id"], right["id"]))
                review = reviews.get(pair, {})
                decision = review.get("decision", "active")
                signatures_changed = bool(
                    review.get("file_a_signature") and (
                        review.get("file_a_signature") != _signature(left)
                        or review.get("file_b_signature") != _signature(right)
                    )
                )
                effective_status = (
                    "active" if decision == "ignored" and signatures_changed else decision
                )
                if effective_status != status:
                    continue
                current_review = dict(review)
                if signatures_changed:
                    current_review["file_a_sha256"] = None
                    current_review["file_b_sha256"] = None
                    current_review["verified_at"] = None
                for row, key in ((left, "file_a"), (right, "file_b")):
                    record = hash_records.get(int(row["id"]))
                    row["hash_status"] = record.get("status") if record else "not_verified"
                    row["hash_error"] = record.get("error") if record else ""
                    row["hashed_at"] = record.get("hashed_at") if record else None
                    current = bool(record and MediaHashService._current({
                        "status": record.get("status"), "sha256": record.get("sha256"),
                        "hash_size": record.get("size_bytes"), "size_bytes": row.get("size_bytes"),
                        "hash_modified": record.get("modified_at"), "modified_at": row.get("modified_at"),
                    }))
                    if current:
                        current_review[f"{key}_sha256"] = record["sha256"]
                        current_review["verified_at"] = record.get("hashed_at")
                    elif record:
                        current_review[f"{key}_sha256"] = None
                candidates.append(self._candidate(
                    left, right, current_review, effective_status
                ))
        candidates.sort(key=lambda item: (
            0 if item["classification"] == "verified_exact" else 1,
            item["title_name"].casefold(), item["file_a"]["filename"].casefold(),
        ))
        return candidates

    @staticmethod
    def _candidate_pairs(title_rows: list[dict[str, Any]]):
        """Yield only plausible pairs, avoiding all-pairs work for large TV series."""
        if not title_rows:
            return
        if title_rows[0]["kind"] != "tv":
            yield from combinations(title_rows, 2)
            return

        episode_files: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for row in title_rows:
            season = row.get("season")
            start = row.get("episode_start")
            if season is None or start is None:
                continue
            end = row.get("episode_end") or start
            for episode in range(int(start), int(end) + 1):
                episode_files.setdefault((int(season), episode), []).append(row)

        yielded: set[tuple[int, int]] = set()
        for files in episode_files.values():
            for left, right in combinations(files, 2):
                pair = tuple(sorted((int(left["id"]), int(right["id"]))))
                if pair in yielded:
                    continue
                yielded.add(pair)
                yield left, right

    @staticmethod
    def _tv_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
        if left.get("season") is None or right.get("season") is None:
            return False
        if left["season"] != right["season"]:
            return False
        left_start = left.get("episode_start")
        right_start = right.get("episode_start")
        if left_start is None or right_start is None:
            return False
        left_end = left.get("episode_end") or left_start
        right_end = right.get("episode_end") or right_start
        return max(left_start, right_start) <= min(left_end, right_end)

    def _candidate(
        self, left: dict[str, Any], right: dict[str, Any],
        review: dict[str, Any], status: str,
    ) -> dict[str, Any]:
        hash_a = review.get("file_a_sha256")
        hash_b = review.get("file_b_sha256")
        verified = bool(hash_a and hash_b)
        exact = verified and hash_a == hash_b
        same_size = int(left.get("size_bytes") or 0) == int(right.get("size_bytes") or 0)
        runtime_a = float(left.get("runtime_seconds") or 0)
        runtime_b = float(right.get("runtime_seconds") or 0)
        runtime_close = bool(runtime_a and runtime_b and abs(runtime_a - runtime_b) <= 2)
        if exact:
            classification = "verified_exact"
            label = "Verified exact duplicate"
            explanation = "A full SHA-256 check confirmed that both files contain identical bytes."
        elif verified:
            classification = "alternate"
            label = "Alternate copies"
            explanation = (
                "Both files represent the same catalog item, but a full hash check "
                "confirmed that their bytes differ. They may be different encodes or editions."
            )
        elif same_size and runtime_close:
            classification = "likely"
            label = "Likely duplicate"
            explanation = (
                "The files represent the same catalog item and have the same size with "
                "nearly identical runtimes. Verify them before treating either as redundant."
            )
        else:
            classification = "alternate"
            label = "Possible alternate copy"
            explanation = (
                "The files represent the same catalog item, but their technical details "
                "differ. This may be intentional, such as a different encode or edition."
            )
        left_quality = _quality_score(left)
        right_quality = _quality_score(right)
        preferred = (
            left if left_quality > right_quality else
            right if right_quality > left_quality else None
        )
        alternate = right if preferred is left else left if preferred is right else None
        recovery_reasons: list[str] = []
        if preferred:
            preferred_pixels = int(preferred.get("width") or 0) * int(
                preferred.get("height") or 0
            )
            alternate_pixels = int(alternate.get("width") or 0) * int(
                alternate.get("height") or 0
            )
            if preferred_pixels > alternate_pixels:
                recovery_reasons.append(
                    f"Higher stored resolution ({_format_resolution(preferred)} versus "
                    f"{_format_resolution(alternate)})."
                )
            if int(preferred.get("bitrate") or 0) > int(alternate.get("bitrate") or 0):
                recovery_reasons.append("Higher stored bitrate.")
            preferred_range = str(preferred.get("dynamic_range") or "").upper()
            alternate_range = str(alternate.get("dynamic_range") or "").upper()
            if preferred_range and preferred_range != "SDR" and alternate_range == "SDR":
                recovery_reasons.append(
                    f"Includes {preferred_range} dynamic range while the other copy is SDR."
                )
        if left.get("root_id") != right.get("root_id"):
            recovery_reasons.append(
                "The copies are on different configured sources, so confirm which source "
                "is intended as primary storage before changing either file."
            )
        else:
            recovery_reasons.append(
                "Both copies are on the same configured source; this pair does not provide "
                "protection from a source-level storage failure."
            )
        if exact:
            recovery_reasons.insert(
                0, "A complete SHA-256 comparison confirmed identical file contents."
            )
        elif not verified:
            recovery_reasons.append(
                "The contents have not been hash-verified, so neither copy should be removed."
            )
        recommendation = (
            f"{preferred['filename']} has the stronger technical profile. "
            "Keep both if they are different editions; InfoMancer will not delete either file."
            if preferred else
            "The stored technical profiles are equivalent. Verify the file contents if you "
            "need to know whether they are byte-for-byte identical; InfoMancer will not delete either file."
        )
        removable = alternate
        if removable is None and exact:
            removable = right
        recoverable_bytes = (
            max(0, int(removable.get("size_bytes") or 0)) if removable else 0
        )
        return {
            "pair": f"{min(left['id'], right['id'])}-{max(left['id'], right['id'])}",
            "title_id": left["title_id"], "title_name": left["title_name"],
            "kind": left["kind"], "status": status,
            "classification": classification, "label": label,
            "explanation": explanation,
            "preferred_id": preferred["id"] if preferred else None,
            "recommendation": recommendation,
            "recovery_reasons": recovery_reasons,
            "recommended_keep": preferred["filename"] if preferred else "No preference yet",
            "safe_to_remove": False,
            "recoverable_bytes": recoverable_bytes,
            "verified_at": review.get("verified_at"),
            "hash_state": self._hash_state(left, right, exact, verified),
            "file_a": self._file_view(left), "file_b": self._file_view(right),
        }

    @staticmethod
    def _hash_state(left: dict[str, Any], right: dict[str, Any], exact: bool, verified: bool) -> str:
        if verified:
            return "verified_exact" if exact else "verified_different"
        states = {left.get("hash_status"), right.get("hash_status")}
        for state in ("running", "queued", "error"):
            if state in states:
                return state
        return "not_verified"

    @staticmethod
    def _file_view(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"], "filename": row["filename"], "path": row["path"],
            "root_id": row.get("root_id"),
            "season": row.get("season"),
            "episode_start": row.get("episode_start"),
            "episode_end": row.get("episode_end"),
            "size_bytes": int(row.get("size_bytes") or 0),
            "runtime_seconds": row.get("runtime_seconds"),
            "resolution": _format_resolution(row),
            "video_codec": row.get("video_codec") or "Not inspected",
            "audio_codec": row.get("audio_codec") or "Not inspected",
            "bitrate": row.get("bitrate"),
            "dynamic_range": row.get("dynamic_range") or "Not inspected",
            "container": row.get("container") or row.get("extension") or "Unknown",
            "root_label": row.get("root_label") or "Unlabeled source",
            "hash_status": row.get("hash_status") or "not_verified",
            "hash_error": row.get("hash_error") or "",
            "hashed_at": row.get("hashed_at"),
        }

    @staticmethod
    def recovery_opportunity(candidates: list[dict[str, Any]]) -> dict[str, int]:
        """Estimate reviewable savings once per file, never per comparison pair."""
        removable: dict[int, tuple[int, str]] = {}
        for candidate in candidates:
            classification = candidate.get("classification")
            if classification not in {"verified_exact", "likely"}:
                continue
            files = [candidate["file_a"], candidate["file_b"]]
            preferred_id = candidate.get("preferred_id")
            if preferred_id is not None:
                suggested = next(
                    (item for item in files if item["id"] != preferred_id), None
                )
            elif classification == "verified_exact":
                # Identical bytes have no quality winner; keep one and count the
                # other only as an estimate until the user chooses explicitly.
                suggested = max(files, key=lambda item: int(item["id"]))
            else:
                suggested = None
            if suggested is None:
                continue
            file_id = int(suggested["id"])
            previous = removable.get(file_id)
            if previous is None or classification == "verified_exact":
                removable[file_id] = (
                    max(0, int(suggested.get("size_bytes") or 0)), classification
                )
        exact = [value for value in removable.values() if value[1] == "verified_exact"]
        likely = [value for value in removable.values() if value[1] == "likely"]
        return {
            "bytes": sum(value[0] for value in removable.values()),
            "files": len(removable),
            "exact_bytes": sum(value[0] for value in exact),
            "exact_files": len(exact),
            "likely_bytes": sum(value[0] for value in likely),
            "likely_files": len(likely),
        }

    def decide(self, file_a_id: int, file_b_id: int, decision: str, user_id: int | None) -> bool:
        if decision not in DECISIONS:
            return False
        a_id, b_id = sorted((file_a_id, file_b_id))
        if a_id == b_id:
            return False
        with self.database.connect() as conn:
            rows = [dict(row) for row in conn.execute(
                "SELECT * FROM files WHERE id IN (?,?) ORDER BY id", (a_id, b_id)
            )]
            if len(rows) != 2:
                return False
            conn.execute(
                """INSERT INTO duplicate_reviews(
                     file_a_id,file_b_id,decision,file_a_signature,file_b_signature,
                     reviewed_by,updated_at
                   ) VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(file_a_id,file_b_id) DO UPDATE SET
                     decision=excluded.decision,
                     file_a_signature=excluded.file_a_signature,
                     file_b_signature=excluded.file_b_signature,
                     reviewed_by=excluded.reviewed_by,
                     updated_at=CURRENT_TIMESTAMP""",
                (a_id, b_id, decision, _signature(rows[0]), _signature(rows[1]), user_id),
            )
        return True

    def verify(self, file_a_id: int, file_b_id: int, user_id: int | None) -> str:
        a_id, b_id = sorted((file_a_id, file_b_id))
        with self.database.connect() as conn:
            rows = [dict(row) for row in conn.execute(
                "SELECT * FROM files WHERE id IN (?,?) ORDER BY id", (a_id, b_id)
            )]
        if len(rows) != 2:
            raise ValueError("Those files are no longer available in the catalog.")
        hashes = [self.hashes.hash_file(row["id"], force=True) for row in rows]
        with self.database.connect() as conn:
            # Hashing may refresh a stale catalog size/mtime. Store the
            # signatures that correspond to the bytes we actually verified.
            rows = [dict(row) for row in conn.execute(
                "SELECT * FROM files WHERE id IN (?,?) ORDER BY id", (a_id, b_id)
            )]
            conn.execute(
                """INSERT INTO duplicate_reviews(
                     file_a_id,file_b_id,decision,file_a_signature,file_b_signature,
                     file_a_sha256,file_b_sha256,verified_at,reviewed_by,updated_at
                   ) VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(file_a_id,file_b_id) DO UPDATE SET
                     decision='active',
                     file_a_signature=excluded.file_a_signature,
                     file_b_signature=excluded.file_b_signature,
                     file_a_sha256=excluded.file_a_sha256,
                     file_b_sha256=excluded.file_b_sha256,
                     verified_at=CURRENT_TIMESTAMP,reviewed_by=excluded.reviewed_by,
                     updated_at=CURRENT_TIMESTAMP""",
                (a_id, b_id, "active", _signature(rows[0]), _signature(rows[1]),
                 hashes[0], hashes[1], user_id),
            )
        return "exact" if hashes[0] == hashes[1] else "different"

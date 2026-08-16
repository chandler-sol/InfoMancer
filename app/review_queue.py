from __future__ import annotations

from collections import Counter
from typing import Any


SEVERITY_ORDER = {"critical": 0, "warning": 1, "information": 2}
BUCKET_ORDER = (
    "health", "matching", "missing", "duplicates", "metadata", "renames",
    "quality", "editions", "sources", "storage",
)
BUCKET_LABELS = {
    "health": "Health",
    "matching": "Matching",
    "missing": "Missing",
    "duplicates": "Duplicates",
    "metadata": "Metadata",
    "renames": "Renames",
    "quality": "Quality",
    "editions": "Editions",
    "sources": "Sources",
    "storage": "Storage",
}
RULE_BUCKETS = {
    "missing-episodes": "missing",
    "identity-confidence-low": "matching",
    "unmatched-title": "matching",
    "metadata-identifiers-missing": "matching",
    "duplicate-candidates": "duplicates",
    "duplicate-storage-recovery": "duplicates",
    "metadata-artwork-missing": "metadata",
    "metadata-credits-missing": "metadata",
    "metadata-episodes-incomplete": "metadata",
    "metadata-stale": "metadata",
    "quality-preference": "quality",
    "quality-consistency": "quality",
    "media-identity-unreviewed": "editions",
    "source-stale": "sources",
    "source-offline": "sources",
    "source-degraded": "sources",
    "technical-details-missing": "quality",
    "media-unreadable": "health",
}
DIRECT_DUPLICATE_RULES = {"duplicate-candidates", "duplicate-storage-recovery"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, dict):
        return ", ".join(f"{key}: {_text(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_text(item) for item in value)
    return str(value)


def _label(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").strip().title()


def _evidence_rows(evidence: dict[str, Any] | None) -> list[dict[str, str]]:
    return [
        {"label": _label(str(key)), "value": _text(value) or "Not recorded"}
        for key, value in (evidence or {}).items()
    ]


class ReviewQueue:
    """Normalize existing review signals into one read-only Workspace queue.

    The queue is an adapter over current sources of truth. It does not create a new
    review-state table, so Library Health feedback, duplicate decisions, and metadata
    jobs keep their existing ownership and security boundaries.
    """

    def __init__(self, database, mie, duplicates, rename_proposals=None) -> None:
        self.database = database
        self.mie = mie
        self.duplicates = duplicates
        self.rename_proposals = rename_proposals

    @staticmethod
    def _bucket(finding: dict[str, Any]) -> str:
        rule_key = str(finding.get("rule_key") or "")
        if rule_key in RULE_BUCKETS:
            return RULE_BUCKETS[rule_key]
        category = str(finding.get("category") or "health")
        return {
            "identity": "matching",
            "completeness": "metadata",
            "quality": "quality",
            "freshness": "metadata",
            "storage": "storage",
            "health": "health",
        }.get(category, "health")

    def _finding_item(self, finding: dict[str, Any]) -> dict[str, Any]:
        bucket = self._bucket(finding)
        title_id = finding.get("title_id")
        root_id = finding.get("root_id")
        affected = (
            finding.get("title_name") or finding.get("filename")
            or finding.get("root_label") or "Library"
        )
        item = {
            "key": f"finding:{finding['id']}",
            "source": "finding",
            "source_label": "Media Intelligence",
            "item_id": str(finding["id"]),
            "status": finding.get("status") or "active",
            "severity": finding.get("severity") or "information",
            "bucket": bucket,
            "bucket_label": BUCKET_LABELS.get(bucket, _label(bucket)),
            "summary": finding.get("summary") or "Review finding",
            "explanation": finding.get("explanation") or "",
            "recommendation": finding.get("recommendation") or "",
            "title_id": title_id,
            "title_name": finding.get("title_name") or "",
            "title_kind": finding.get("title_kind") or "",
            "root_id": root_id,
            "root_label": finding.get("root_label") or "",
            "affected": affected,
            "href": finding.get("href") or "/library",
            "review_label": finding.get("review_label") or "Open affected media",
            "last_seen_at": finding.get("last_seen_at") or "",
            "rule_key": finding.get("rule_key") or "",
            "evidence": finding.get("evidence") or {},
            "evidence_rows": _evidence_rows(finding.get("evidence") or {}),
            "files": [],
        }
        item["drawer_url"] = f"/review/items/finding/{item['item_id']}"
        return item

    def _duplicate_item(self, candidate: dict[str, Any]) -> dict[str, Any]:
        file_a = candidate["file_a"]
        file_b = candidate["file_b"]
        left, right = sorted((int(file_a["id"]), int(file_b["id"])))
        severity = "warning" if candidate.get("classification") in {"verified_exact", "likely"} else "information"
        evidence = {
            "classification": candidate.get("label"),
            "recommended keep": candidate.get("recommended_keep"),
            "recoverable bytes": candidate.get("recoverable_bytes"),
            "verification": candidate.get("hash_state"),
            "source A": file_a.get("root_label"),
            "source B": file_b.get("root_label"),
        }
        item = {
            "key": f"duplicate:{left}:{right}",
            "source": "duplicate",
            "source_label": "Duplicate Review",
            "item_id": f"{left}:{right}",
            "status": candidate.get("status") or "active",
            "severity": severity,
            "bucket": "duplicates",
            "bucket_label": "Duplicates",
            "summary": f"{candidate.get('label')}: {candidate.get('title_name')}",
            "explanation": candidate.get("explanation") or "",
            "recommendation": candidate.get("recommendation") or "",
            "title_id": candidate.get("title_id"),
            "title_name": candidate.get("title_name") or "",
            "title_kind": candidate.get("kind") or "",
            "root_id": None,
            "root_label": "",
            "affected": candidate.get("title_name") or "Library",
            "href": "/duplicates",
            "review_label": "Open Duplicate Review",
            "last_seen_at": candidate.get("verified_at") or "",
            "rule_key": "duplicate-pair",
            "evidence": evidence,
            "evidence_rows": _evidence_rows(evidence),
            "files": [file_a, file_b],
            "file_a_id": left,
            "file_b_id": right,
        }
        item["drawer_url"] = f"/review/items/duplicate/{left}:{right}"
        return item

    def _rename_items(self, status: str) -> list[dict[str, Any]]:
        if self.rename_proposals is None:
            return []
        items = []
        for row in self.rename_proposals.list_for_review(status):
            proposal_status = row["status"]
            review_status = (
                "dismissed" if proposal_status == "dismissed"
                else "resolved" if proposal_status in {"resolved", "applied", "stale"}
                else "active"
            )
            blocked = proposal_status == "blocked"
            evidence = {
                "current filename": row["source_name"],
                "proposed filename": row["destination_name"],
                "source path": row["source_path"],
                "destination path": row["destination_path"],
                "snapshot state": proposal_status,
                "validation": row["reason"],
            }
            item = {
                "key": f"rename:{row['id']}", "source": "rename",
                "source_label": "Rename Snapshot", "item_id": str(row["id"]),
                "status": review_status, "proposal_status": proposal_status,
                "severity": "warning" if blocked else "information",
                "bucket": "renames", "bucket_label": "Renames",
                "summary": (
                    f"Rename blocked: {row['source_name']}" if blocked
                    else f"Rename {row['source_name']}"
                ),
                "explanation": (
                    row["reason"] if blocked else
                    "This proposal was generated in the background and saved as a filesystem snapshot. Review does not stat the file again until you explicitly apply it."
                ),
                "recommendation": (
                    "Resolve the collision or missing-file condition, then refresh rename proposals."
                    if blocked else "Review the proposed filename, then apply it or dismiss the suggestion."
                ),
                "title_id": row["title_id"], "title_name": row["title_name"],
                "title_kind": row["title_kind"], "root_id": row["root_id"],
                "root_label": row["root_label"] or "", "affected": row["title_name"],
                "href": f"/titles/{row['title_id']}", "review_label": "Open title",
                "last_seen_at": row["updated_at"] or row["last_checked_at"] or "",
                "rule_key": "persisted-rename-proposal", "evidence": evidence,
                "evidence_rows": _evidence_rows(evidence), "files": [],
            }
            item["drawer_url"] = f"/review/items/rename/{row['id']}"
            items.append(item)
        return items

    def _metadata_items(self) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT q.title_id,q.status,q.provider,q.error,q.requested_at,
                          q.completed_at,t.kind,
                          COALESCE(NULLIF(t.metadata_title,''),t.title) title_name,
                          t.root_id,r.label root_label
                   FROM metadata_refresh_queue q
                   JOIN titles t ON t.id=q.title_id
                   JOIN roots r ON r.id=t.root_id
                   WHERE q.status='failed'
                   ORDER BY COALESCE(q.completed_at,q.requested_at) DESC,q.title_id"""
            ).fetchall()
        items = []
        for row in rows:
            evidence = {
                "provider": row["provider"] or "Default provider",
                "requested at": row["requested_at"],
                "completed at": row["completed_at"],
                "error": row["error"] or "No provider error was stored",
            }
            item = {
                "key": f"metadata:{row['title_id']}",
                "source": "metadata",
                "source_label": "Metadata Queue",
                "item_id": str(row["title_id"]),
                "status": "active",
                "severity": "warning",
                "bucket": "metadata",
                "bucket_label": "Metadata",
                "summary": f"Metadata refresh failed for {row['title_name']}",
                "explanation": row["error"] or "The most recent metadata refresh did not complete successfully.",
                "recommendation": "Open the title, verify its provider identity, and retry the metadata refresh when the provider is available.",
                "title_id": row["title_id"],
                "title_name": row["title_name"],
                "title_kind": row["kind"],
                "root_id": row["root_id"],
                "root_label": row["root_label"] or "",
                "affected": row["title_name"],
                "href": f"/titles/{row['title_id']}",
                "review_label": "Open title",
                "last_seen_at": row["completed_at"] or row["requested_at"] or "",
                "rule_key": "metadata-refresh-failed",
                "evidence": evidence,
                "evidence_rows": _evidence_rows(evidence),
                "files": [],
            }
            item["drawer_url"] = f"/review/items/metadata/{row['title_id']}"
            items.append(item)
        return items

    def _all_items(self, *, status: str, include_librarian: bool) -> list[dict[str, Any]]:
        status = status if status in {"active", "dismissed", "resolved"} else "active"
        items = []
        for finding in self.mie.findings(status=status):
            if finding.get("rule_key") in DIRECT_DUPLICATE_RULES:
                if include_librarian:
                    continue
                # Members cannot open Duplicate Review, so omit duplicate cleanup work.
                continue
            items.append(self._finding_item(finding))
        if include_librarian:
            items.extend(self._rename_items(status))
        if status == "active" and include_librarian:
            items.extend(self._metadata_items())
            items.extend(self._duplicate_item(item) for item in self.duplicates.candidates(status="active"))
        return items

    def view(
        self, *, status: str = "active", severity: str = "", bucket: str = "",
        q: str = "", sort: str = "priority", include_librarian: bool = False,
    ) -> dict[str, Any]:
        status = status if status in {"active", "dismissed", "resolved"} else "active"
        severity = severity if severity in SEVERITY_ORDER else ""
        bucket = bucket if bucket in BUCKET_LABELS else ""
        sort = sort if sort in {"priority", "newest", "title"} else "priority"
        query = q.strip().casefold()[:200]
        all_items = self._all_items(status=status, include_librarian=include_librarian)
        counts = Counter(item["severity"] for item in all_items)
        bucket_counts = Counter(item["bucket"] for item in all_items)
        items = all_items
        if severity:
            items = [item for item in items if item["severity"] == severity]
        if bucket:
            items = [item for item in items if item["bucket"] == bucket]
        if query:
            items = [item for item in items if query in " ".join((
                item["summary"], item["explanation"], item["recommendation"],
                item["affected"], item["source_label"], item["bucket_label"],
            )).casefold()]
        bucket_rank = {key: index for index, key in enumerate(BUCKET_ORDER)}
        if sort == "newest":
            items.sort(key=lambda item: (item["last_seen_at"], item["summary"].casefold()), reverse=True)
        elif sort == "title":
            items.sort(key=lambda item: (item["affected"].casefold(), SEVERITY_ORDER[item["severity"]]))
        else:
            items.sort(key=lambda item: (
                SEVERITY_ORDER[item["severity"]],
                bucket_rank.get(item["bucket"], 99),
                item["affected"].casefold(), item["summary"].casefold(),
            ))
        status_counts = {}
        for review_status in ("active", "dismissed", "resolved"):
            if review_status == status:
                status_counts[review_status] = len(all_items)
            else:
                status_counts[review_status] = len(self._all_items(
                    status=review_status, include_librarian=include_librarian,
                ))
        summary = self.mie.summary()
        return {
            "items": items,
            "visible_count": len(items),
            "total": len(all_items),
            "counts": {
                "total": len(all_items),
                "critical": int(counts["critical"]),
                "warning": int(counts["warning"]),
                "information": int(counts["information"]),
            },
            "bucket_counts": {key: int(bucket_counts[key]) for key in BUCKET_ORDER},
            "buckets": [
                {"key": key, "label": BUCKET_LABELS[key], "count": int(bucket_counts[key])}
                for key in BUCKET_ORDER if bucket_counts[key]
            ],
            "status_counts": status_counts,
            "last_analyzed_at": summary.get("last_analyzed_at"),
            "overall_score": summary.get("overall_score"),
            "filters": {"status": status, "severity": severity, "bucket": bucket, "q": q.strip()[:200], "sort": sort},
        }

    def get_item(self, source: str, item_id: str, *, include_librarian: bool) -> dict[str, Any] | None:
        if source == "finding" and item_id.isdigit():
            target = int(item_id)
            for status in ("active", "dismissed", "resolved"):
                for finding in self.mie.findings(status=status):
                    if int(finding["id"]) == target:
                        if finding.get("rule_key") in DIRECT_DUPLICATE_RULES:
                            return None
                        return self._finding_item(finding)
            return None
        if source == "duplicate" and include_librarian:
            parts = item_id.split(":", 1)
            if len(parts) != 2 or not all(part.isdigit() for part in parts):
                return None
            pair = tuple(sorted((int(parts[0]), int(parts[1]))))
            for candidate in self.duplicates.candidates(status="active"):
                ids = tuple(sorted((int(candidate["file_a"]["id"]), int(candidate["file_b"]["id"]))))
                if ids == pair:
                    return self._duplicate_item(candidate)
            return None
        if source == "metadata" and include_librarian and item_id.isdigit():
            target = f"metadata:{int(item_id)}"
            return next((item for item in self._metadata_items() if item["key"] == target), None)
        if source == "rename" and include_librarian and item_id.isdigit():
            target = int(item_id)
            for review_status in ("active", "dismissed", "resolved"):
                for item in self._rename_items(review_status):
                    if int(item["item_id"]) == target:
                        return item
            return None
        return None

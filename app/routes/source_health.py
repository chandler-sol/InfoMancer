from __future__ import annotations

from urllib.parse import quote_plus

from fastapi import APIRouter, Depends

from ..access import require_librarian
from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    HTTPException = ctx.get("HTTPException")
    db = ctx.live("db")
    json = ctx.live("json")
    scan_jobs = ctx.live("scan_jobs")

    def librarian_get(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.get(path, dependencies=dependencies, **kwargs)

    def latest_scan_context(root_id: int) -> dict:
        with scan_jobs._value().get(root_id, {}) if False else _nullcontext():
            pass
        try:
            active = dict(scan_jobs.get(root_id, {}) or {})
        except Exception:
            active = {}
        if active.get("scan_id"):
            return active

        with db.connect() as conn:
            rows = conn.execute(
                """SELECT context_json FROM event_logs
                   WHERE category='scan' ORDER BY id DESC LIMIT 500"""
            ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["context_json"] or "{}")
            except (TypeError, ValueError):
                continue
            try:
                payload_root = int(payload.get("root_id"))
            except (TypeError, ValueError):
                continue
            if payload_root == root_id:
                return payload
        return {}

    @librarian_get("/api/sources/{root_id}/health-details")
    def source_health_details(root_id: int):
        with db.connect() as conn:
            root = conn.execute(
                """SELECT r.*, COUNT(DISTINCT t.id) title_count, COUNT(f.id) file_count
                   FROM roots r
                   LEFT JOIN titles t ON t.root_id=r.id
                   LEFT JOIN files f ON f.title_id=t.id
                   WHERE r.id=? GROUP BY r.id""",
                (root_id,),
            ).fetchone()
            if not root:
                raise HTTPException(404, "That source no longer exists.")

            finding = conn.execute(
                """SELECT id,rule_key,severity,summary,explanation,recommendation,
                          evidence_json,last_seen_at
                   FROM mie_findings
                   WHERE root_id=? AND status='active'
                     AND rule_key IN ('source-offline','source-degraded')
                   ORDER BY CASE severity
                              WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                            id DESC LIMIT 1""",
                (root_id,),
            ).fetchone()

        scan_context = latest_scan_context(root_id)
        scan_id = str(scan_context.get("scan_id") or "").strip()
        affected = []
        if scan_id and str(scan_context.get("source_status") or "") == "degraded":
            with db.connect() as conn:
                rows = conn.execute(
                    """SELECT f.id file_id,f.title_id,f.filename,f.path,
                              t.kind,COALESCE(NULLIF(t.metadata_title,''),t.title) title_name
                       FROM files f JOIN titles t ON t.id=f.title_id
                       WHERE t.root_id=? AND f.seen_scan!=?
                       ORDER BY title_name COLLATE NOCASE,f.filename COLLATE NOCASE
                       LIMIT 100""",
                    (root_id, scan_id),
                ).fetchall()
                for row in rows:
                    affected.append({
                        "file_id": row["file_id"],
                        "title_id": row["title_id"],
                        "title_name": row["title_name"],
                        "kind": row["kind"],
                        "filename": row["filename"],
                        "path": row["path"],
                        "summary": "Not seen during the latest guarded scan",
                        "href": f"/titles/{row['title_id']}",
                    })

        # File-level health findings can explain an affected item more precisely
        # than the source-level guard message. Merge them without duplicating files.
        with db.connect() as conn:
            health_rows = conn.execute(
                """SELECT mf.file_id,mf.summary,mf.explanation,
                          f.title_id,f.filename,f.path,t.kind,
                          COALESCE(NULLIF(t.metadata_title,''),t.title) title_name
                   FROM mie_findings mf
                   JOIN files f ON f.id=mf.file_id
                   JOIN titles t ON t.id=f.title_id
                   WHERE mf.root_id=? AND mf.status='active'
                     AND mf.category='health' AND mf.file_id IS NOT NULL
                   ORDER BY mf.id DESC LIMIT 100""",
                (root_id,),
            ).fetchall()
        by_file = {int(item["file_id"]): item for item in affected if item.get("file_id")}
        for row in health_rows:
            file_id = int(row["file_id"])
            if file_id in by_file:
                by_file[file_id]["summary"] = row["summary"] or by_file[file_id]["summary"]
                by_file[file_id]["explanation"] = row["explanation"] or ""
                continue
            item = {
                "file_id": file_id,
                "title_id": row["title_id"],
                "title_name": row["title_name"],
                "kind": row["kind"],
                "filename": row["filename"],
                "path": row["path"],
                "summary": row["summary"] or "Media health issue",
                "explanation": row["explanation"] or "",
                "href": f"/titles/{row['title_id']}",
            }
            affected.append(item)
            by_file[file_id] = item

        label = root["label"] or root["path"]
        status = str(root["health_status"] or "unknown")
        protected = int(root["guard_preserved_count"] or 0)
        known_files = int(root["file_count"] or root["last_file_count"] or 0)
        affected_total = protected if protected else len(affected)
        if status == "offline" and affected_total == 0:
            affected_total = known_files
        affected_total = max(affected_total, len(affected))

        finding_evidence = {}
        if finding:
            try:
                finding_evidence = json.loads(finding["evidence_json"] or "{}")
            except (TypeError, ValueError):
                finding_evidence = {}

        fallback_summary = {
            "offline": f"{label} is unavailable",
            "degraded": f"{label} returned an incomplete view",
            "healthy": f"{label} is healthy",
        }.get(status, f"{label} health is {status}")
        fallback_explanation = str(root["last_error"] or "").strip() or (
            "InfoMancer has not recorded a technical error for this source."
        )
        fallback_recommendation = (
            "Check the storage connection and permissions, then run Check connection."
            if status == "offline" else
            "Review the affected media below, then run a full source scan after the storage path is stable."
            if status == "degraded" else
            "No source-health action is currently required."
        )

        return {
            "root": {
                "id": root_id,
                "label": label,
                "path": root["path"],
                "kind": root["kind"],
                "status": status,
                "last_checked_at": root["last_checked_at"],
                "last_seen_at": root["last_seen_at"],
                "last_scanned_at": root["last_scanned_at"],
                "last_known_files": int(root["last_file_count"] or 0),
                "observed_files": int(root["last_observed_file_count"] or 0),
                "protected_files": protected,
            },
            "issue_count": max(1, affected_total) if status in {"offline", "degraded"} else len(affected),
            "summary": finding["summary"] if finding else fallback_summary,
            "explanation": finding["explanation"] if finding else fallback_explanation,
            "recommendation": finding["recommendation"] if finding else fallback_recommendation,
            "technical_detail": str(root["last_error"] or "").strip(),
            "evidence": finding_evidence,
            "affected": affected,
            "affected_total": affected_total,
            "affected_truncated": affected_total > len(affected),
            "review_url": f"/review?bucket=sources&q={quote_plus(str(label))}",
            "logs_url": f"/logs?category=scan&search={quote_plus(str(label))}",
        }

    return router, {"source_health_details": source_health_details}


class _nullcontext:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False

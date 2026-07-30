from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .db import Database


SEVERITIES = {"critical", "warning", "information"}
CATEGORIES = {"health", "identity", "completeness", "quality", "freshness"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class MediaIntelligenceEngine:
    """Explainable, read-only analysis over facts already stored in the catalog."""

    def __init__(self, database: Database):
        self.database = database

    def analyze(self) -> int:
        analyzed_at = _utc_now()
        candidates: list[dict[str, Any]] = []
        with self.database.connect() as conn:
            titles = {
                row["id"]: row for row in conn.execute(
                    """SELECT id,root_id,kind,title,metadata_title,tvdb_id,
                              tvdb_movie_id,tmdb_id,imdb_id
                       FROM titles"""
                )
            }

            for row in conn.execute(
                """SELECT f.id,f.title_id,f.filename,f.path,f.media_info_error,
                          t.root_id,t.title,t.metadata_title
                   FROM files f JOIN titles t ON t.id=f.title_id
                   WHERE COALESCE(f.media_info_error,'')!=''"""
            ):
                title_name = row["metadata_title"] or row["title"]
                plain_error = row["media_info_error"].partition(
                    "Technical details:"
                )[0].strip()
                candidates.append({
                    "fingerprint": f"media-unreadable:file:{row['id']}",
                    "rule_key": "media-unreadable",
                    "category": "health",
                    "severity": "critical",
                    "root_id": row["root_id"],
                    "title_id": row["title_id"],
                    "file_id": row["id"],
                    "summary": f"Media details could not be read for {row['filename']}",
                    "explanation": plain_error or (
                        "InfoMancer asked FFprobe to inspect this file, but the "
                        "container or media streams could not be read."
                    ),
                    "recommendation": (
                        "Confirm the storage location is available and try playing "
                        "the file. If it plays normally, reinspect it. If it does "
                        "not, restore or replace the file before attempting repairs."
                    ),
                    "evidence": {
                        "title": title_name,
                        "filename": row["filename"],
                        "path": row["path"],
                        "inspection_error": row["media_info_error"][:2000],
                    },
                })

            for title in titles.values():
                matched = (
                    bool(title["tvdb_id"]) if title["kind"] == "tv"
                    else bool(
                        title["tvdb_movie_id"]
                        or title["tmdb_id"]
                        or title["imdb_id"]
                    )
                )
                if matched:
                    continue
                name = title["metadata_title"] or title["title"]
                candidates.append({
                    "fingerprint": f"unmatched-title:title:{title['id']}",
                    "rule_key": "unmatched-title",
                    "category": "identity",
                    "severity": "warning",
                    "root_id": title["root_id"],
                    "title_id": title["id"],
                    "summary": f"{name} has not been matched",
                    "explanation": (
                        "InfoMancer only knows this title from its folder and "
                        "filename. Provider metadata has not confirmed its identity."
                    ),
                    "recommendation": (
                        "Review suggested matches and select the correct movie or "
                        "series before relying on metadata or rename recommendations."
                    ),
                    "evidence": {
                        "catalog_title": title["title"],
                        "kind": title["kind"],
                    },
                })

            missing_by_title: dict[int, list[Any]] = defaultdict(list)
            for episode in conn.execute(
                """SELECT e.id,e.title_id,e.season,e.episode,e.name,e.aired
                   FROM expected_episodes e
                   WHERE e.season>0
                     AND (e.aired IS NULL OR e.aired<=date('now'))
                     AND NOT EXISTS (
                       SELECT 1 FROM files f
                       WHERE f.title_id=e.title_id AND f.season=e.season
                         AND e.episode BETWEEN f.episode_start
                                           AND COALESCE(f.episode_end,f.episode_start)
                     )
                   ORDER BY e.title_id,e.season,e.episode"""
            ):
                missing_by_title[episode["title_id"]].append(episode)
            for title_id, episodes in missing_by_title.items():
                title = titles.get(title_id)
                if not title:
                    continue
                name = title["metadata_title"] or title["title"]
                codes = [
                    f"S{row['season']:02d}E{row['episode']:02d}"
                    for row in episodes[:8]
                ]
                candidates.append({
                    "fingerprint": f"missing-episodes:title:{title_id}",
                    "rule_key": "missing-episodes",
                    "category": "completeness",
                    "severity": "warning",
                    "root_id": title["root_id"],
                    "title_id": title_id,
                    "expected_episode_id": episodes[0]["id"],
                    "summary": (
                        f"{name} is missing {len(episodes)} aired "
                        f"episode{'s' if len(episodes) != 1 else ''}"
                    ),
                    "explanation": (
                        "The matched episode order contains aired regular episodes "
                        "whose season and episode numbers were not found in local files."
                    ),
                    "recommendation": (
                        "Review the missing-episode list. If the files exist, check "
                        "their names or episode order and rescan the series."
                    ),
                    "evidence": {
                        "missing_count": len(episodes),
                        "sample_episode_codes": codes,
                    },
                })

            for root in conn.execute(
                """SELECT r.id,r.label,r.path,r.last_scanned_at,
                          SUM(CASE WHEN f.id IS NOT NULL
                                    AND f.media_info_at IS NULL
                                    AND COALESCE(f.media_info_error,'')=''
                                   THEN 1 ELSE 0 END) missing_details
                   FROM roots r
                   LEFT JOIN titles t ON t.root_id=r.id
                   LEFT JOIN files f ON f.title_id=t.id
                   WHERE r.enabled=1
                   GROUP BY r.id"""
            ):
                label = root["label"] or root["path"]
                missing_details = int(root["missing_details"] or 0)
                if missing_details:
                    candidates.append({
                        "fingerprint": f"technical-details:root:{root['id']}",
                        "rule_key": "technical-details-missing",
                        "category": "quality",
                        "severity": "information",
                        "root_id": root["id"],
                        "summary": (
                            f"{missing_details:,} files in {label} have not been inspected"
                        ),
                        "explanation": (
                            "Runtime, resolution, codecs, bitrate, container, and "
                            "HDR/SDR details have not yet been collected for these files."
                        ),
                        "recommendation": (
                            "Run media inspection for new or missing files when the "
                            "storage location is available."
                        ),
                        "evidence": {
                            "root": label,
                            "path": root["path"],
                            "file_count": missing_details,
                        },
                    })
                stale = conn.execute(
                    """SELECT CASE
                         WHEN ? IS NULL THEN 1
                         WHEN datetime(?) < datetime('now','-1 day') THEN 1
                         ELSE 0 END""",
                    (root["last_scanned_at"], root["last_scanned_at"]),
                ).fetchone()[0]
                if stale:
                    candidates.append({
                        "fingerprint": f"source-stale:root:{root['id']}",
                        "rule_key": "source-stale",
                        "category": "freshness",
                        "severity": "information",
                        "root_id": root["id"],
                        "summary": f"{label} needs a fresh scan",
                        "explanation": (
                            "This source has never been scanned or its most recent "
                            "scan is more than one day old."
                        ),
                        "recommendation": (
                            "Scan the source to discover new media and update files "
                            "that were moved or removed."
                        ),
                        "evidence": {
                            "root": label,
                            "path": root["path"],
                            "last_scanned_at": root["last_scanned_at"],
                        },
                    })

            conn.execute(
                """UPDATE mie_findings
                   SET status='resolved',resolved_at=?
                   WHERE status IN ('active','dismissed')""",
                (analyzed_at,),
            )
            for finding in candidates:
                conn.execute(
                    """INSERT INTO mie_findings(
                         fingerprint,rule_key,category,severity,root_id,title_id,
                         file_id,expected_episode_id,summary,explanation,
                         recommendation,evidence_json,status,first_seen_at,last_seen_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'active',?,?)
                       ON CONFLICT(fingerprint) DO UPDATE SET
                         rule_key=excluded.rule_key,
                         category=excluded.category,
                         severity=excluded.severity,
                         root_id=excluded.root_id,
                         title_id=excluded.title_id,
                         file_id=excluded.file_id,
                         expected_episode_id=excluded.expected_episode_id,
                         summary=excluded.summary,
                         explanation=excluded.explanation,
                         recommendation=excluded.recommendation,
                         evidence_json=excluded.evidence_json,
                         status=CASE WHEN mie_findings.dismissed_at IS NOT NULL
                                     THEN 'dismissed' ELSE 'active' END,
                         last_seen_at=excluded.last_seen_at,
                         resolved_at=NULL""",
                    (
                        finding["fingerprint"], finding["rule_key"],
                        finding["category"], finding["severity"],
                        finding.get("root_id"), finding.get("title_id"),
                        finding.get("file_id"),
                        finding.get("expected_episode_id"),
                        finding["summary"], finding["explanation"],
                        finding["recommendation"], _json(finding["evidence"]),
                        analyzed_at, analyzed_at,
                    ),
                )
            conn.execute(
                """INSERT INTO mie_analysis_state(id,last_analyzed_at,finding_count)
                   VALUES (1,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     last_analyzed_at=excluded.last_analyzed_at,
                     finding_count=excluded.finding_count""",
                (analyzed_at, len(candidates)),
            )
        return len(candidates)

    def summary(self) -> dict[str, Any]:
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT
                     SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) active,
                     SUM(CASE WHEN status='active' AND severity='critical'
                              THEN 1 ELSE 0 END) critical,
                     SUM(CASE WHEN status='active' AND severity='warning'
                              THEN 1 ELSE 0 END) warning,
                     SUM(CASE WHEN status='dismissed' THEN 1 ELSE 0 END) dismissed
                   FROM mie_findings"""
            ).fetchone()
            state = conn.execute(
                "SELECT last_analyzed_at FROM mie_analysis_state WHERE id=1"
            ).fetchone()
        return {
            "active": int(row["active"] or 0),
            "critical": int(row["critical"] or 0),
            "warning": int(row["warning"] or 0),
            "dismissed": int(row["dismissed"] or 0),
            "last_analyzed_at": state["last_analyzed_at"] if state else None,
        }

    def findings(
        self, *, status: str = "active", severity: str = "",
        category: str = "",
    ) -> list[dict[str, Any]]:
        status = status if status in {"active", "dismissed", "resolved"} else "active"
        conditions = ["mf.status=?"]
        params: list[Any] = [status]
        if severity in SEVERITIES:
            conditions.append("mf.severity=?")
            params.append(severity)
        if category in CATEGORIES:
            conditions.append("mf.category=?")
            params.append(category)
        with self.database.connect() as conn:
            rows = conn.execute(
                f"""SELECT mf.*,COALESCE(t.metadata_title,t.title) title_name,
                           t.kind title_kind,f.filename,r.label root_label,r.path root_path
                    FROM mie_findings mf
                    LEFT JOIN titles t ON t.id=mf.title_id
                    LEFT JOIN files f ON f.id=mf.file_id
                    LEFT JOIN roots r ON r.id=mf.root_id
                    WHERE {' AND '.join(conditions)}
                    ORDER BY
                      CASE mf.severity WHEN 'critical' THEN 1
                           WHEN 'warning' THEN 2 ELSE 3 END,
                      mf.category,mf.last_seen_at DESC,mf.id DESC""",
                params,
            ).fetchall()
        findings = []
        for row in rows:
            finding = dict(row)
            try:
                finding["evidence"] = json.loads(finding["evidence_json"] or "{}")
            except (TypeError, ValueError):
                finding["evidence"] = {}
            finding["href"] = self.finding_href(finding)
            findings.append(finding)
        return findings

    @staticmethod
    def finding_href(finding: dict[str, Any]) -> str:
        if finding["rule_key"] == "missing-episodes" and finding.get("title_id"):
            return (
                f"/titles/{finding['title_id']}?show_missing=1#missing-panel"
            )
        if finding["rule_key"] == "unmatched-title" and finding.get("title_id"):
            return f"/titles/{finding['title_id']}/tvdb"
        if finding.get("file_id") and finding.get("title_id"):
            return f"/titles/{finding['title_id']}"
        if finding["rule_key"] == "technical-details-missing":
            return "/settings/system#media-information"
        if finding.get("root_id"):
            return "/sources"
        if finding.get("title_id"):
            return f"/titles/{finding['title_id']}"
        return "/library"

    def dismiss(self, finding_id: int, user_id: int | None) -> bool:
        with self.database.connect() as conn:
            cursor = conn.execute(
                """UPDATE mie_findings
                   SET status='dismissed',dismissed_at=CURRENT_TIMESTAMP,
                       dismissed_by=?
                   WHERE id=? AND status='active'""",
                (user_id if user_id and user_id > 0 else None, finding_id),
            )
        return bool(cursor.rowcount)

    def restore(self, finding_id: int) -> bool:
        with self.database.connect() as conn:
            cursor = conn.execute(
                """UPDATE mie_findings
                   SET status='active',dismissed_at=NULL,dismissed_by=NULL
                   WHERE id=? AND status='dismissed'""",
                (finding_id,),
            )
        return bool(cursor.rowcount)

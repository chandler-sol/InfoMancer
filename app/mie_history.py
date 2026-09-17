from __future__ import annotations

import threading
from collections import Counter, defaultdict
from contextlib import contextmanager
from typing import Any

from .mie import DEFAULT_CALIBRATION, MediaIntelligenceEngine


class _TransactionalDatabase:
    """Expose one existing connection through the Database.connect contract."""

    def __init__(self, database, connection) -> None:
        self._database = database
        self._connection = connection

    @contextmanager
    def connect(self):
        yield self._connection

    def __getattr__(self, name: str):
        return getattr(self._database, name)


class MediaIntelligenceHistoryEngine(MediaIntelligenceEngine):
    """Add deterministic 0.9 lifecycle/history persistence to the existing MIE.

    The underlying analyzer remains the source of truth for finding generation. This
    wrapper serializes analysis runs within the single InfoMancer runtime and executes
    the base analysis plus lifecycle/history finalization in one database transaction.
    """

    def __init__(self, database):
        super().__init__(database)
        self._history_lock = threading.Lock()

    def analyze(self) -> int:
        with self._history_lock:
            with self.database.connect() as conn:
                previous_active = {
                    str(row["fingerprint"])
                    for row in conn.execute(
                        "SELECT fingerprint FROM mie_findings WHERE status='active'"
                    )
                }

                transactional_engine = MediaIntelligenceEngine(
                    _TransactionalDatabase(self.database, conn)
                )
                candidate_count = transactional_engine.analyze()

                run = conn.execute(
                    "SELECT id FROM mie_analysis_runs ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if not run:
                    return candidate_count
                run_id = int(run["id"])

                active_findings = [
                    dict(row)
                    for row in conn.execute(
                        """SELECT fingerprint,severity,title_id
                           FROM mie_findings WHERE status='active'"""
                    )
                ]
                current_active = {
                    str(finding["fingerprint"]) for finding in active_findings
                }
                opened = len(current_active - previous_active)
                resolved = len(previous_active - current_active)
                conn.execute(
                    """UPDATE mie_analysis_runs
                       SET opened_findings=?,resolved_findings=?
                       WHERE id=?""",
                    (opened, resolved, run_id),
                )

                calibration = dict(DEFAULT_CALIBRATION)
                calibration_row = conn.execute(
                    "SELECT * FROM mie_calibration WHERE id=1"
                ).fetchone()
                if calibration_row:
                    calibration.update({
                        key: calibration_row[key] for key in DEFAULT_CALIBRATION
                    })
                weights = {
                    "critical": int(calibration["critical_weight"]),
                    "warning": int(calibration["warning_weight"]),
                    "information": int(calibration["information_weight"]),
                }

                findings_by_title: dict[int, Counter] = defaultdict(Counter)
                for finding in active_findings:
                    if finding["title_id"] is not None:
                        findings_by_title[int(finding["title_id"])][
                            str(finding["severity"])
                        ] += 1

                title_ids = [
                    int(row["id"])
                    for row in conn.execute("SELECT id FROM titles ORDER BY id")
                ]
                conn.execute(
                    "DELETE FROM mie_title_health_snapshots WHERE run_id=?",
                    (run_id,),
                )
                if title_ids:
                    conn.executemany(
                        """INSERT INTO mie_title_health_snapshots(
                             run_id,title_id,score,critical_count,
                             warning_count,information_count
                           ) VALUES (?,?,?,?,?,?)""",
                        [
                            (
                                run_id,
                                title_id,
                                max(
                                    0,
                                    100 - sum(
                                        counts[level] * weight
                                        for level, weight in weights.items()
                                    ),
                                ),
                                counts["critical"],
                                counts["warning"],
                                counts["information"],
                            )
                            for title_id in title_ids
                            for counts in [findings_by_title[title_id]]
                        ],
                    )

            return candidate_count

    def titles_needing_attention(self, limit: int = 12) -> list[dict[str, Any]]:
        """Return the lowest-health titles from the latest run with health snapshots."""
        normalized_limit = max(1, min(int(limit), 100))
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT h.*,COALESCE(t.metadata_title,t.title) title_name,t.kind
                   FROM mie_title_health_snapshots h
                   JOIN titles t ON t.id=h.title_id
                   WHERE h.run_id=(SELECT MAX(run_id) FROM mie_title_health_snapshots)
                     AND (h.critical_count+h.warning_count+h.information_count)>0
                   ORDER BY h.score ASC,h.critical_count DESC,
                            h.warning_count DESC,title_name COLLATE NOCASE
                   LIMIT ?""",
                (normalized_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def title_health_history(
        self, title_id: int, limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return recent persisted health snapshots for one title."""
        normalized_limit = max(1, min(int(limit), 50))
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT h.*,r.analyzed_at,r.opened_findings,r.resolved_findings
                   FROM mie_title_health_snapshots h
                   JOIN mie_analysis_runs r ON r.id=h.run_id
                   WHERE h.title_id=?
                   ORDER BY h.run_id DESC LIMIT ?""",
                (int(title_id), normalized_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def attention_overview(
        self, limit: int = 12, history_limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Return attention titles enriched with a compact, explainable score trend."""
        overview: list[dict[str, Any]] = []
        for title in self.titles_needing_attention(limit):
            item = dict(title)
            history = self.title_health_history(
                int(item["title_id"]), limit=history_limit,
            )
            current_score = int(item["score"])
            previous_score = (
                int(history[1]["score"]) if len(history) > 1 else None
            )
            score_delta = (
                current_score - previous_score if previous_score is not None else None
            )
            if score_delta is None:
                trend = "new"
            elif score_delta > 0:
                trend = "improving"
            elif score_delta < 0:
                trend = "worsening"
            else:
                trend = "stable"
            item.update({
                "history": history,
                "recent_scores": [
                    int(row["score"]) for row in reversed(history)
                ],
                "previous_score": previous_score,
                "score_delta": score_delta,
                "trend": trend,
            })
            overview.append(item)
        return overview

    def summary(self) -> dict[str, Any]:
        """Extend the existing Library Health summary with title-level review context."""
        summary = super().summary()
        summary["attention_titles"] = self.attention_overview()
        return summary

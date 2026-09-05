from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ..access import require_librarian
from .context import RouteContext


def build_router(ctx: RouteContext):
    """Own Bulk Match review GETs without the legacy 50-row pagination."""
    router = APIRouter()
    db = ctx.live("db")
    templates = ctx.live("templates")
    movie_match_job = ctx.live("movie_match_job")
    movie_match_lock = ctx.live("movie_match_lock")
    tv_match_job = ctx.live("tv_match_job")
    tv_match_lock = ctx.live("tv_match_lock")

    def librarian_get(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.get(path, dependencies=dependencies, **kwargs)

    @librarian_get("/shows/bulk-match", response_class=HTMLResponse)
    def bulk_tv_match_review(
        request: Request, review: bool = False, selected: bool = False,
    ):
        with tv_match_lock:
            job = dict(tv_match_job)
        selected_ids = (
            job.get("title_ids", [])
            if selected and job.get("mode") == "selected"
            else []
        )
        direct_selection = bool(selected_ids)

        with db.connect() as conn:
            available = conn.execute(
                """SELECT t.*, r.label root_label, r.path root_path
                   FROM titles t JOIN roots r ON r.id=t.root_id
                   WHERE t.kind='tv' AND t.tvdb_id IS NULL
                   ORDER BY COALESCE(t.metadata_title,t.title) COLLATE NOCASE"""
            ).fetchall()
            cached_count = conn.execute(
                """SELECT COUNT(*) FROM tv_match_suggestions s
                   JOIN titles t ON t.id=s.title_id
                   WHERE t.kind='tv' AND t.tvdb_id IS NULL"""
            ).fetchone()[0]

            suggestion_rows = []
            if review and direct_selection:
                placeholders = ",".join("?" for _ in selected_ids)
                suggestion_rows = conn.execute(
                    f"""SELECT t.*, r.label root_label, r.path root_path,
                               s.title_id suggestion_id, s.candidate_json,
                               s.confidence_score, s.confidence_label,
                               s.result_count, s.exact, s.error analysis_error
                        FROM titles t JOIN roots r ON r.id=t.root_id
                        LEFT JOIN tv_match_suggestions s ON s.title_id=t.id
                        WHERE t.kind='tv' AND t.tvdb_id IS NULL
                          AND t.id IN ({placeholders})
                        ORDER BY COALESCE(t.metadata_title,t.title) COLLATE NOCASE""",
                    selected_ids,
                ).fetchall()
                cached_count = len(selected_ids)
            elif review:
                suggestion_rows = conn.execute(
                    """SELECT t.*, r.label root_label, r.path root_path,
                              s.title_id suggestion_id, s.candidate_json,
                              s.confidence_score, s.confidence_label,
                              s.result_count, s.exact, s.error analysis_error
                       FROM tv_match_suggestions s
                       JOIN titles t ON t.id=s.title_id
                       JOIN roots r ON r.id=t.root_id
                       WHERE t.kind='tv' AND t.tvdb_id IS NULL
                       ORDER BY s.analyzed_at,
                                COALESCE(t.metadata_title,t.title) COLLATE NOCASE"""
                ).fetchall()

        suggestions = [
            {
                "title": row,
                "candidate": (
                    json.loads(row["candidate_json"])
                    if row["candidate_json"] else None
                ),
                "confidence": (
                    {
                        "score": row["confidence_score"],
                        "label": row["confidence_label"],
                    }
                    if row["confidence_score"] is not None else None
                ),
                "exact": bool(row["exact"]),
                "result_count": row["result_count"],
                "error": row["analysis_error"],
                "pending": row["suggestion_id"] is None,
            }
            for row in suggestion_rows
        ]
        return templates.TemplateResponse(request, "bulk_tv_match.html", {
            "shows": available,
            "available_count": len(available),
            "suggestions": suggestions,
            "analyzed": review,
            "cached_count": cached_count,
            "job": job,
            "direct_selection": direct_selection,
            "message": request.query_params.get("message", ""),
        })

    @librarian_get("/movies/bulk-match", response_class=HTMLResponse)
    def bulk_movie_match_review(
        request: Request, review: bool = False, selected: bool = False,
    ):
        with movie_match_lock:
            job = dict(movie_match_job)
        selected_ids = (
            job.get("title_ids", [])
            if selected and job.get("mode") == "selected"
            else []
        )
        direct_selection = bool(selected_ids)

        with db.connect() as conn:
            available = conn.execute(
                """SELECT t.*, r.label root_label, r.path root_path
                   FROM titles t JOIN roots r ON r.id=t.root_id
                   WHERE t.kind='movie' AND t.tvdb_movie_id IS NULL
                   ORDER BY COALESCE(t.metadata_title,t.title) COLLATE NOCASE"""
            ).fetchall()
            cached_count = conn.execute(
                """SELECT COUNT(*) FROM movie_match_suggestions s
                   JOIN titles t ON t.id=s.title_id
                   WHERE t.kind='movie'"""
            ).fetchone()[0]
            unanalyzed_count = conn.execute(
                """SELECT COUNT(*) FROM titles t
                   LEFT JOIN movie_match_suggestions s ON s.title_id=t.id
                   WHERE t.kind='movie' AND t.tvdb_movie_id IS NULL
                     AND s.title_id IS NULL"""
            ).fetchone()[0]
            no_result_count = conn.execute(
                """SELECT COUNT(*) FROM movie_match_suggestions s
                   JOIN titles t ON t.id=s.title_id
                   WHERE t.kind='movie' AND t.tvdb_movie_id IS NULL
                     AND s.candidate_json IS NULL"""
            ).fetchone()[0]

            suggestion_rows = []
            if review and direct_selection:
                placeholders = ",".join("?" for _ in selected_ids)
                suggestion_rows = conn.execute(
                    f"""SELECT t.*, r.label root_label, r.path root_path,
                               s.title_id suggestion_id, s.candidate_json,
                               s.confidence_score, s.confidence_label,
                               s.result_count, s.exact, s.error analysis_error
                        FROM titles t JOIN roots r ON r.id=t.root_id
                        LEFT JOIN movie_match_suggestions s ON s.title_id=t.id
                        WHERE t.kind='movie' AND t.tvdb_movie_id IS NULL
                          AND t.tmdb_id IS NULL AND t.imdb_id IS NULL
                          AND t.id IN ({placeholders})
                        ORDER BY COALESCE(t.metadata_title,t.title) COLLATE NOCASE""",
                    selected_ids,
                ).fetchall()
                cached_count = len(selected_ids)
            elif review:
                suggestion_rows = conn.execute(
                    """SELECT t.*, r.label root_label, r.path root_path,
                              s.title_id suggestion_id, s.candidate_json,
                              s.confidence_score, s.confidence_label,
                              s.result_count, s.exact, s.error analysis_error
                       FROM movie_match_suggestions s
                       JOIN titles t ON t.id=s.title_id
                       JOIN roots r ON r.id=t.root_id
                       WHERE t.kind='movie'
                       ORDER BY s.analyzed_at,
                                COALESCE(t.metadata_title,t.title) COLLATE NOCASE"""
                ).fetchall()

        suggestions = []
        for row in suggestion_rows:
            candidate = (
                json.loads(row["candidate_json"])
                if row["candidate_json"] else None
            )
            confidence = None
            if row["confidence_score"] is not None:
                confidence = {
                    "score": row["confidence_score"],
                    "label": row["confidence_label"],
                }
            suggestions.append({
                "title": row,
                "candidate": candidate,
                "confidence": confidence,
                "exact": bool(row["exact"]),
                "result_count": row["result_count"],
                "error": row["analysis_error"],
                "pending": row["suggestion_id"] is None,
            })

        return templates.TemplateResponse(request, "bulk_movie_match.html", {
            "movies": available,
            "available_count": len(available),
            "unanalyzed_count": unanalyzed_count,
            "suggestions": suggestions,
            "analyzed": review,
            "error": "",
            "cached_count": cached_count,
            "no_result_count": no_result_count,
            "job": job,
            "direct_selection": direct_selection,
        })

    return router, {
        "bulk_tv_match_review": bulk_tv_match_review,
        "bulk_movie_match_review": bulk_movie_match_review,
    }

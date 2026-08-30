from fastapi import APIRouter, Depends

from ..access import require_librarian
from .context import RouteContext


def build_router(ctx: RouteContext):
    """Full-queue bulk match review routes.

    Bulk analysis already operates on the complete selected title set. These routes
    intentionally keep the review/apply surface aligned with that job scope instead
    of slicing completed work into legacy 50-title pages.
    """
    router = APIRouter()
    Form = ctx.get("Form")
    HTMLResponse = ctx.get("HTMLResponse")
    Request = ctx.get("Request")
    TVDBError = ctx.get("TVDBError")
    db = ctx.live("db")
    json = ctx.live("json")
    movie_match_job = ctx.live("movie_match_job")
    movie_match_lock = ctx.live("movie_match_lock")
    redirect = ctx.live("redirect")
    store_movie_match = ctx.live("store_movie_match")
    store_tv_match = ctx.live("store_tv_match")
    templates = ctx.live("templates")
    tv_match_job = ctx.live("tv_match_job")
    tv_match_lock = ctx.live("tv_match_lock")

    def librarian_get(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.get(path, dependencies=dependencies, **kwargs)

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

    @librarian_get("/shows/bulk-match", response_class=HTMLResponse)
    def bulk_tv_match_review(request: Request, review: bool = False, selected: bool = False):
        with tv_match_lock:
            job = dict(tv_match_job)
        selected_ids = job.get("title_ids", []) if selected and job.get("mode") == "selected" else []
        direct_selection = bool(selected_ids)
        with db.connect() as conn:
            available = conn.execute(
                """SELECT t.*, r.label root_label, r.path root_path
                   FROM titles t JOIN roots r ON r.id=t.root_id
                   WHERE t.kind='tv' AND t.tvdb_id IS NULL
                   ORDER BY COALESCE(t.metadata_title,t.title) COLLATE NOCASE"""
            ).fetchall()
            cached_count = conn.execute(
                """SELECT COUNT(*) FROM tv_match_suggestions s JOIN titles t ON t.id=s.title_id
                   WHERE t.kind='tv' AND t.tvdb_id IS NULL"""
            ).fetchone()[0]
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
            else:
                suggestion_rows = conn.execute(
                    """SELECT t.*, r.label root_label, r.path root_path,
                              s.title_id suggestion_id, s.candidate_json,
                              s.confidence_score, s.confidence_label,
                              s.result_count, s.exact, s.error analysis_error
                       FROM tv_match_suggestions s JOIN titles t ON t.id=s.title_id
                       JOIN roots r ON r.id=t.root_id WHERE t.kind='tv' AND t.tvdb_id IS NULL
                       ORDER BY s.analyzed_at, COALESCE(t.metadata_title,t.title) COLLATE NOCASE"""
                ).fetchall() if review else []
        suggestions = []
        for row in suggestion_rows:
            suggestions.append({
                "title": row,
                "candidate": json.loads(row["candidate_json"]) if row["candidate_json"] else None,
                "confidence": ({"score": row["confidence_score"], "label": row["confidence_label"]}
                               if row["confidence_score"] is not None else None),
                "exact": bool(row["exact"]), "result_count": row["result_count"],
                "error": row["analysis_error"], "pending": row["suggestion_id"] is None,
            })
        return templates.TemplateResponse(request, "bulk_tv_match.html", {
            "shows": available, "available_count": len(available), "suggestions": suggestions,
            "analyzed": review, "cached_count": cached_count, "job": job,
            "direct_selection": direct_selection,
            "message": request.query_params.get("message", ""),
        })

    @librarian_post("/shows/bulk-match")
    def bulk_tv_match_apply(matches: list[str] = Form(default=[]), selected_scope: str = Form("")):
        applied = failed = 0
        for value in matches:
            try:
                title_id, series_id = (int(part) for part in value.split(":", 1))
                store_tv_match(title_id, series_id)
                with db.connect() as conn:
                    conn.execute("DELETE FROM tv_match_suggestions WHERE title_id=?", (title_id,))
                applied += 1
            except (ValueError, TVDBError):
                failed += 1
        message = f"Matched {applied} TV series"
        if failed:
            message += f"; {failed} failed"
        destination = "/shows/bulk-match?review=true&selected=true" if selected_scope else "/shows/bulk-match?review=true"
        return redirect(destination, message)

    @librarian_get("/movies/bulk-match", response_class=HTMLResponse)
    def bulk_movie_match_review(request: Request, review: bool = False, selected: bool = False):
        with movie_match_lock:
            job = dict(movie_match_job)
        selected_ids = job.get("title_ids", []) if selected and job.get("mode") == "selected" else []
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
                   JOIN titles t ON t.id=s.title_id WHERE t.kind='movie'"""
            ).fetchone()[0]
            unanalyzed_count = conn.execute(
                """SELECT COUNT(*) FROM titles t
                   LEFT JOIN movie_match_suggestions s ON s.title_id=t.id
                   WHERE t.kind='movie' AND t.tvdb_movie_id IS NULL AND s.title_id IS NULL"""
            ).fetchone()[0]
            no_result_count = conn.execute(
                """SELECT COUNT(*) FROM movie_match_suggestions s
                   JOIN titles t ON t.id=s.title_id
                   WHERE t.kind='movie' AND t.tvdb_movie_id IS NULL
                     AND s.candidate_json IS NULL"""
            ).fetchone()[0]
            suggestion_rows = []
            if review:
                if direct_selection:
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
                else:
                    suggestion_rows = conn.execute(
                        """SELECT t.*, r.label root_label, r.path root_path,
                                  s.title_id suggestion_id, s.candidate_json,
                                  s.confidence_score, s.confidence_label,
                                  s.result_count, s.exact, s.error analysis_error
                           FROM movie_match_suggestions s
                           JOIN titles t ON t.id=s.title_id
                           JOIN roots r ON r.id=t.root_id
                           WHERE t.kind='movie'
                           ORDER BY s.analyzed_at, COALESCE(t.metadata_title,t.title) COLLATE NOCASE"""
                    ).fetchall()
        suggestions = []
        for row in suggestion_rows:
            candidate = json.loads(row["candidate_json"]) if row["candidate_json"] else None
            confidence = None
            if row["confidence_score"] is not None:
                confidence = {"score": row["confidence_score"], "label": row["confidence_label"]}
            suggestions.append({
                "title": row, "candidate": candidate, "confidence": confidence,
                "exact": bool(row["exact"]), "result_count": row["result_count"],
                "error": row["analysis_error"], "pending": row["suggestion_id"] is None,
            })
        return templates.TemplateResponse(request, "bulk_movie_match.html", {
            "movies": available, "available_count": len(available),
            "unanalyzed_count": unanalyzed_count, "suggestions": suggestions,
            "analyzed": review, "error": "", "cached_count": cached_count,
            "no_result_count": no_result_count, "job": job,
            "direct_selection": direct_selection,
        })

    @librarian_post("/movies/bulk-match")
    def bulk_movie_match_apply(matches: list[str] = Form(default=[]), selected_scope: str = Form("")):
        applied = failed = 0
        for value in matches:
            try:
                title_id, movie_id = (int(part) for part in value.split(":", 1))
                store_movie_match(title_id, movie_id)
                with db.connect() as conn:
                    conn.execute("DELETE FROM movie_match_suggestions WHERE title_id=?", (title_id,))
                applied += 1
            except (ValueError, TVDBError):
                failed += 1
        message = f"Matched {applied} movies"
        if failed:
            message += f"; {failed} failed"
        destination = "/movies/bulk-match?review=true&selected=true" if selected_scope else "/movies/bulk-match?review=true"
        return redirect(destination, message)

    return router

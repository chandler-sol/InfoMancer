from fastapi import APIRouter, Depends, Response

from ..access import require_librarian
from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    db = ctx.live("db")
    json = ctx.live("json")
    movie_match_job = ctx.live("movie_match_job")
    movie_match_lock = ctx.live("movie_match_lock")

    @router.get(
        "/api/movies/bulk-match/progress",
        dependencies=[Depends(require_librarian)],
    )
    def bulk_movie_match_progress(response: Response, after: int = 0) -> dict:
        """Return only suggestions saved since the caller's last processed index."""
        response.headers["Cache-Control"] = "no-store"
        with movie_match_lock:
            job = dict(movie_match_job)

        title_ids: list[int] = []
        for value in job.get("title_ids") or []:
            try:
                title_ids.append(int(value))
            except (TypeError, ValueError):
                continue
        title_ids = list(dict.fromkeys(title_ids))
        processed = max(0, min(int(job.get("processed") or 0), len(title_ids)))
        after = max(0, min(int(after or 0), processed))
        changed_title_ids = title_ids[after:processed]

        items: list[dict] = []
        if changed_title_ids:
            placeholders = ",".join("?" for _ in changed_title_ids)
            with db.connect() as conn:
                rows = conn.execute(
                    f"""SELECT t.id title_id,
                               COALESCE(t.metadata_title,t.title) library_title,
                               s.title_id suggestion_id,s.candidate_json,
                               s.confidence_score,s.confidence_label,
                               s.result_count,s.exact,s.error
                        FROM titles t
                        LEFT JOIN movie_match_suggestions s ON s.title_id=t.id
                        WHERE t.kind='movie' AND t.id IN ({placeholders})""",
                    tuple(changed_title_ids),
                ).fetchall()
            by_id = {int(row["title_id"]): row for row in rows}
            for title_id in changed_title_ids:
                row = by_id.get(title_id)
                if not row or row["suggestion_id"] is None:
                    continue
                raw_candidate = None
                if row["candidate_json"]:
                    try:
                        decoded = json.loads(row["candidate_json"])
                    except (TypeError, ValueError):
                        decoded = None
                    if isinstance(decoded, dict):
                        raw_candidate = decoded

                candidate = None
                if raw_candidate:
                    candidate = {
                        "id": str(
                            raw_candidate.get("tvdb_id")
                            or raw_candidate.get("id")
                            or ""
                        ),
                        "name": str(
                            raw_candidate.get("name")
                            or raw_candidate.get("title")
                            or ""
                        ),
                        "year": str(raw_candidate.get("year") or "")[:4],
                        "image_url": str(raw_candidate.get("image_url") or ""),
                        "possible_match": bool(raw_candidate.get("_possible_match")),
                        "search_query": str(raw_candidate.get("_search_query") or ""),
                    }
                items.append({
                    "title_id": title_id,
                    "library_title": str(row["library_title"] or ""),
                    "candidate": candidate,
                    "confidence_score": row["confidence_score"],
                    "confidence_label": str(row["confidence_label"] or ""),
                    "result_count": int(row["result_count"] or 0),
                    "exact": bool(row["exact"]),
                    "error": str(row["error"] or ""),
                })

        return {
            "status": str(job.get("status") or "idle"),
            "processed": processed,
            "total": int(job.get("total") or 0),
            "matched": int(job.get("matched") or 0),
            "errors": int(job.get("errors") or 0),
            "items": items,
        }

    return router, {"bulk_movie_match_progress": bulk_movie_match_progress}

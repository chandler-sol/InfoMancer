import json
from urllib.parse import urlencode

from fastapi import APIRouter, Depends

from ..access import require_librarian
from .context import RouteContext


BULK_MOVIE_RETURNS = {
    "bulk-movie": "/movies/bulk-match?review=true",
    "bulk-movie-selected": "/movies/bulk-match?review=true&selected=true",
}


def build_router(ctx: RouteContext):
    router = APIRouter()
    Form = ctx.get("Form")
    HTTPException = ctx.get("HTTPException")
    TVDBError = ctx.get("TVDBError")
    db = ctx.live("db")
    match_confidence = ctx.live("match_confidence")
    match_success_redirect = ctx.live("match_success_redirect")
    redirect = ctx.live("redirect")
    store_movie_match = ctx.live("store_movie_match")
    tvdb = ctx.live("tvdb")

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

    def bulk_return(match_origin: str, title_id: int) -> str:
        base = BULK_MOVIE_RETURNS.get(match_origin, "")
        return f"{base}#bulk-title-{title_id}" if base else ""

    def bulk_search_path(title_id: int, match_origin: str, query: str = "") -> str:
        return_to = BULK_MOVIE_RETURNS.get(match_origin, "")
        params = {"from": match_origin, "return_to": return_to}
        if query.strip():
            params["q"] = query.strip()
        return f"/titles/{title_id}/tvdb?{urlencode(params)}"

    def cache_bulk_movie_choice(
        title_id: int, movie_id: int, *, search_query: str = "",
        result_count: int = 1,
    ) -> None:
        """Save an explicit manual choice as a Bulk Match suggestion, not a match.

        Bulk Match has one deliberate commit point: Apply selected matches. A manual
        correction made from its review screen therefore replaces the cached analyzer
        suggestion and returns to review without mutating the title's provider IDs.
        """
        with db.connect() as conn:
            title = conn.execute(
                "SELECT id,kind,title,year,tvdb_movie_id FROM titles WHERE id=?",
                (title_id,),
            ).fetchone()
        if not title:
            raise ValueError("Title not found")
        if title["kind"] != "movie":
            raise ValueError("Manual movie matching is only available for movie titles")
        if title["tvdb_movie_id"] is not None:
            raise ValueError("That movie has already been matched")

        candidate = dict(tvdb.movie(movie_id) or {})
        if not candidate:
            raise ValueError("That TVDB movie could not be loaded")
        provider_id = candidate.get("tvdb_id") or candidate.get("id") or movie_id
        candidate["tvdb_id"] = provider_id
        candidate.setdefault("id", provider_id)
        candidate["image_url"] = candidate.get("image_url") or candidate.get("image") or ""
        candidate["_manual_choice"] = True
        candidate["_search_query"] = search_query.strip() or title["title"]
        confidence = match_confidence(title["title"], title["year"], candidate)
        try:
            stored_result_count = max(1, min(int(result_count), 500))
        except (TypeError, ValueError):
            stored_result_count = 1

        with db.connect() as conn:
            conn.execute(
                """INSERT INTO movie_match_suggestions
                   (title_id, candidate_json, confidence_score, confidence_label,
                    result_count, exact, error, analyzed_at)
                   VALUES (?, ?, ?, ?, ?, 1, '', CURRENT_TIMESTAMP)
                   ON CONFLICT(title_id) DO UPDATE SET
                     candidate_json=excluded.candidate_json,
                     confidence_score=excluded.confidence_score,
                     confidence_label=excluded.confidence_label,
                     result_count=excluded.result_count,
                     exact=1,
                     error='',
                     analyzed_at=CURRENT_TIMESTAMP""",
                (
                    title_id,
                    json.dumps(candidate),
                    confidence["score"],
                    confidence["label"],
                    stored_result_count,
                ),
            )

    @librarian_post("/titles/{title_id}/movie/{movie_id}")
    def match_movie(
        title_id: int, movie_id: int, return_to: str = Form(""),
        match_origin: str = Form(""), search_query: str = Form(""),
        result_count: int = Form(1),
    ):
        bulk_path = bulk_return(match_origin, title_id)
        if bulk_path:
            try:
                cache_bulk_movie_choice(
                    title_id, movie_id, search_query=search_query,
                    result_count=result_count,
                )
            except (TVDBError, ValueError) as exc:
                return redirect(
                    bulk_search_path(title_id, match_origin, search_query), str(exc)
                )
            return redirect(bulk_path)

        try:
            provider = store_movie_match(title_id, movie_id)
        except (TVDBError, ValueError) as exc:
            return redirect(f"/titles/{title_id}", str(exc))
        return match_success_redirect(
            title_id, f"Movie matched using {provider}", return_to, match_origin,
        )

    @librarian_post("/titles/{title_id}/movie-manual")
    def match_movie_manual(
        title_id: int, tvdb_reference: str = Form(""), return_to: str = Form(""),
        match_origin: str = Form(""), search_query: str = Form(""),
        result_count: int = Form(1),
    ):
        with db.connect() as conn:
            title = conn.execute(
                "SELECT id,kind FROM titles WHERE id=?", (title_id,)
            ).fetchone()
        if not title:
            raise HTTPException(404, "Title not found")
        if title["kind"] != "movie":
            return redirect(
                f"/titles/{title_id}/tvdb",
                "Manual movie matching is only available for movie titles",
            )

        try:
            movie_id = tvdb.movie_id_from_reference(tvdb_reference)
            bulk_path = bulk_return(match_origin, title_id)
            if bulk_path:
                cache_bulk_movie_choice(
                    title_id, movie_id, search_query=search_query,
                    result_count=result_count,
                )
                return redirect(bulk_path)
            provider = store_movie_match(title_id, movie_id)
        except (TVDBError, ValueError) as exc:
            if BULK_MOVIE_RETURNS.get(match_origin):
                return redirect(
                    bulk_search_path(title_id, match_origin, search_query), str(exc)
                )
            return redirect(f"/titles/{title_id}/tvdb", str(exc))

        return match_success_redirect(
            title_id, f"Movie matched using {provider}", return_to, match_origin,
        )

    return router, {
        "match_movie": match_movie,
        "match_movie_manual": match_movie_manual,
    }

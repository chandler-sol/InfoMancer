from fastapi import APIRouter, Depends

from ..access import require_librarian
from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    Form = ctx.get("Form")
    HTTPException = ctx.get("HTTPException")
    TVDBError = ctx.get("TVDBError")
    db = ctx.live("db")
    match_success_redirect = ctx.live("match_success_redirect")
    redirect = ctx.live("redirect")
    store_movie_match = ctx.live("store_movie_match")
    tvdb = ctx.live("tvdb")

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

    @librarian_post("/titles/{title_id}/movie-manual")
    def match_movie_manual(
        title_id: int, tvdb_reference: str = Form(""), return_to: str = Form(""),
        match_origin: str = Form(""),
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
            provider = store_movie_match(title_id, movie_id)
        except (TVDBError, ValueError) as exc:
            return redirect(f"/titles/{title_id}/tvdb", str(exc))

        return match_success_redirect(
            title_id, f"Movie matched using {provider}", return_to, match_origin,
        )

    return router, {
        "match_movie_manual": match_movie_manual,
    }

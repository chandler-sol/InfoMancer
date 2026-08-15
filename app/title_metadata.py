from __future__ import annotations

from typing import Callable

from .db import Database
from .tvdb import TVDBClient


class TitleMetadataService:
    """Explicit metadata enrichment that may contact providers and write catalog data."""

    def __init__(
        self, database: Database, tvdb: TVDBClient, *,
        poster_from: Callable, plex_movie_ids: Callable,
        localized_title: Callable, match_confidence: Callable,
    ):
        self.database = database
        self.tvdb = tvdb
        self.poster_from = poster_from
        self.plex_movie_ids = plex_movie_ids
        self.localized_title = localized_title
        self.match_confidence = match_confidence

    def enrich(self, title_id: int) -> bool:
        with self.database.connect() as conn:
            title = conn.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
        if not title:
            raise ValueError("Title not found")
        if title["kind"] == "tv":
            return self._enrich_tv(title)
        return self._enrich_movie(title)

    def _enrich_tv(self, title) -> bool:
        if not title["tvdb_id"]:
            return False
        if all((title["poster_url"], title["imdb_id"], title["metadata_title_language"], title["overview"])):
            return False
        series = self.tvdb.series(title["tvdb_id"])
        poster_url = self.poster_from(series)
        _tmdb_id, imdb_id = self.plex_movie_ids(series)
        metadata_title, title_language = self.localized_title(series, title["metadata_title"])
        overview = str(series.get("overview") or "").strip()
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE titles SET
                   poster_url=COALESCE(NULLIF(?, ''), poster_url),
                   imdb_id=COALESCE(NULLIF(?, ''), imdb_id),
                   metadata_title=COALESCE(NULLIF(?, ''), metadata_title),
                   metadata_title_language=?,
                   overview=COALESCE(NULLIF(?, ''), overview),
                   imdb_checked_at=CURRENT_TIMESTAMP,
                   metadata_refreshed_at=CURRENT_TIMESTAMP,
                   metadata_provider='TVDB', updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (poster_url, imdb_id, metadata_title, title_language or "preserved", overview, title["id"]),
            )
        return True

    def _enrich_movie(self, title) -> bool:
        if title["overview"]:
            return False
        movie = None
        movie_id = title["tvdb_movie_id"]
        if not movie_id and (title["tmdb_id"] or title["imdb_id"]):
            candidates = self.tvdb.search_movies(
                title["metadata_title"] or title["title"],
                title["metadata_year"] or title["year"],
            )
            for candidate in candidates:
                confidence = self.match_confidence(
                    title["metadata_title"] or title["title"],
                    title["metadata_year"] or title["year"], candidate,
                )
                if not (confidence["exact_title"] and confidence["exact_year"]):
                    continue
                candidate_id = candidate.get("tvdb_id") or candidate.get("id")
                if not candidate_id:
                    continue
                candidate_movie = self.tvdb.movie(candidate_id)
                tmdb_id, imdb_id = self.plex_movie_ids(candidate_movie)
                same_external_id = (
                    bool(title["tmdb_id"] and tmdb_id == str(title["tmdb_id"]))
                    or bool(title["imdb_id"] and imdb_id == title["imdb_id"])
                )
                if same_external_id:
                    movie_id = candidate_id
                    movie = candidate_movie
                    break
        if not movie_id:
            return False
        if movie is None:
            movie = self.tvdb.movie(movie_id)
        overview = str((movie or {}).get("overview") or "").strip()
        if not overview:
            return False
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE titles SET overview=?, tvdb_movie_id=COALESCE(tvdb_movie_id, ?),
                   metadata_refreshed_at=CURRENT_TIMESTAMP, metadata_provider='TVDB',
                   updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (overview, movie_id, title["id"]),
            )
        return True

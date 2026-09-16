from __future__ import annotations

import threading

from fastapi import APIRouter

from ..maintenance_gate import APPLICATION_MAINTENANCE_GATE
from .context import RouteContext


_RECOVERY_BLOCKING_THREAD_NAMES = {
    "infomancer-rename-proposals",
}


def build_router(ctx: RouteContext):
    """Keep database-writing background work coordinated with recovery."""
    router = APIRouter()

    base_other_background_work_running = ctx.get("_other_background_work_running")
    base_run_imdb_genre_sync = ctx.get("run_imdb_genre_sync")
    base_run_movie_match_analysis = ctx.get("run_movie_match_analysis")
    base_run_tv_match_analysis = ctx.get("run_tv_match_analysis")

    imdb_genre_job = ctx.live("imdb_genre_job")
    imdb_genre_lock = ctx.live("imdb_genre_lock")
    movie_match_job = ctx.live("movie_match_job")
    movie_match_lock = ctx.live("movie_match_lock")
    tv_match_job = ctx.live("tv_match_job")
    tv_match_lock = ctx.live("tv_match_lock")
    record_event = ctx.live("record_event")

    def _pause(job, lock, label: str) -> None:
        with lock:
            job.update({
                "status": "paused",
                "current": "",
                "error": f"Exclusive maintenance started before {label} could run.",
            })

    def _fail(job, lock, label: str, exc: Exception) -> None:
        detail = str(exc)[:1000]
        with lock:
            job.update({"status": "error", "current": "", "error": detail})
        record_event(
            "metadata",
            f"{label.capitalize()} stopped because of an unexpected error.",
            level="error",
            detail=detail,
            context={"operation": label.replace(" ", "_")},
        )

    def leased_run_movie_match_analysis(title_ids: list[int]) -> None:
        with APPLICATION_MAINTENANCE_GATE.operation_lease() as admitted:
            if not admitted:
                _pause(movie_match_job, movie_match_lock, "movie match analysis")
                return
            try:
                base_run_movie_match_analysis(title_ids)
            except Exception as exc:
                _fail(movie_match_job, movie_match_lock, "movie match analysis", exc)

    def leased_run_tv_match_analysis(title_ids: list[int]) -> None:
        with APPLICATION_MAINTENANCE_GATE.operation_lease() as admitted:
            if not admitted:
                _pause(tv_match_job, tv_match_lock, "TV match analysis")
                return
            try:
                base_run_tv_match_analysis(title_ids)
            except Exception as exc:
                _fail(tv_match_job, tv_match_lock, "TV match analysis", exc)

    def leased_run_imdb_genre_sync(
        title_ids: list[int] | None = None,
        episode_ids: list[int] | None = None,
        scope_label: str = "",
    ) -> None:
        with APPLICATION_MAINTENANCE_GATE.operation_lease() as admitted:
            if not admitted:
                _pause(imdb_genre_job, imdb_genre_lock, "metadata refresh")
                return
            try:
                base_run_imdb_genre_sync(title_ids, episode_ids, scope_label)
            except Exception as exc:
                _fail(imdb_genre_job, imdb_genre_lock, "metadata refresh", exc)

    def coordinated_background_work_running() -> bool:
        """Include legacy local worker threads that are not represented in the registry.

        Rename-proposal refresh owns a route-local job dictionary, so recovery cannot
        inspect that state directly. Its named thread remains alive through proposal
        writes and terminal event logging, making thread lifetime the conservative
        quiescence boundary until that legacy worker is moved into BackgroundCoordinator.
        """
        if base_other_background_work_running():
            return True
        return any(
            worker.is_alive() and worker.name in _RECOVERY_BLOCKING_THREAD_NAMES
            for worker in threading.enumerate()
        )

    return router, {
        "run_movie_match_analysis": leased_run_movie_match_analysis,
        "run_tv_match_analysis": leased_run_tv_match_analysis,
        "run_imdb_genre_sync": leased_run_imdb_genre_sync,
        "_other_background_work_running": coordinated_background_work_running,
    }

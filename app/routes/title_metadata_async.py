from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..access import require_librarian
from .context import RouteContext


def build_router(ctx: RouteContext):
    """Keep one-title metadata refresh interactive instead of running the bulk IMDb importer."""
    router = APIRouter()
    db = ctx.live("db")
    tvdb = ctx.live("tvdb")
    poster_from = ctx.live("poster_from")
    localized_tvdb_title = ctx.live("localized_tvdb_title")
    imdb_genre_job = ctx.live("imdb_genre_job")
    imdb_genre_lock = ctx.live("imdb_genre_lock")
    redirect = ctx.live("redirect")

    def async_request(request: Request) -> bool:
        return (
            request.headers.get("x-infomancer-async") == "1"
            or "application/json" in request.headers.get("accept", "")
        )

    def external_ids(record: dict) -> tuple[str, str]:
        imdb_id = ""
        tmdb_id = ""
        for remote in record.get("remoteIds") or record.get("remote_ids") or []:
            if not isinstance(remote, dict):
                continue
            source = str(remote.get("sourceName") or remote.get("source_name") or "").lower()
            remote_id = str(remote.get("id") or "").strip()
            if not remote_id:
                continue
            if "imdb" in source:
                imdb_id = remote_id
            elif "movie database" in source or "themoviedb" in source or source == "tmdb":
                tmdb_id = remote_id
        return imdb_id, tmdb_id

    def genre_names(record: dict) -> str:
        names: list[str] = []
        for genre in record.get("genres") or []:
            name = str(genre.get("name") if isinstance(genre, dict) else genre).strip()
            if name and name not in names:
                names.append(name)
        return ",".join(names)

    def credit_role(character: dict) -> str:
        people_type = character.get("peopleType") or character.get("people_type") or ""
        if isinstance(people_type, dict):
            people_type = people_type.get("name") or people_type.get("typeName") or ""
        normalized = str(people_type).strip().lower()
        if "director" in normalized:
            return "director"
        if "writer" in normalized or "screenplay" in normalized:
            return "writer"
        if (
            "actor" in normalized
            or "actress" in normalized
            or "guest" in normalized
            or "cast" in normalized
        ):
            return "actor"
        return ""

    def title_credits(record: dict) -> list[tuple[str, str, str, int]]:
        credits: list[tuple[str, str, str, int]] = []
        seen: set[tuple[str, str]] = set()
        for index, character in enumerate(record.get("characters") or [], start=1):
            if not isinstance(character, dict):
                continue
            role = credit_role(character)
            person_name = str(
                character.get("personName")
                or character.get("person_name")
                or ""
            ).strip()
            person_id = character.get("peopleId") or character.get("people_id")
            if not role or not person_name or not person_id:
                continue
            provider_person_id = f"tvdb:{person_id}"
            key = (provider_person_id, role)
            if key in seen:
                continue
            seen.add(key)
            try:
                billing_order = int(character.get("sort") or index)
            except (TypeError, ValueError):
                billing_order = index
            credits.append((provider_person_id, person_name, role, billing_order))
        return credits

    def safe_year(value) -> int | None:
        try:
            year = int(str(value or "").strip())
        except (TypeError, ValueError):
            return None
        return year if 1800 <= year <= 3000 else None

    def update_task(**values) -> None:
        with imdb_genre_lock:
            # Keep this one-title job on the local surface. task-widget.js explicitly
            # ignores ui_scope=local, while the Metadata modal owns the interaction.
            imdb_genre_job.update(values)
            imdb_genre_job["ui_scope"] = "local"

    def run_targeted_refresh(title_id: int, label: str) -> dict:
        started = time.monotonic()
        try:
            with db.connect() as conn:
                title = conn.execute(
                    """SELECT id,kind,title,metadata_title,metadata_year,year,
                              tvdb_id,tvdb_movie_id,poster_url,overview
                       FROM titles WHERE id=?""",
                    (title_id,),
                ).fetchone()
                if not title:
                    raise ValueError("Title no longer exists")
                conn.execute(
                    """UPDATE metadata_refresh_queue
                       SET status='running',started_at=CURRENT_TIMESTAMP,
                           completed_at=NULL,attempts=attempts+1,error=''
                       WHERE title_id=?""",
                    (title_id,),
                )

            update_task(
                status="running", phase="provider",
                title_ids=[title_id], scope_label=label,
            )
            if title["kind"] == "movie":
                provider_id = int(title["tvdb_movie_id"] or 0)
                if not provider_id:
                    raise ValueError(
                        "This movie needs a TVDB match before it can be refreshed quickly."
                    )
                record = tvdb.movie(provider_id)
            else:
                provider_id = int(title["tvdb_id"] or 0)
                if not provider_id:
                    raise ValueError(
                        "This series needs a TVDB match before it can be refreshed quickly."
                    )
                record = tvdb.series(provider_id)
            if not record:
                raise ValueError("TVDB returned no metadata for this title")

            update_task(
                status="running", phase="details",
                title_ids=[title_id], scope_label=label,
            )
            display_title, title_language = localized_tvdb_title(
                record, str(title["metadata_title"] or title["title"] or "")
            )
            imdb_id, tmdb_id = external_ids(record)
            genres = genre_names(record)
            poster_url = str(poster_from(record) or record.get("image") or "").strip()
            overview = str(record.get("overview") or "").strip()
            metadata_year = safe_year(record.get("year"))
            status_record = record.get("status") or {}
            metadata_status = str(
                status_record.get("name")
                if isinstance(status_record, dict)
                else status_record
            ).strip()

            update_task(
                status="running", phase="credits",
                title_ids=[title_id], scope_label=label,
            )
            credits = title_credits(record)

            update_task(
                status="running", phase="save",
                title_ids=[title_id], scope_label=label,
            )
            with db.connect() as conn:
                conn.execute(
                    """UPDATE titles SET
                           poster_url=CASE WHEN ?!='' THEN ? ELSE poster_url END,
                           metadata_title=CASE WHEN ?!='' THEN ? ELSE metadata_title END,
                           metadata_title_language=CASE WHEN ?!='' THEN ? ELSE metadata_title_language END,
                           metadata_year=COALESCE(?,metadata_year,year),
                           metadata_status=CASE WHEN ?!='' THEN ? ELSE metadata_status END,
                           overview=CASE WHEN ?!='' THEN ? ELSE overview END,
                           genres=CASE WHEN ?!='' THEN ? ELSE genres END,
                           imdb_id=CASE WHEN ?!='' THEN ? ELSE imdb_id END,
                           imdb_checked_at=CASE WHEN ?!='' THEN CURRENT_TIMESTAMP ELSE imdb_checked_at END,
                           tmdb_id=CASE WHEN ?!='' THEN ? ELSE tmdb_id END,
                           metadata_refreshed_at=CURRENT_TIMESTAMP,
                           metadata_refresh_error='',metadata_provider='TVDB',
                           updated_at=CURRENT_TIMESTAMP
                       WHERE id=?""",
                    (
                        poster_url, poster_url,
                        display_title, display_title,
                        title_language, title_language,
                        metadata_year,
                        metadata_status, metadata_status,
                        overview, overview,
                        genres, genres,
                        imdb_id, imdb_id,
                        imdb_id,
                        tmdb_id, tmdb_id,
                        title_id,
                    ),
                )
                # TVDB extended records include cast and crew as character/talent
                # records. Replace credits only when TVDB supplied usable credits so a
                # sparse provider response cannot erase previously good IMDb credits.
                if credits:
                    conn.execute(
                        "DELETE FROM title_credits WHERE title_id=?", (title_id,)
                    )
                    conn.executemany(
                        """INSERT OR IGNORE INTO title_credits
                           (title_id,imdb_person_id,person_name,role,billing_order)
                           VALUES (?,?,?,?,?)""",
                        [
                            (title_id, person_id, person_name, role, billing_order)
                            for person_id, person_name, role, billing_order in credits
                        ],
                    )
                conn.execute(
                    """UPDATE metadata_refresh_queue
                       SET status='complete',completed_at=CURRENT_TIMESTAMP,
                           provider='TVDB',error=''
                       WHERE title_id=?""",
                    (title_id,),
                )

            duration_ms = int((time.monotonic() - started) * 1000)
            update_task(
                status="complete",
                phase="complete",
                title_ids=[title_id],
                scope_label=label,
                matched=1,
                requested=1,
                credits_matched=1 if credits else 0,
                duration_ms=duration_ms,
            )
            return {
                "completed": True,
                "status": "complete",
                "detail": "Metadata refresh complete.",
                "provider": "TVDB",
                "credits_matched": 1 if credits else 0,
                "duration_ms": duration_ms,
            }
        except Exception as exc:
            detail = str(exc) or "Metadata refresh failed"
            duration_ms = int((time.monotonic() - started) * 1000)
            with db.connect() as conn:
                conn.execute(
                    "UPDATE titles SET metadata_refresh_error=? WHERE id=?",
                    (detail, title_id),
                )
                conn.execute(
                    """UPDATE metadata_refresh_queue
                       SET status='failed',completed_at=CURRENT_TIMESTAMP,error=?
                       WHERE title_id=?""",
                    (detail, title_id),
                )
            update_task(
                status="error",
                phase="error",
                title_ids=[title_id],
                scope_label=label,
                error=detail,
                duration_ms=duration_ms,
            )
            return {
                "completed": False,
                "status": "failed",
                "detail": detail,
                "duration_ms": duration_ms,
            }

    @router.post(
        "/titles/{title_id}/imdb-refresh",
        dependencies=[Depends(require_librarian)],
    )
    def refresh_title_metadata(request: Request, title_id: int):
        with db.connect() as conn:
            title = conn.execute(
                """SELECT id,kind,tvdb_id,tvdb_movie_id,
                          COALESCE(NULLIF(metadata_title,''),title) display_title
                   FROM titles WHERE id=?""",
                (title_id,),
            ).fetchone()
        if not title:
            detail = "Metadata refresh could not start because that title no longer exists."
            if async_request(request):
                return JSONResponse(
                    {"started": False, "detail": detail}, status_code=404,
                )
            return redirect("/library", detail)

        if not tvdb.api_key:
            detail = "TVDB credentials must be configured before refreshing one title."
            if async_request(request):
                return JSONResponse(
                    {"started": False, "detail": detail}, status_code=409,
                )
            return redirect(f"/titles/{title_id}", detail)

        provider_id = (
            title["tvdb_movie_id"] if title["kind"] == "movie" else title["tvdb_id"]
        )
        if not provider_id:
            detail = "This title needs a TVDB match before it can use the quick refresh action."
            if async_request(request):
                return JSONResponse(
                    {"started": False, "detail": detail}, status_code=409,
                )
            return redirect(f"/titles/{title_id}", detail)

        label = f"Refreshing metadata for {title['display_title']}"
        with imdb_genre_lock:
            if imdb_genre_job.get("status") in {"starting", "running"}:
                detail = "Another metadata refresh is already running. Try again when it finishes."
                if async_request(request):
                    return JSONResponse(
                        {"started": False, "detail": detail}, status_code=409,
                    )
                return redirect(f"/titles/{title_id}", detail)
            imdb_genre_job.clear()
            imdb_genre_job.update({
                "status": "starting",
                "phase": "provider",
                "scope_label": label,
                "title_ids": [title_id],
                "ui_scope": "local",
                "ui_title_id": title_id,
            })

        with db.connect() as conn:
            conn.execute(
                """INSERT INTO metadata_refresh_queue
                       (title_id,status,requested_by,requested_at,error)
                   VALUES (?,'queued',?,CURRENT_TIMESTAMP,'')
                   ON CONFLICT(title_id) DO UPDATE SET
                       status='queued',requested_by=excluded.requested_by,
                       requested_at=CURRENT_TIMESTAMP,started_at=NULL,
                       completed_at=NULL,error=''""",
                (
                    title_id,
                    request.state.user.id if request.state.user.id > 0 else None,
                ),
            )

        # A one-title refresh is deliberately completed inside this request. The
        # provider calls are already bounded by TVDBClient timeouts, so keeping the
        # operation here removes the background-thread/polling race that could leave
        # the UI stuck at "queued". Bulk refresh remains asynchronous elsewhere.
        result = run_targeted_refresh(title_id, label)
        payload = {
            "started": True,
            "title_id": title_id,
            "ui_scope": "local",
            **result,
        }
        if async_request(request):
            return JSONResponse(
                payload, status_code=200 if result["completed"] else 502,
            )
        return redirect(f"/titles/{title_id}", result["detail"])

    @router.get("/api/titles/{title_id}/metadata-refresh-state")
    def title_metadata_refresh_state(title_id: int):
        # Retain the durable state endpoint for recovery, older clients, and
        # diagnostics. The current Metadata UI does not need it for the normal
        # one-title refresh path once the POST returns.
        with imdb_genre_lock:
            task = {
                key: imdb_genre_job.get(key)
                for key in (
                    "status", "phase", "scope_label", "title_ids", "records",
                    "matched", "requested", "id_processed", "id_total",
                    "id_found", "id_missing", "id_errors", "credits_matched",
                    "duration_ms", "error", "ui_scope", "ui_title_id",
                )
                if key in imdb_genre_job
            }
        task.setdefault("status", "idle")
        active_ids = task.get("title_ids")
        task_is_this_title = (
            task["status"] in {"starting", "running"}
            and (active_ids is None or title_id in active_ids)
        )
        if task_is_this_title:
            return {
                "title_id": title_id,
                "task": task,
                "queue": None,
                "metadata_refreshed_at": None,
                "metadata_refresh_error": "",
                "updated_at": None,
            }

        with db.connect() as conn:
            row = conn.execute(
                """SELECT t.id,t.metadata_refreshed_at,t.metadata_refresh_error,t.updated_at,
                          q.status queue_status,q.requested_at,q.started_at,q.completed_at,
                          q.provider,q.error queue_error
                   FROM titles t
                   LEFT JOIN metadata_refresh_queue q ON q.title_id=t.id
                   WHERE t.id=?""",
                (title_id,),
            ).fetchone()
        if not row:
            return JSONResponse({"detail": "Title not found"}, status_code=404)

        queue = None
        if row["queue_status"] is not None:
            queue = {
                "status": row["queue_status"],
                "requested_at": row["requested_at"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "provider": row["provider"],
                "error": row["queue_error"],
            }
        return {
            "title_id": title_id,
            "task": task,
            "queue": queue,
            "metadata_refreshed_at": row["metadata_refreshed_at"],
            "metadata_refresh_error": row["metadata_refresh_error"],
            "updated_at": row["updated_at"],
        }

    return router, {
        "refresh_title_metadata": refresh_title_metadata,
        "title_metadata_refresh_state": title_metadata_refresh_state,
    }

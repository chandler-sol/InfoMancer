from pathlib import Path

path = Path(__file__).resolve().parent.parent / "app" / "main.py"
text = path.read_text(encoding="utf-8")
text = text.replace(
    "from .runtime import JobRegistry, RuntimeLease\n",
    "from .background import BackgroundCoordinator\n",
    1,
)
start = text.index("job_registry = JobRegistry()\n")
end = text.index("\ndef record_event(\n", start)
replacement = '''def _file_signatures(*, root_id: int | None = None, title_id: int | None = None) -> dict[int, tuple[int, float]]:
    where, value = ("t.root_id", root_id) if root_id is not None else ("f.title_id", title_id)
    with db.connect() as conn:
        return {
            int(row["id"]): (int(row["size_bytes"] or 0), float(row["modified_at"] or 0))
            for row in conn.execute(
                f"""SELECT f.id,f.size_bytes,f.modified_at FROM files f
                    JOIN titles t ON t.id=f.title_id WHERE {where}=?""", (value,)
            )
        }


def _changed_file_ids(before: dict[int, tuple[int, float]], after: dict[int, tuple[int, float]]) -> list[int]:
    return [file_id for file_id, signature in after.items() if before.get(file_id) != signature]

'''
text = text[:start] + replacement + text[end + 1:]
needle = '''    event_log.write(
        category, message, level=stored_level, detail=detail,
        context=context, user_id=user_id,
    )


PUBLIC_PATHS = {"/health", "/login", "/setup", "/forgot-password"}
'''
insert = '''    event_log.write(
        category, message, level=stored_level, detail=detail,
        context=context, user_id=user_id,
    )


background = BackgroundCoordinator(
    db, app_settings, media_hashes, duplicate_trash, record_event,
)
job_registry = background.registry
runtime_lease = background.runtime_lease
scan_jobs = background.scan_jobs
scan_lock = background.scan_lock
scan_all_job = background.scan_all_job
scan_all_lock = background.scan_all_lock
title_scan_jobs = background.title_scan_jobs
title_scan_lock = background.title_scan_lock
imdb_genre_job = background.imdb_genre_job
imdb_genre_lock = background.imdb_genre_lock
movie_match_job = background.movie_match_job
movie_match_lock = background.movie_match_lock
tv_match_job = background.tv_match_job
tv_match_lock = background.tv_match_lock
media_info_job = background.media_info_job
media_info_lock = background.media_info_lock
duplicate_verify_job = background.duplicate_verify_job
duplicate_verify_lock = background.duplicate_verify_lock
media_hash_job = background.media_hash_job
media_hash_lock = background.media_hash_lock
media_hash_pause = background.media_hash_pause
media_hash_cancel = background.media_hash_cancel
background_scheduler_stop = background.scheduler_stop
trash_cleanup_job = background.trash_cleanup_job
trash_cleanup_lock = background.trash_cleanup_lock
run_media_hashing = background.run_media_hashing
start_media_hashing = background.start_media_hashing
handle_import_hashing = background.handle_import_hashing
_other_background_work_running = background.other_background_work_running
maybe_start_scheduled_hashing = background.maybe_start_scheduled_hashing
run_background_scheduler = background.run_scheduler
trash_retention_days = background.trash_retention_days
maybe_start_trash_cleanup = background.maybe_start_trash_cleanup


@app.on_event("startup")
def start_background_scheduler() -> None:
    background.start()


@app.on_event("shutdown")
def stop_background_scheduler() -> None:
    background.stop()


PUBLIC_PATHS = {"/health", "/login", "/setup", "/forgot-password"}
'''
if needle not in text:
    raise RuntimeError("record_event insertion point changed")
text = text.replace(needle, insert, 1)
path.write_text(text, encoding="utf-8")
print("background coordinator extracted from main.py")

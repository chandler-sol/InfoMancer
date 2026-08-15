from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from .app_settings import AppSettings
from .db import Database
from .duplicate_trash import DuplicateTrashService
from .file_hashes import MediaHashService
from .runtime import JobRegistry, RuntimeLease


class BackgroundCoordinator:
    """Own background task state, schedules, and single-runtime coordination."""

    def __init__(
        self, database: Database, app_settings: AppSettings,
        media_hashes: MediaHashService, duplicate_trash: DuplicateTrashService,
        record_event: Callable[..., None],
    ) -> None:
        self.database = database
        self.app_settings = app_settings
        self.media_hashes = media_hashes
        self.duplicate_trash = duplicate_trash
        self.record_event = record_event
        self.registry = JobRegistry()
        self.runtime_lease = RuntimeLease(database)

        self.scan_jobs: dict[int, dict] = self.registry.mapping("scan")
        self.scan_lock = self.registry.lock("scan")
        self.scan_all_job = self.registry.job("scan-all")
        self.scan_all_lock = self.registry.lock("scan-all")
        self.title_scan_jobs: dict[int, dict] = self.registry.mapping("title-scan")
        self.title_scan_lock = self.registry.lock("title-scan")
        self.imdb_genre_job = self.registry.job("imdb-metadata")
        self.imdb_genre_lock = self.registry.lock("imdb-metadata")
        self.movie_match_job = self.registry.job("movie-match")
        self.movie_match_lock = self.registry.lock("movie-match")
        self.tv_match_job = self.registry.job("tv-match")
        self.tv_match_lock = self.registry.lock("tv-match")
        self.media_info_job = self.registry.job("media-info")
        self.media_info_lock = self.registry.lock("media-info")
        self.duplicate_verify_job = self.registry.job("duplicate-verify")
        self.duplicate_verify_lock = self.registry.lock("duplicate-verify")
        self.media_hash_job = self.registry.job("media-hash")
        self.media_hash_lock = self.registry.lock("media-hash")
        self.media_hash_pause = self.registry.event("media-hash-pause")
        self.media_hash_cancel = self.registry.event("media-hash-cancel")
        self.scheduler_stop = self.registry.event("scheduler-stop")
        self.trash_cleanup_job = self.registry.job("trash-cleanup")
        self.trash_cleanup_lock = self.registry.lock("trash-cleanup")
        self.hash_schedule_last_check = 0.0
        self.trash_cleanup_last_check = 0.0

    def run_media_hashing(self, file_ids: list[int], reason: str) -> None:
        ids = list(dict.fromkeys(file_ids))
        self.media_hash_cancel.clear()
        self.media_hash_pause.clear()
        with self.media_hash_lock:
            self.media_hash_job.clear()
            self.media_hash_job.update({
                "status": "running", "processed": 0, "total": len(ids),
                "current": "", "reason": reason, "complete": 0, "failed": 0,
            })

        def progress(processed: int, total: int, current: str) -> None:
            with self.media_hash_lock:
                self.media_hash_job.update({
                    "processed": processed, "total": total, "current": current,
                })

        result = self.media_hashes.hash_many(
            ids, progress=progress, cancelled=self.media_hash_cancel.is_set,
            paused=lambda: self.media_hash_pause.is_set() or (
                self.app_settings.get("hash_pause_for_activity") == "1"
                and self.other_background_work_running()
            ),
            intensity=self.app_settings.get("hash_io_intensity"),
        )
        status = "cancelled" if self.media_hash_cancel.is_set() else "complete"
        with self.media_hash_lock:
            self.media_hash_job.update({"status": status, **result, "current": ""})
        self.record_event(
            "media",
            f"File fingerprinting finished: {result['complete']:,} checked and "
            f"{result['failed']:,} could not be read.",
            level="warning" if result["failed"] else "info",
            context={"reason": reason, **result},
        )

    def start_media_hashing(
        self, file_ids: list[int], reason: str, *, queue_files: bool = True,
    ) -> bool:
        ids = (
            self.media_hashes.queue(file_ids)
            if queue_files else list(dict.fromkeys(file_ids))
        )
        if not ids:
            return False
        with self.media_hash_lock:
            if self.media_hash_job.get("status") in {"starting", "running"}:
                return False
            self.media_hash_job.clear()
            self.media_hash_job.update({
                "status": "starting", "processed": 0,
                "total": len(ids), "reason": reason,
            })
        threading.Thread(
            target=self.run_media_hashing, args=(ids, reason), daemon=True,
        ).start()
        return True

    def handle_import_hashing(self, file_ids: list[int], reason: str) -> None:
        mode = self.app_settings.get("hash_mode")
        if mode in {"off", "on_demand"} or not file_ids:
            return
        queued = self.media_hashes.queue(file_ids)
        if mode == "automatic" and queued:
            limit = int(self.app_settings.get("hash_immediate_limit"))
            immediate = queued[:limit]
            deferred = queued[limit:]
            started = self.start_media_hashing(immediate, reason, queue_files=False)
            if immediate and not started:
                self.record_event(
                    "media",
                    f"{len(queued):,} new or changed files were queued because another "
                    "fingerprinting task is already running.",
                    context={"queued": len(queued), "reason": reason},
                )
            if deferred:
                self.record_event(
                    "media",
                    f"{len(queued):,} new or changed files need fingerprints. "
                    f"{len(immediate):,} "
                    f"{'are being checked now' if started else 'remain queued'} "
                    f"and {len(deferred):,} were queued for scheduled or manual processing.",
                    context={
                        "queued": len(queued), "immediate": len(immediate),
                        "deferred": len(deferred),
                    },
                )
        elif queued:
            self.record_event(
                "media",
                f"{len(queued):,} new or changed files were queued for scheduled fingerprinting.",
                context={"queued": len(queued), "reason": reason},
            )

    def other_background_work_running(self) -> bool:
        with (
            self.scan_all_lock, self.scan_lock, self.title_scan_lock,
            self.movie_match_lock, self.tv_match_lock, self.media_info_lock,
        ):
            return any((
                self.scan_all_job.get("status") in {"starting", "running"},
                any(
                    job.get("status") in {"starting", "running"}
                    for job in self.scan_jobs.values()
                ),
                any(
                    job.get("status") in {"starting", "running"}
                    for job in self.title_scan_jobs.values()
                ),
                self.movie_match_job.get("status") in {"starting", "running"},
                self.tv_match_job.get("status") in {"starting", "running"},
                self.media_info_job.get("status") in {"starting", "running"},
            ))

    def maybe_start_scheduled_hashing(self) -> None:
        now_epoch = time.time()
        if now_epoch - self.hash_schedule_last_check < 30:
            return
        self.hash_schedule_last_check = now_epoch
        prefs = self.app_settings.values()
        if prefs["hash_mode"] not in {"automatic", "scheduled"}:
            return
        if (
            prefs["hash_pause_for_activity"] == "1"
            and self.other_background_work_running()
        ):
            return
        local_now = datetime.now(ZoneInfo(prefs["timezone"]))
        hour, minute = (
            int(part) for part in prefs["hash_schedule_time"].split(":")
        )
        if (local_now.hour, local_now.minute) < (hour, minute):
            return
        frequency = prefs["hash_schedule_frequency"]
        day = int(prefs["hash_schedule_day"])
        if frequency == "weekly" and local_now.weekday() != day:
            return
        if frequency == "monthly" and local_now.day != day:
            return
        last_text = prefs.get("hash_last_scheduled_at", "")
        if last_text:
            last = datetime.fromisoformat(last_text)
            already_ran = (
                frequency == "daily" and last.date() == local_now.date()
                or frequency == "weekly"
                and last.isocalendar()[:2] == local_now.isocalendar()[:2]
                or frequency == "monthly"
                and (last.year, last.month) == (local_now.year, local_now.month)
            )
            if already_ran:
                return
        ids = self.media_hashes.eligible_ids()
        if ids and self.start_media_hashing(ids, "Scheduled file fingerprinting"):
            self.app_settings.set_internal(
                "hash_last_scheduled_at", local_now.isoformat(),
            )

    def trash_retention_days(self) -> int | None:
        value = self.app_settings.get("trash_retention_days")
        return None if value == "never" else int(value)

    def maybe_start_trash_cleanup(self) -> None:
        """Check for expired managed-trash items at most once per day."""
        now = time.time()
        with self.trash_cleanup_lock:
            if now - self.trash_cleanup_last_check < 86_400:
                return
            self.trash_cleanup_last_check = now
            self.trash_cleanup_job.clear()
            self.trash_cleanup_job.update({
                "status": "starting", "detail": "Checking retention dates",
            })

        def run() -> None:
            try:
                with self.trash_cleanup_lock:
                    self.trash_cleanup_job.update({
                        "status": "running",
                        "detail": "Removing expired managed-trash items",
                    })
                purged = self.duplicate_trash.purge_expired()
                with self.trash_cleanup_lock:
                    self.trash_cleanup_job.update({
                        "status": "complete",
                        "detail": f"{purged:,} expired item(s) removed",
                    })
            except (OSError, ValueError, sqlite3.Error) as exc:
                with self.trash_cleanup_lock:
                    self.trash_cleanup_job.update({
                        "status": "error",
                        "detail": "Trash cleanup could not finish. Open Logs for details.",
                        "error": str(exc),
                    })

        threading.Thread(target=run, daemon=True).start()

    def run_scheduler(self) -> None:
        """Run installation schedules even when no browser is open."""
        while not self.scheduler_stop.wait(30):
            try:
                self.maybe_start_scheduled_hashing()
                self.maybe_start_trash_cleanup()
            except Exception as exc:
                self.record_event(
                    "system",
                    "A scheduled maintenance check could not be completed. "
                    "InfoMancer will try again automatically.",
                    level="error", detail=str(exc),
                )

    def start(self) -> None:
        self.runtime_lease.start()
        self.scheduler_stop.clear()
        threading.Thread(
            target=self.run_scheduler, name="infomancer-scheduler", daemon=True,
        ).start()

    def stop(self) -> None:
        self.scheduler_stop.set()
        self.runtime_lease.release()

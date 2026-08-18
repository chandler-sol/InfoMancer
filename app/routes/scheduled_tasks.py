from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request

from ..access import require_librarian
from .context import RouteContext


WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _format_local(value: datetime) -> str:
    clock = value.strftime("%I:%M %p").lstrip("0")
    return f"{value.strftime('%A, %b')} {value.day} at {clock} {value.strftime('%Z')}"


def _next_hash_run(preferences: dict[str, str]) -> str:
    if preferences.get("hash_mode") not in {"automatic", "scheduled"}:
        return "Not scheduled"
    try:
        zone = ZoneInfo(preferences.get("timezone") or "UTC")
        now = datetime.now(zone)
        hour, minute = (int(part) for part in preferences["hash_schedule_time"].split(":"))
        frequency = preferences["hash_schedule_frequency"]
        day = int(preferences["hash_schedule_day"])
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        if frequency == "daily":
            if candidate <= now:
                candidate += timedelta(days=1)
        elif frequency == "weekly":
            days_ahead = (day - now.weekday()) % 7
            candidate += timedelta(days=days_ahead)
            if candidate <= now:
                candidate += timedelta(days=7)
        elif frequency == "monthly":
            candidate = candidate.replace(day=day)
            if candidate <= now:
                if candidate.month == 12:
                    candidate = candidate.replace(year=candidate.year + 1, month=1, day=day)
                else:
                    candidate = candidate.replace(month=candidate.month + 1, day=day)
        else:
            return "Not scheduled"
        return _format_local(candidate)
    except (KeyError, TypeError, ValueError):
        return "Schedule needs attention"


def _last_hash_run(preferences: dict[str, str]) -> str:
    raw = preferences.get("hash_last_scheduled_at") or ""
    if not raw:
        return "Never"
    try:
        value = datetime.fromisoformat(raw)
        zone = ZoneInfo(preferences.get("timezone") or "UTC")
        if value.tzinfo is None:
            value = value.replace(tzinfo=zone)
        else:
            value = value.astimezone(zone)
        return _format_local(value)
    except (TypeError, ValueError):
        return raw


def _schedule_label(preferences: dict[str, str]) -> str:
    frequency = preferences.get("hash_schedule_frequency", "weekly")
    day = int(preferences.get("hash_schedule_day") or 0)
    clock = preferences.get("hash_schedule_time") or "03:00"
    try:
        display_clock = datetime.strptime(clock, "%H:%M").strftime("%I:%M %p").lstrip("0")
    except ValueError:
        display_clock = clock
    if frequency == "daily":
        return f"Daily at {display_clock}"
    if frequency == "weekly" and 0 <= day <= 6:
        return f"Every {WEEKDAYS[day]} at {display_clock}"
    if frequency == "monthly" and 1 <= day <= 28:
        return f"Monthly on day {day} at {display_clock}"
    return "Schedule needs attention"


def build_router(ctx: RouteContext):
    router = APIRouter()
    AppSettingError = ctx.get("AppSettingError")
    Form = ctx.get("Form")
    HTMLResponse = ctx.get("HTMLResponse")
    app_settings = ctx.live("app_settings")
    media_hash_cancel = ctx.live("media_hash_cancel")
    media_hash_job = ctx.live("media_hash_job")
    media_hash_lock = ctx.live("media_hash_lock")
    media_hash_pause = ctx.live("media_hash_pause")
    media_hashes = ctx.live("media_hashes")
    record_event = ctx.live("record_event")
    redirect = ctx.live("redirect")
    start_media_hashing = ctx.live("start_media_hashing")
    templates = ctx.live("templates")
    trash_cleanup_job = ctx.live("trash_cleanup_job")
    trash_cleanup_lock = ctx.live("trash_cleanup_lock")

    def librarian_get(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.get(path, dependencies=dependencies, **kwargs)

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

    @librarian_get("/settings/scheduled-tasks", response_class=HTMLResponse)
    def scheduled_tasks_page(request: Request):
        preferences = app_settings.values()
        with media_hash_lock:
            hash_job = dict(media_hash_job)
        with trash_cleanup_lock:
            trash_job = dict(trash_cleanup_job)
        protection_mode = app_settings.file_protection_mode()
        retention = preferences.get("trash_retention_days", "30")
        retention_label = {
            "never": "Never expire automatically",
            "7": "7 days",
            "30": "30 days",
            "90": "90 days",
            "365": "1 year",
        }.get(retention, retention)
        trash_status = (
            "Running" if trash_job.get("status") in {"starting", "running"}
            else "Needs attention" if trash_job.get("status") in {"error", "failed"}
            else "Paused by file protection" if protection_mode in {"readonly", "lockdown"}
            else "Daily check"
        )
        return templates.TemplateResponse(request, "scheduled_tasks.html", {
            "section": "scheduled-tasks",
            "preferences": preferences,
            "hash_counts": media_hashes.counts(),
            "hash_job": hash_job,
            "hash_paused": media_hash_pause.is_set(),
            "hash_schedule": {
                "enabled": preferences.get("hash_mode") in {"automatic", "scheduled"},
                "label": _schedule_label(preferences),
                "next_run": _next_hash_run(preferences),
                "last_run": _last_hash_run(preferences),
            },
            "trash_task": {
                "status": trash_status,
                "retention": retention,
                "retention_label": retention_label,
                "protection_mode": protection_mode,
                "detail": trash_job.get("detail") or "Checks for expired managed-trash items once per day.",
            },
            "message": request.query_params.get("message", ""),
            "error": "",
        })

    @librarian_post("/settings/scheduled-tasks/fingerprints")
    def save_fingerprint_schedule(
        request: Request,
        hash_mode: str = Form(...),
        hash_immediate_limit: str = Form(...),
        hash_schedule_frequency: str = Form(...),
        hash_schedule_day: str = Form(...),
        hash_schedule_time: str = Form(...),
        hash_io_intensity: str = Form(...),
        hash_pause_for_activity: str = Form("0"),
    ):
        try:
            validated = app_settings.validate_hashing(
                hash_mode, hash_immediate_limit, hash_schedule_frequency,
                hash_schedule_day, hash_schedule_time, hash_io_intensity,
                hash_pause_for_activity,
            )
            changed = app_settings.update(validated, request.state.user.id)
        except AppSettingError as exc:
            return redirect("/settings/scheduled-tasks", str(exc))
        record_event(
            "settings", "File fingerprint schedule updated.",
            context={"changed": changed, "mode": validated["hash_mode"]},
            user_id=request.state.user.id,
        )
        return redirect(
            "/settings/scheduled-tasks",
            "Fingerprint schedule saved." if changed else
            "Fingerprint schedule was already up to date; nothing changed.",
        )

    @librarian_post("/settings/scheduled-tasks/fingerprints/run")
    def run_fingerprints_now():
        ids = media_hashes.eligible_ids()
        if not ids:
            return redirect(
                "/settings/scheduled-tasks",
                "Every current media file already has a fingerprint.",
            )
        if not start_media_hashing(ids, "Manual file fingerprinting"):
            return redirect(
                "/settings/scheduled-tasks",
                "Fingerprinting is already running. Progress remains visible in the task widget.",
            )
        return redirect(
            "/settings/scheduled-tasks",
            f"Fingerprinting started for {len(ids):,} files. You can continue using InfoMancer while it runs.",
        )

    @librarian_post("/settings/scheduled-tasks/fingerprints/pause")
    def pause_fingerprints():
        with media_hash_lock:
            running = media_hash_job.get("status") in {"starting", "running"}
        if not running:
            return redirect("/settings/scheduled-tasks", "There is no fingerprinting task to pause.")
        media_hash_pause.set()
        return redirect(
            "/settings/scheduled-tasks",
            "Fingerprinting paused after the current file. Select Resume when you are ready.",
        )

    @librarian_post("/settings/scheduled-tasks/fingerprints/resume")
    def resume_fingerprints():
        with media_hash_lock:
            running = media_hash_job.get("status") in {"starting", "running"}
        if not running:
            return redirect("/settings/scheduled-tasks", "There is no paused fingerprinting task to resume.")
        media_hash_pause.clear()
        return redirect("/settings/scheduled-tasks", "Fingerprinting resumed.")

    @librarian_post("/settings/scheduled-tasks/fingerprints/cancel")
    def cancel_fingerprints():
        with media_hash_lock:
            running = media_hash_job.get("status") in {"starting", "running"}
        if not running:
            return redirect("/settings/scheduled-tasks", "There is no fingerprinting task to cancel.")
        media_hash_cancel.set()
        media_hash_pause.clear()
        return redirect(
            "/settings/scheduled-tasks",
            "Fingerprinting is stopping after the current file. Unfinished files remain available for the next run.",
        )

    @librarian_post("/settings/scheduled-tasks/trash-retention")
    def save_trash_retention(request: Request, trash_retention_days: str = Form(...)):
        retention = trash_retention_days.strip().casefold()
        if retention not in {"never", "7", "30", "90", "365"}:
            return redirect(
                "/settings/scheduled-tasks",
                "Choose Never, 7 days, 30 days, 90 days, or 1 year for managed-trash retention.",
            )
        changed = app_settings.update(
            {"trash_retention_days": retention}, request.state.user.id,
        )
        record_event(
            "settings", "Managed-trash retention schedule updated.",
            context={"retention_days": retention}, user_id=request.state.user.id,
        )
        return redirect(
            "/settings/scheduled-tasks",
            "Managed-trash retention saved." if changed else
            "Managed-trash retention was already up to date; nothing changed.",
        )

    return router, {
        "scheduled_tasks_page": scheduled_tasks_page,
        "save_fingerprint_schedule": save_fingerprint_schedule,
        "run_fingerprints_now": run_fingerprints_now,
        "pause_fingerprints": pause_fingerprints,
        "resume_fingerprints": resume_fingerprints,
        "cancel_fingerprints": cancel_fingerprints,
        "save_trash_retention": save_trash_retention,
    }

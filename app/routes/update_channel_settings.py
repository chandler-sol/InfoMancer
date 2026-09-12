from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request

from ..access import require_librarian
from ..maintenance import (
    MaintenanceError,
    create_database_backup,
    read_update_status,
    write_update_request,
    write_update_status,
)
from ..update_channels import (
    CHANNEL_DESCRIPTIONS,
    CHANNEL_LABELS,
    CHANNEL_RANK,
    UPDATE_CHANNELS,
    channel_label,
    channel_transition,
    normalize_channel,
    read_update_channel,
    select_release,
    update_state,
    write_update_channel,
)
from .context import RouteContext


REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def build_router(ctx: RouteContext):
    router = APIRouter()
    APP_VERSION = ctx.get("APP_VERSION")
    db = ctx.live("db")
    record_event = ctx.live("record_event")
    redirect = ctx.live("redirect")
    templates = ctx.live("templates")

    def repository_name() -> str:
        return os.getenv(
            "INFOMANCER_UPDATE_REPOSITORY", "chandler-sol/InfoMancer"
        ).strip()

    def page_context(request: Request, error: str = "") -> dict:
        channel = read_update_channel(db.path)
        status = read_update_status(db.path)
        return {
            "section": "updates",
            "error": error,
            "message": request.query_params.get("message", ""),
            "update_channel": channel,
            "update_channel_label": channel_label(channel),
            "update_channels": UPDATE_CHANNELS,
            "update_channel_labels": CHANNEL_LABELS,
            "update_channel_descriptions": CHANNEL_DESCRIPTIONS,
            "update_channel_ranks": CHANNEL_RANK,
            "update_status": status,
            "update_repository": repository_name(),
        }

    @router.get(
        "/settings/updates",
        dependencies=[Depends(require_librarian)],
    )
    def update_settings_page(request: Request):
        return templates.TemplateResponse(
            request, "settings_updates.html", page_context(request)
        )

    @router.post(
        "/settings/updates/channel",
        dependencies=[Depends(require_librarian)],
    )
    def choose_update_channel(
        request: Request,
        update_channel: str = Form(...),
        confirm_preview: str = Form(""),
    ):
        current = read_update_channel(db.path)
        try:
            selected = normalize_channel(update_channel)
        except ValueError as exc:
            return templates.TemplateResponse(
                request,
                "settings_updates.html",
                page_context(request, str(exc)),
                status_code=400,
            )
        transition = channel_transition(current, selected)
        if transition == "less_stable" and confirm_preview != "PREVIEW":
            return templates.TemplateResponse(
                request,
                "settings_updates.html",
                page_context(
                    request,
                    "Switching to a less-stable update channel requires confirmation. "
                    "No channel setting was changed.",
                ),
                status_code=400,
            )
        try:
            write_update_channel(db.path, selected)
            write_update_status(db.path, {
                "status": "idle",
                "channel": selected,
                "current_version": APP_VERSION,
                "message": (
                    f"Update channel changed to {channel_label(selected)}. "
                    "Check for updates when you are ready."
                ),
                "changed_at": datetime.now(timezone.utc).isoformat(),
            })
        except OSError:
            return templates.TemplateResponse(
                request,
                "settings_updates.html",
                page_context(
                    request,
                    "InfoMancer could not save the update channel. Check application-data permissions and free disk space.",
                ),
                status_code=500,
            )
        message = f"Update channel changed to {channel_label(selected)}."
        if transition == "more_stable":
            message += (
                " InfoMancer will not downgrade the installed build. It will wait "
                "for that channel to catch up."
            )
        record_event(
            "update",
            message,
            context={
                "old_channel": current,
                "new_channel": selected,
                "transition": transition,
            },
            user_id=request.state.user.id,
        )
        return redirect("/settings/updates", message)

    @router.post(
        "/settings/updates/check",
        dependencies=[Depends(require_librarian)],
    )
    def check_channel_for_updates(request: Request):
        repository = repository_name()
        channel = read_update_channel(db.path)
        if not REPOSITORY_PATTERN.fullmatch(repository):
            return redirect(
                "/settings/updates",
                "Update checking is unavailable because the configured GitHub repository name is invalid.",
            )
        url = f"https://api.github.com/repos/{repository}/releases?per_page=100"
        try:
            github_request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"InfoMancer/{APP_VERSION}",
                },
            )
            with urllib.request.urlopen(github_request, timeout=10) as response:
                releases = json.loads(response.read(2 * 1024 * 1024))
            if not isinstance(releases, list):
                raise ValueError("GitHub returned an unexpected releases response.")
            release = select_release(releases, channel)
            checked_at = datetime.now(timezone.utc).isoformat()
            if release is None:
                status = {
                    "status": "no_releases",
                    "channel": channel,
                    "checked_at": checked_at,
                    "current_version": APP_VERSION,
                    "message": (
                        f"GitHub is reachable, but the {channel_label(channel)} channel "
                        "does not contain a published InfoMancer release yet."
                    ),
                }
                write_update_status(db.path, status)
                return redirect("/settings/updates", status["message"])

            tag = str(release.get("tag_name") or "").strip()
            state = update_state(APP_VERSION, tag)
            latest_channel = str(release.get("infomancer_channel") or channel)
            status = {
                "status": state,
                "channel": channel,
                "latest_channel": latest_channel,
                "checked_at": checked_at,
                "current_version": APP_VERSION,
                "latest_version": tag,
                "release_name": release.get("name") or tag,
                "release_url": release.get("html_url") or "",
                "release_notes": str(release.get("body") or "")[:4000],
                "published_at": release.get("published_at") or "",
                "qualification_status": "published",
            }
            if state == "waiting_for_channel":
                status["message"] = (
                    f"This installation is already newer than the latest "
                    f"{channel_label(channel)} release ({tag}). InfoMancer will not "
                    "downgrade it; it will wait for the selected channel to catch up."
                )
            elif state == "current":
                status["message"] = (
                    f"InfoMancer {APP_VERSION} is current on the "
                    f"{channel_label(channel)} channel."
                )
            else:
                status["message"] = (
                    f"InfoMancer {tag} is available on the "
                    f"{channel_label(channel)} channel."
                )
            write_update_status(db.path, status)
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            ValueError,
            json.JSONDecodeError,
            OSError,
        ) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                explanation = "GitHub could not find the configured InfoMancer repository."
            elif isinstance(exc, urllib.error.HTTPError) and exc.code == 403:
                explanation = (
                    "GitHub temporarily refused the update check, usually because its anonymous API limit was reached."
                )
            else:
                explanation = (
                    "GitHub could not be reached or returned an unreadable release list."
                )
            record_event(
                "update",
                explanation,
                level="error",
                detail=str(exc),
                context={"channel": channel},
                user_id=request.state.user.id,
            )
            return redirect(
                "/settings/updates",
                f"{explanation} InfoMancer was not changed.",
            )
        record_event(
            "update",
            status["message"],
            context={"channel": channel, "latest_version": tag},
            user_id=request.state.user.id,
        )
        return redirect("/settings/updates", status["message"])

    @router.post(
        "/settings/updates/apply",
        dependencies=[Depends(require_librarian)],
    )
    def apply_channel_update(
        request: Request,
        tag: str = Form(...),
        confirm: str = Form(""),
    ):
        if confirm != "UPDATE":
            return redirect(
                "/settings/updates", "Update cancelled; InfoMancer was not changed."
            )
        channel = read_update_channel(db.path)
        status = read_update_status(db.path)
        if (
            status.get("status") != "available"
            or status.get("latest_version") != tag
            or status.get("channel") != channel
        ):
            return redirect(
                "/settings/updates",
                "That build is no longer the verified available release for the selected channel. Check for updates again before applying it.",
            )
        try:
            safety = create_database_backup(db.path, "before-update")
            write_update_request(db.path, tag, request.state.user.username)
            write_update_status(db.path, {
                "status": "requested",
                "channel": channel,
                "current_version": APP_VERSION,
                "latest_version": tag,
                "qualification_status": status.get("qualification_status", "published"),
                "message": (
                    f"{channel_label(channel)} update {tag} is queued. The restricted "
                    "host updater will begin it when that helper is running."
                ),
                "requested_at": datetime.now(timezone.utc).isoformat(),
            })
        except MaintenanceError as exc:
            return redirect("/settings/updates", str(exc))
        record_event(
            "update",
            f"Application update {tag} requested from the {channel_label(channel)} channel.",
            context={"backup": safety.name, "channel": channel},
            user_id=request.state.user.id,
        )
        return redirect(
            "/settings/updates",
            f"Update {tag} was queued and database backup {safety.name} was created.",
        )

    return router, {
        "update_settings_page": update_settings_page,
        "choose_update_channel": choose_update_channel,
        "check_channel_for_updates": check_channel_for_updates,
        "apply_channel_update": apply_channel_update,
    }

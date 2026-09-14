from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request

from ..access import require_librarian
from ..maintenance import (
    MaintenanceError,
    create_database_backup,
    read_update_status,
    write_update_request,
    write_update_status,
)
from ..migrations import (
    assess_schema_downgrade,
    schema_compatibility_history,
    schema_contract,
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
    validate_channel_manifest,
    write_update_channel,
)
from .context import RouteContext


REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
MAX_UPDATE_RESPONSE = 2 * 1024 * 1024
DEFAULT_MANIFEST_BASE_URL = (
    "https://github.com/chandler-sol/InfoMancer/releases/download/update-channels"
)


def _require_https_update_url(url: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("Update metadata URL must use HTTPS without embedded credentials.")


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            _require_https_update_url(newurl)
        except ValueError as exc:
            raise urllib.error.URLError(
                "Update metadata redirects must remain on HTTPS."
            ) from exc
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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

    def manifest_base_url() -> str:
        configured = os.getenv("INFOMANCER_UPDATE_MANIFEST_BASE_URL")
        if configured is None:
            return DEFAULT_MANIFEST_BASE_URL
        configured = configured.strip()
        if configured.casefold() in {"off", "disabled", "none"}:
            return ""
        return configured.rstrip("/")

    def page_context(request: Request, error: str = "") -> dict:
        channel = read_update_channel(db.path)
        status = read_update_status(db.path)
        with db.connect() as conn:
            compatibility_history = schema_compatibility_history(conn)
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
            "update_manifest_base_url": manifest_base_url(),
            "schema_contract": schema_contract(),
            "schema_compatibility_history": compatibility_history,
        }

    def fetch_json(url: str) -> object:
        _require_https_update_url(url)
        update_request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json, application/vnd.github+json",
                "User-Agent": f"InfoMancer/{APP_VERSION}",
            },
        )
        opener = urllib.request.build_opener(_HttpsOnlyRedirectHandler())
        with opener.open(update_request, timeout=10) as response:
            payload = response.read(MAX_UPDATE_RESPONSE + 1)
        if len(payload) > MAX_UPDATE_RESPONSE:
            raise ValueError("Update metadata response is unexpectedly large.")
        return json.loads(payload)

    def status_from_manifest(channel: str, checked_at: str) -> dict:
        base_url = manifest_base_url()
        manifest_url = f"{base_url}/{channel}.json"
        manifest = validate_channel_manifest(fetch_json(manifest_url), channel)
        version = manifest["version"]
        state = update_state(APP_VERSION, version)
        qualification = manifest["qualification"]
        database_schema = manifest["database_schema"]
        with db.connect() as conn:
            schema_assessment = assess_schema_downgrade(
                conn, int(database_schema["current"])
            )
        artifacts = manifest["artifacts"]
        server_artifact = artifacts.get("server") if isinstance(artifacts, dict) else None
        server_tag = ""
        if isinstance(server_artifact, dict):
            server_tag = str(server_artifact.get("tag") or "").strip()
        installable = bool(server_tag)
        status = {
            "status": state,
            "channel": channel,
            "latest_channel": channel,
            "checked_at": checked_at,
            "current_version": APP_VERSION,
            "latest_version": version,
            "build_id": manifest["build_id"],
            "commit_sha": manifest["commit_sha"],
            "qualified_at": manifest["qualified_at"],
            "qualification_status": "passed",
            "qualification_workflow": qualification["workflow"],
            "qualification_run_id": qualification["run_id"],
            "qualification_run_url": qualification.get("run_url") or "",
            "qualification_gates": qualification["gates"],
            "database_schema": database_schema,
            "schema_assessment": schema_assessment,
            "release_notes_url": manifest.get("release_notes_url") or "",
            "manifest_url": manifest_url,
            "metadata_source": "qualified_manifest",
            "server_tag": server_tag,
            "installable": installable,
            "artifacts": sorted(artifacts),
        }
        if state == "waiting_for_channel":
            assessment = schema_assessment["status"]
            if assessment == "safe_downgrade":
                status["message"] = (
                    f"The latest qualified {channel_label(channel)} build ({version}) "
                    "is older than this installation, but the recorded database schema "
                    "history says it can read and write this database safely. Automatic "
                    "downgrade remains disabled until the updater path is qualified for rollback."
                )
            elif assessment == "read_only":
                status["message"] = (
                    f"The latest qualified {channel_label(channel)} build ({version}) is older. "
                    "Its schema generation could read this database, but writing would be unsafe, "
                    "so InfoMancer will not downgrade to it."
                )
            else:
                status["message"] = (
                    f"The latest qualified {channel_label(channel)} build ({version}) is older and "
                    "cannot safely use the current database schema. Returning to it would require "
                    "restoring a compatible pre-update database backup."
                )
        elif state == "current":
            status["message"] = (
                f"InfoMancer {APP_VERSION} is current on the "
                f"{channel_label(channel)} channel."
            )
        elif installable:
            status["message"] = (
                f"Qualified {channel_label(channel)} build {version} is available "
                "and has a trusted server release target."
            )
        else:
            status["message"] = (
                f"Qualified {channel_label(channel)} build {version} is available, "
                "but this installation does not have a server update artifact for it yet."
            )
        return status

    def status_from_github_releases(channel: str, checked_at: str) -> dict:
        repository = repository_name()
        if not REPOSITORY_PATTERN.fullmatch(repository):
            raise ValueError("The configured GitHub repository name is invalid.")
        url = f"https://api.github.com/repos/{repository}/releases?per_page=100"
        releases = fetch_json(url)
        if not isinstance(releases, list):
            raise ValueError("GitHub returned an unexpected releases response.")
        release = select_release(releases, channel)
        if release is None:
            return {
                "status": "no_releases",
                "channel": channel,
                "checked_at": checked_at,
                "current_version": APP_VERSION,
                "metadata_source": "github_releases",
                "installable": False,
                "message": (
                    f"GitHub is reachable, but the {channel_label(channel)} channel "
                    "does not contain a published InfoMancer release yet."
                ),
            }

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
            "metadata_source": "github_releases",
            "server_tag": tag,
            "installable": state == "available",
        }
        if state == "waiting_for_channel":
            status["message"] = (
                f"This installation is already newer than the latest "
                f"{channel_label(channel)} release ({tag}). This legacy release metadata "
                "does not contain a schema compatibility contract, so InfoMancer will not downgrade it."
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
        return status

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
                " InfoMancer will evaluate the recorded schema compatibility before any "
                "older build is considered. It will not perform an unproven downgrade."
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
        channel = read_update_channel(db.path)
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            if manifest_base_url():
                try:
                    status = status_from_manifest(channel, checked_at)
                except urllib.error.HTTPError as exc:
                    if exc.code != 404:
                        raise
                    # During 0C rollout, Standard/Beta may not have a manifest yet.
                    # A missing manifest falls back to the existing published-release
                    # path; an invalid manifest never does.
                    status = status_from_github_releases(channel, checked_at)
                    status["manifest_fallback"] = True
            else:
                status = status_from_github_releases(channel, checked_at)
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
                explanation = "The selected update channel metadata has not been published yet."
            elif isinstance(exc, urllib.error.HTTPError) and exc.code == 403:
                explanation = (
                    "The update metadata provider temporarily refused the check, usually because an API limit was reached."
                )
            else:
                explanation = (
                    "Update metadata could not be reached or did not pass validation."
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
            context={
                "channel": channel,
                "latest_version": status.get("latest_version", ""),
                "metadata_source": status.get("metadata_source", ""),
                "schema_assessment": (status.get("schema_assessment") or {}).get("status", ""),
            },
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
        trusted_tag = str(status.get("server_tag") or "").strip()
        if (
            status.get("status") != "available"
            or status.get("latest_version") != tag
            or status.get("channel") != channel
            or not status.get("installable")
            or not trusted_tag
        ):
            return redirect(
                "/settings/updates",
                "That build is not currently a verified installable server release for the selected channel. Check for updates again before applying it.",
            )
        try:
            safety = create_database_backup(db.path, "before-update")
            # The host updater receives only the trusted release tag. It still owns
            # GPG signature verification and refuses tags outside its trust policy.
            write_update_request(db.path, trusted_tag, request.state.user.username)
            write_update_status(db.path, {
                "status": "requested",
                "channel": channel,
                "current_version": APP_VERSION,
                "latest_version": tag,
                "server_tag": trusted_tag,
                "build_id": status.get("build_id", ""),
                "commit_sha": status.get("commit_sha", ""),
                "qualification_status": status.get("qualification_status", "published"),
                "database_schema": status.get("database_schema", {}),
                "schema_assessment": status.get("schema_assessment", {}),
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
            context={
                "backup": safety.name,
                "channel": channel,
                "trusted_tag": trusted_tag,
                "build_id": status.get("build_id", ""),
                "schema_assessment": (status.get("schema_assessment") or {}).get("status", ""),
            },
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

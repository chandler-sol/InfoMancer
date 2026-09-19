from __future__ import annotations

from fastapi import APIRouter, Depends

from ..access import require_librarian
from ..media_identity.external_config import (
    ExternalSourceConfigError,
    normalize_server_url,
    test_external_connection,
)
from ..provider_secrets import ProviderSecretError
from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    Form = ctx.get("Form")
    Request = ctx.get("Request")
    external_source_config = ctx.live("external_source_config")
    provider_secrets = ctx.live("provider_secrets")
    record_event = ctx.live("record_event")
    redirect = ctx.live("redirect")
    render_settings = ctx.live("render_settings")

    def librarian_get(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.get(path, dependencies=dependencies, **kwargs)

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

    @librarian_post("/settings/integrations/{source_key}")
    def save_external_source(
        request: Request,
        source_key: str,
        enabled: str = Form(""),
        server_url: str = Form(""),
        metadata_root: str = Form(""),
        token: str = Form(""),
        clear_token: str = Form(""),
    ):
        key = source_key.strip().casefold()
        secret_key = f"{key}_token"
        previous = None
        saved = None
        try:
            previous = external_source_config.source(key)
            current_secrets = provider_secrets.load()
            new_token = token.strip()
            if new_token and clear_token:
                raise ExternalSourceConfigError(
                    "Choose either a replacement token or Remove saved token, not both."
                )
            normalized_url = normalize_server_url(server_url)
            endpoint_changed = normalized_url != previous.server_url
            has_saved_token = bool(current_secrets.get(secret_key, ""))
            if endpoint_changed and has_saved_token and not new_token and not clear_token:
                raise ExternalSourceConfigError(
                    f"The {key.title()} server URL changed. Enter a replacement token or "
                    "select Remove saved token so the credential cannot be sent to a different server."
                )
            will_have_token = bool(
                new_token
                or (has_saved_token and not clear_token and not endpoint_changed)
            )
            if enabled and not will_have_token:
                raise ExternalSourceConfigError(
                    f"Save a {key.title()} access token before enabling this integration."
                )
            saved = external_source_config.save_source(
                key,
                enabled=bool(enabled),
                server_url=normalized_url,
                metadata_root=metadata_root if key == "plex" else "",
            )
            if clear_token:
                provider_secrets.delete({secret_key})
            elif new_token:
                provider_secrets.update({secret_key: new_token})
            if (
                previous.server_url != saved.server_url
                or bool(new_token)
                or bool(clear_token)
            ):
                external_source_config.clear_connection_result(key)
        except ProviderSecretError as exc:
            if previous is not None and saved is not None:
                try:
                    external_source_config.save_source(
                        previous.source_key,
                        enabled=previous.enabled,
                        server_url=previous.server_url,
                        metadata_root=previous.metadata_root,
                        config=previous.config,
                    )
                except ExternalSourceConfigError:
                    pass
            record_event(
                "settings",
                f"{key.title() if key else 'External'} integration was not saved.",
                level="error",
                detail=str(exc),
                user_id=request.state.user.id,
            )
            return redirect("/settings/integrations", str(exc))
        except ExternalSourceConfigError as exc:
            record_event(
                "settings",
                f"{key.title() if key else 'External'} integration was not saved.",
                level="error",
                detail=str(exc),
                user_id=request.state.user.id,
            )
            return redirect("/settings/integrations", str(exc))

        record_event(
            "settings",
            f"{key.title()} integration settings saved.",
            context={
                "source_key": key,
                "enabled": saved.enabled,
                "server_url": saved.server_url,
                "token_changed": bool(token.strip() or clear_token),
            },
            user_id=request.state.user.id,
        )
        return redirect(
            "/settings/integrations",
            f"{key.title()} integration settings saved.",
        )

    @librarian_post("/settings/integrations/{source_key}/test")
    def test_source_connection(request: Request, source_key: str):
        key = source_key.strip().casefold()
        try:
            source = external_source_config.source(key)
            token = provider_secrets.load().get(f"{key}_token", "")
            result = test_external_connection(key, source.server_url, token)
            external_source_config.record_connection_result(result)
        except (ExternalSourceConfigError, ProviderSecretError) as exc:
            return redirect("/settings/integrations", str(exc))

        record_event(
            "settings",
            f"{key.title()} integration connection test {'succeeded' if result.ok else 'failed'}.",
            level="info" if result.ok else "warning",
            detail="" if result.ok else result.detail,
            context={
                "source_key": key,
                "server_name": result.server_name,
                "version": result.version,
            },
            user_id=request.state.user.id,
        )
        if not result.ok:
            return redirect("/settings/integrations", result.detail)
        identity = result.server_name or key.title()
        version = f" {result.version}" if result.version else ""
        return redirect(
            "/settings/integrations",
            f"Connected successfully to {identity}{version}.",
        )

    @librarian_post("/settings/integrations/{source_key}/mappings")
    def add_source_mapping(
        request: Request,
        source_key: str,
        external_root: str = Form(...),
        local_root: str = Form(...),
        priority: str = Form("100"),
    ):
        key = source_key.strip().casefold()
        try:
            mapping_id = external_source_config.add_mapping(
                key,
                external_root,
                local_root,
                priority=int(priority),
            )
        except (ExternalSourceConfigError, ValueError) as exc:
            return redirect("/settings/integrations", str(exc))
        record_event(
            "settings",
            f"{key.title()} path mapping added.",
            context={
                "source_key": key,
                "mapping_id": mapping_id,
                "external_root": external_root,
                "local_root": local_root,
            },
            user_id=request.state.user.id,
        )
        return redirect(
            "/settings/integrations",
            f"{key.title()} path mapping added.",
        )

    @librarian_post("/settings/integrations/{source_key}/mappings/{mapping_id}/delete")
    def delete_source_mapping(
        request: Request, source_key: str, mapping_id: int,
    ):
        key = source_key.strip().casefold()
        try:
            deleted = external_source_config.delete_mapping(key, mapping_id)
        except ExternalSourceConfigError as exc:
            return redirect("/settings/integrations", str(exc))
        if not deleted:
            return redirect(
                "/settings/integrations",
                "That path mapping no longer exists; nothing changed.",
            )
        record_event(
            "settings",
            f"{key.title()} path mapping removed.",
            context={"source_key": key, "mapping_id": mapping_id},
            user_id=request.state.user.id,
        )
        return redirect("/settings/integrations", "Path mapping removed.")

    @librarian_get("/settings/integrations/{source_key}/mapping-test")
    def test_source_mapping(
        request: Request, source_key: str, external_path: str = "",
    ):
        key = source_key.strip().casefold()
        if not external_path.strip():
            return redirect(
                "/settings/integrations",
                "Enter a complete external media path to test.",
            )
        try:
            result = external_source_config.test_mapping(key, external_path)
        except ExternalSourceConfigError as exc:
            return render_settings(
                request,
                "integrations",
                str(exc),
                status_code=400,
                extra={"mapping_test_source": key},
            )
        return render_settings(
            request,
            "integrations",
            extra={
                "mapping_test_source": key,
                "mapping_test": result,
            },
        )

    return router, {
        "save_external_source": save_external_source,
        "test_source_connection": test_source_connection,
        "add_source_mapping": add_source_mapping,
        "delete_source_mapping": delete_source_mapping,
        "test_source_mapping": test_source_mapping,
    }

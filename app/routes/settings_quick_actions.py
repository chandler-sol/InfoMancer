from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse

from ..access import require_librarian
from ..provider_secrets import ProviderSecretError
from ..tvdb import TVDBClient, TVDBError
from .context import RouteContext


def build_router(ctx: RouteContext):
    """Small Settings actions that should not send users back through onboarding."""
    router = APIRouter()
    check_source_health = ctx.live("check_source_health")
    db = ctx.live("db")
    mie = ctx.live("mie")
    provider_secrets = ctx.live("provider_secrets")
    record_event = ctx.live("record_event")
    redirect = ctx.live("redirect")
    tvdb = ctx.live("tvdb")

    def async_request(request: Request) -> bool:
        return (
            request.headers.get("x-infomancer-async") == "1"
            or "application/json" in request.headers.get("accept", "")
        )

    def credential_response(
        request: Request, detail: str, *, status_code: int = 200,
        configured: bool | None = None, pin_configured: bool | None = None,
        key_hint: str = "",
    ):
        if async_request(request):
            payload = {"ok": status_code < 400, "detail": detail}
            if configured is not None:
                payload.update({
                    "configured": configured,
                    "pin_configured": bool(pin_configured),
                    "key_hint": key_hint,
                })
            return JSONResponse(payload, status_code=status_code)
        return redirect("/settings/metadata", detail)

    @router.post(
        "/settings/metadata/tvdb-credentials",
        dependencies=[Depends(require_librarian)],
    )
    def save_tvdb_credentials(
        request: Request,
        api_key: str = Form(""),
        subscriber_pin: str = Form(""),
    ):
        candidate_key = api_key.strip() or str(tvdb.api_key or "").strip()
        candidate_pin = subscriber_pin.strip() or str(tvdb.pin or "").strip()
        if not candidate_key:
            return credential_response(
                request,
                "Enter a TVDB project API key before saving.",
                status_code=400,
            )

        candidate = TVDBClient(candidate_key, candidate_pin)
        try:
            candidate.test_connection()
        except TVDBError:
            return credential_response(
                request,
                "TVDB did not accept that API key and PIN. Check the credentials in your TVDB account, then try again.",
                status_code=400,
            )
        except Exception:
            return credential_response(
                request,
                "InfoMancer could not reach TVDB. Check this server's internet connection, then try again.",
                status_code=503,
            )

        try:
            provider_secrets.update({
                "tvdb_api_key": candidate_key,
                "tvdb_pin": candidate_pin,
            })
            stored = provider_secrets.load()
        except ProviderSecretError as exc:
            return credential_response(request, str(exc), status_code=500)

        # Replace the live provider object only after both verification and encrypted
        # persistence succeed. Every existing LiveRef consumer sees the new client.
        ctx.set("tvdb", candidate)
        ctx.set("stored_provider_secrets", stored)
        ctx.set("provider_secret_error", "")
        record_event(
            "settings",
            "TVDB credentials verified and updated from Metadata Settings.",
            user_id=request.state.user.id,
            context={"provider": "tvdb", "pin_configured": bool(candidate_pin)},
        )
        return credential_response(
            request,
            "TVDB credentials verified and saved securely.",
            configured=True,
            pin_configured=bool(candidate_pin),
            key_hint=f"Configured · ends in {candidate_key[-4:]}",
        )

    @router.post(
        "/roots/check-all",
        dependencies=[Depends(require_librarian)],
    )
    def check_all_root_connections(request: Request):
        with db.connect() as conn:
            root_ids = [
                int(row["id"])
                for row in conn.execute("SELECT id FROM roots ORDER BY kind,label,path,id")
            ]
        if not root_ids:
            return redirect("/sources", "There are no configured sources to check.")

        counts = {"healthy": 0, "degraded": 0, "offline": 0, "unknown": 0}
        skipped = 0
        for root_id in root_ids:
            try:
                result = check_source_health(root_id)
            except ValueError:
                skipped += 1
                continue
            status = str(result.get("status") or "unknown")
            counts[status if status in counts else "unknown"] += 1

        mie.analyze()
        attention = counts["degraded"] + counts["offline"] + counts["unknown"]
        record_event(
            "source-guard",
            f"Bulk source connection check completed for {len(root_ids) - skipped} source(s).",
            level="warning" if attention else "info",
            context={"checked": len(root_ids) - skipped, "skipped": skipped, **counts},
            user_id=request.state.user.id,
        )

        checked = len(root_ids) - skipped
        if attention == 0 and skipped == 0:
            return redirect(
                "/sources",
                f"Checked all {checked} source{'s' if checked != 1 else ''}. Every connection is healthy.",
            )
        parts = [f"{counts['healthy']} healthy"]
        if counts["degraded"]:
            parts.append(f"{counts['degraded']} degraded")
        if counts["offline"]:
            parts.append(f"{counts['offline']} offline")
        if counts["unknown"]:
            parts.append(f"{counts['unknown']} unknown")
        if skipped:
            parts.append(f"{skipped} changed before it could be checked")
        return redirect(
            "/sources",
            f"Checked {checked} source{'s' if checked != 1 else ''}: " + ", ".join(parts) + ".",
        )

    return router, {
        "save_tvdb_credentials": save_tvdb_credentials,
        "check_all_root_connections": check_all_root_connections,
    }

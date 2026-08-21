from __future__ import annotations

import re
import secrets
from urllib.parse import urlsplit

from fastapi import APIRouter
from jinja2 import BaseLoader

from .context import RouteContext


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
CSP_META = (
    '<meta http-equiv="Content-Security-Policy" content="'
    "default-src 'self'; object-src 'none'; img-src 'self' https: data:; "
    "style-src 'self'; style-src-elem 'self' 'nonce-{{ csp_nonce(request) }}'; "
    "style-src-attr 'unsafe-inline'; "
    "script-src 'self' 'nonce-{{ csp_nonce(request) }}'; script-src-attr 'none'; "
    "connect-src 'self'; base-uri 'self'; form-action 'self'"
    '">'
)
_SETTINGS_WARNING = """{% set secret_warning = deployment_secret_warning() %}
{% if secret_warning %}
<section class="panel security-hardening-warning" role="status">
  <p class="eyebrow">SECURITY HARDENING</p>
  <strong>Set a persistent application secret for remote access.</strong>
  <p>{{ secret_warning }}</p>
</section>
{% endif %}
"""


def _host(value: str) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    try:
        parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
    except ValueError:
        return ""
    return (parsed.hostname or "").casefold().rstrip(".")


def _deployment_secret_warning(settings) -> str:
    if getattr(settings, "sandbox", False) or str(
        getattr(settings, "application_secret", "") or ""
    ).strip():
        return ""
    hosts = {
        _host(getattr(settings, "public_url", "")),
        *(_host(value) for value in getattr(settings, "trusted_hosts", ()) or ()),
    }
    hosts.discard("")
    remotely_addressable = (
        getattr(settings, "auth_mode", "") == "cloudflare"
        or any(host not in LOCAL_HOSTS for host in hosts)
    )
    if not remotely_addressable:
        return ""
    return (
        "INFOMANCER_SECRET is not configured. Provider credentials are still encrypted, "
        "but an automatically generated encryption key can live beside the encrypted "
        "credential store. Set INFOMANCER_SECRET to a long random value and keep it "
        "outside the InfoMancer data directory."
    )


def _strip_member_export_paths(rows: list[dict], *, is_librarian: bool) -> list[dict]:
    if is_librarian:
        return rows
    sanitized: list[dict] = []
    for row in rows:
        item = dict(row)
        # Keep the export schema stable while withholding server topology from Members.
        item["source_path"] = ""
        item["file_path"] = ""
        sanitized.append(item)
    return sanitized


def _nonce(request) -> str:
    state = getattr(request, "state", None)
    if state is None:
        return ""
    value = getattr(state, "csp_nonce", "")
    if not value:
        value = secrets.token_urlsafe(24)
        state.csp_nonce = value
    return value


def _harden_template_source(template: str, source: str) -> str:
    # Every inline block receives the same request-local nonce. External scripts/styles
    # may also carry it harmlessly, which keeps the transform simple and future-proof.
    source = re.sub(
        r"<script(?![^>]*\bnonce=)(?=[\s>])",
        '<script nonce="{{ csp_nonce(request) }}"',
        source,
        flags=re.IGNORECASE,
    )
    source = re.sub(
        r"<style(?![^>]*\bnonce=)(?=[\s>])",
        '<style nonce="{{ csp_nonce(request) }}"',
        source,
        flags=re.IGNORECASE,
    )
    if "content-security-policy" not in source.casefold():
        source = re.sub(
            r"(<head(?:\s[^>]*)?>)",
            lambda match: match.group(1) + "\n  " + CSP_META,
            source,
            count=1,
            flags=re.IGNORECASE,
        )
    normalized = template.replace("\\", "/")
    if normalized.endswith("settings.html") and "security-hardening-warning" not in source:
        source = source.replace(
            "{% block content %}",
            "{% block content %}\n" + _SETTINGS_WARNING,
            1,
        )
    return source


class HardenedTemplateLoader(BaseLoader):
    """Apply security-only HTML transforms without duplicating template files."""

    def __init__(self, wrapped: BaseLoader) -> None:
        self.wrapped = wrapped

    def get_source(self, environment, template):
        source, filename, uptodate = self.wrapped.get_source(environment, template)
        return _harden_template_source(template, source), filename, uptodate

    def list_templates(self):
        return self.wrapped.list_templates()


def build_router(ctx: RouteContext):
    router = APIRouter()
    templates = ctx.live("templates")
    settings = ctx.live("settings")
    db = ctx.live("db")

    templates.env.globals["csp_nonce"] = _nonce
    templates.env.globals["deployment_secret_warning"] = (
        lambda: _deployment_secret_warning(settings)
    )
    if templates.env.loader and not isinstance(
        templates.env.loader, HardenedTemplateLoader
    ):
        templates.env.loader = HardenedTemplateLoader(templates.env.loader)

    original_export = ctx.get("library_export_rows")
    if original_export and not getattr(original_export, "_infomancer_security_wrapped", False):
        def secure_library_export_rows(user_id: int):
            rows = original_export(user_id)
            if int(user_id or 0) <= 0:
                # Disabled-auth mode represents its trusted local Librarian as user 0.
                return rows
            with db.connect() as conn:
                user = conn.execute(
                    "SELECT role FROM users WHERE id=?", (user_id,)
                ).fetchone()
            return _strip_member_export_paths(
                rows,
                is_librarian=bool(user and user["role"] == "librarian"),
            )

        secure_library_export_rows._infomancer_security_wrapped = True
        ctx.set("library_export_rows", secure_library_export_rows)

    return router, {}

from __future__ import annotations

import re
import secrets

from fastapi import APIRouter
from jinja2 import BaseLoader

from .context import RouteContext


CSP_META = (
    '<meta http-equiv="Content-Security-Policy" content="'
    "default-src 'self'; object-src 'none'; img-src 'self' https: data:; "
    "style-src 'self'; style-src-elem 'self' 'nonce-{{ csp_nonce(request) }}'; "
    "style-src-attr 'unsafe-inline'; "
    "script-src 'self' 'nonce-{{ csp_nonce(request) }}'; script-src-attr 'none'; "
    "connect-src 'self'; base-uri 'self'; form-action 'self'"
    '">'
)


def _nonce(request) -> str:
    state = getattr(request, "state", None)
    if state is None:
        return ""
    value = getattr(state, "csp_nonce", "")
    if not value:
        value = secrets.token_urlsafe(24)
        state.csp_nonce = value
    return value


def _harden_template_source(source: str) -> str:
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
    return source


class HardenedTemplateLoader(BaseLoader):
    """Apply CSP nonces without duplicating or manually rewriting templates."""

    def __init__(self, wrapped: BaseLoader) -> None:
        self.wrapped = wrapped

    def get_source(self, environment, template):
        source, filename, uptodate = self.wrapped.get_source(environment, template)
        return _harden_template_source(source), filename, uptodate

    def list_templates(self):
        return self.wrapped.list_templates()


def build_router(ctx: RouteContext):
    router = APIRouter()
    templates = ctx.live("templates")
    templates.env.globals["csp_nonce"] = _nonce
    if templates.env.loader and not isinstance(templates.env.loader, HardenedTemplateLoader):
        templates.env.loader = HardenedTemplateLoader(templates.env.loader)
    return router, {}

from __future__ import annotations

from fastapi import APIRouter

from .context import RouteContext


_SOURCE_RULES = {"source-stale", "source-offline", "source-degraded"}
_IDENTITY_REVIEW_RULES = {"unmatched-title", "identity-confidence-low"}
_DUPLICATE_RULES = {"duplicate-candidates", "duplicate-storage-recovery"}


def health_finding_href(finding: dict) -> str:
    """Return the most specific review destination for a Library Health finding.

    MIE findings often carry both a source id and a title id. Source ownership is
    useful evidence, but it must not steal title-specific actions and send them to
    Sources. Route explicit workflows first, then fall back from title to source.
    """
    rule_key = str(finding.get("rule_key") or "")
    title_id = finding.get("title_id")
    root_id = finding.get("root_id")

    if rule_key == "missing-episodes" and title_id:
        return f"/titles/{title_id}?show_missing=1#missing-panel"

    if rule_key == "metadata-identifiers-missing" and title_id:
        return f"/titles/{title_id}/tvdb"

    if rule_key in _IDENTITY_REVIEW_RULES and title_id:
        return f"/titles/{title_id}/identity"

    if rule_key in _DUPLICATE_RULES:
        return "/duplicates"

    if rule_key == "technical-details-missing":
        return "/settings/system#media-information"

    if rule_key in _SOURCE_RULES and root_id:
        return "/sources"

    if title_id:
        return f"/titles/{title_id}"

    if root_id:
        return "/sources"

    return "/library"


def build_router(ctx: RouteContext):
    """Install route policy on the live MIE class before review pages are used."""
    router = APIRouter()
    mie_service = ctx.get("mie")
    if mie_service is not None:
        # Patch the class, not only the current instance. Tests and runtime recovery
        # can replace main.mie with another MediaIntelligenceEngine instance and
        # still receive the same UI destination policy.
        type(mie_service).finding_href = staticmethod(health_finding_href)
    return router, {"health_finding_href": health_finding_href}

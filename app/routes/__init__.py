from __future__ import annotations

from .security_hardening import build_router as build_security_hardening_router
from .system import build_router as build_system_router
from .operations import build_router as build_operations_router
from .dashboard import build_router as build_dashboard_router
from .bulk_matching import build_router as build_bulk_matching_router
from .review import build_router as build_review_router
from .library import build_router as build_library_router
from .inspector_media import build_router as build_inspector_media_router
from .recovery import build_router as build_recovery_router
from .settings import build_router as build_settings_router
from .collections import build_router as build_collections_router
from .titles import build_router as build_titles_router

ROUTER_BUILDERS = (
    build_security_hardening_router,
    build_system_router,
    build_operations_router,
    build_dashboard_router,
    build_bulk_matching_router,
    build_review_router,
    build_library_router,
    build_inspector_media_router,
    build_recovery_router,
    build_settings_router,
    build_collections_router,
    build_titles_router,
)

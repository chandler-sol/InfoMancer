from __future__ import annotations

from .system import build_router as build_system_router
from .operations import build_router as build_operations_router
from .dashboard import build_router as build_dashboard_router
from .review import build_router as build_review_router
from .library import build_router as build_library_router
from .inspector_media import build_router as build_inspector_media_router
from .recovery import build_router as build_recovery_router
from .scheduled_tasks import build_router as build_scheduled_tasks_router
from .settings import build_router as build_settings_router
from .account_avatar import build_router as build_account_avatar_router
from .collections import build_router as build_collections_router
from .titles import build_router as build_titles_router

ROUTER_BUILDERS = (
    build_system_router,
    build_operations_router,
    build_dashboard_router,
    build_review_router,
    build_library_router,
    build_inspector_media_router,
    build_recovery_router,
    build_scheduled_tasks_router,
    build_settings_router,
    build_account_avatar_router,
    build_collections_router,
    build_titles_router,
)

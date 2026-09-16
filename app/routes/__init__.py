from __future__ import annotations

from functools import wraps

from .cycle1_intelligence_foundation import build_router as build_cycle1_intelligence_foundation_router
from .security_hardening import build_router as build_security_hardening_router
from .resilience import build_router as build_resilience_router
from .final_polish import build_router as build_final_polish_router
from .worker_maintenance import build_router as build_worker_maintenance_router
from .duplicate_verification_maintenance import build_router as build_duplicate_verification_maintenance_router
from .release_081_announcements import build_router as build_release_081_announcements_router
from .release_081_stabilization import build_router as build_release_081_stabilization_router
from .release_081_collection_undo import build_router as build_release_081_collection_undo_router
from .health_action_routing import build_router as build_health_action_routing_router
from .performance import build_router as build_performance_router
from .system import build_router as build_system_router
from .operations import build_router as build_operations_router
from .dashboard import build_router as build_dashboard_router
from .bulk_match_progress import build_router as build_bulk_match_progress_router
from .bulk_match_apply import build_router as build_bulk_match_apply_router
from .bulk_match_review import build_router as build_bulk_match_review_router
from .review import build_router as build_review_router
from .library_optimized import build_router as build_library_router
from .inspector_media import build_router as build_inspector_media_router
from .recovery import build_router as build_recovery_router
from .scheduled_tasks import build_router as build_scheduled_tasks_router
from .source_commit import build_router as build_source_commit_router
from .source_health import build_router as build_source_health_router
from .update_channel_settings import build_router as build_update_channel_settings_router
from .settings import build_router as build_settings_router
from .settings_quick_actions import build_router as build_settings_quick_actions_router
from .account_avatar import build_router as build_account_avatar_router
from .user_management import build_router as build_user_management_router
from .metadata_maintenance import build_router as build_metadata_maintenance_router
from .collections import build_router as build_collections_router
from .title_bulk_actions import build_router as build_title_bulk_actions_router
from .title_media_info import build_router as build_title_media_info_router
from .title_metadata_async import build_router as build_title_metadata_async_router
from .movie_manual_match import build_router as build_movie_manual_match_router
from .titles import build_router as build_titles_router


def _without_shadowed_routes(builder, *method_paths: tuple[str, str]):
    """Keep legacy handler aliases while refusing duplicate route registration."""
    shadowed = {(method.upper(), path) for method, path in method_paths}

    @wraps(builder)
    def build_without_shadowed_routes(ctx):
        router, handlers = builder(ctx)
        router.routes[:] = [
            route
            for route in router.routes
            if not any(
                (method.upper(), getattr(route, "path", "")) in shadowed
                for method in (getattr(route, "methods", set()) or set())
            )
        ]
        return router, handlers

    return build_without_shadowed_routes


build_operations_router = _without_shadowed_routes(
    build_operations_router,
    ("POST", "/titles/{title_id}/imdb-refresh"),
)
build_review_router = _without_shadowed_routes(
    build_review_router,
    ("GET", "/movies/bulk-match"),
    ("POST", "/movies/bulk-match"),
    ("GET", "/shows/bulk-match"),
    ("POST", "/shows/bulk-match"),
    ("POST", "/duplicates/bulk-action"),
    ("POST", "/duplicates/{file_a_id}/{file_b_id}/verify"),
)
build_title_bulk_actions_router = _without_shadowed_routes(
    build_title_bulk_actions_router,
    ("POST", "/api/titles/{title_id}/favorite"),
)
build_settings_router = _without_shadowed_routes(
    build_settings_router,
    ("GET", "/maintenance/diagnostics"),
    ("POST", "/roots"),
)
build_collections_router = _without_shadowed_routes(
    build_collections_router,
    ("POST", "/collections/{collection_id}/delete"),
)
build_titles_router = _without_shadowed_routes(
    build_titles_router,
    ("POST", "/titles/organize-bulk"),
    ("POST", "/titles/{title_id}/media-info"),
    ("POST", "/titles/{title_id}/movie/{movie_id}"),
)


ROUTER_BUILDERS = (
    # Service-only Cycle 1 replacements are installed first so every later route
    # builder sees the same MIE history engine and media-inspection worker.
    build_cycle1_intelligence_foundation_router,
    # Keep security hooks first among HTTP route owners as the canonical hardening
    # boundary. Its maintenance middleware renders/logs admitted API failures before
    # releasing admission; resilience remains the outer fallback for other failures.
    build_security_hardening_router,
    build_resilience_router,
    build_final_polish_router,
    build_worker_maintenance_router,
    # Duplicate verification is a database-writing background worker even though
    # it does not mutate media. Own these routes before the broader Review bundle.
    build_duplicate_verification_maintenance_router,
    build_release_081_announcements_router,
    build_release_081_stabilization_router,
    build_release_081_collection_undo_router,
    build_health_action_routing_router,
    build_title_metadata_async_router,
    build_performance_router,
    build_system_router,
    build_operations_router,
    build_dashboard_router,
    build_bulk_match_progress_router,
    build_bulk_match_apply_router,
    build_bulk_match_review_router,
    build_review_router,
    build_library_router,
    build_inspector_media_router,
    build_recovery_router,
    build_scheduled_tasks_router,
    build_source_commit_router,
    build_source_health_router,
    build_update_channel_settings_router,
    build_settings_router,
    build_settings_quick_actions_router,
    build_account_avatar_router,
    build_user_management_router,
    build_metadata_maintenance_router,
    build_collections_router,
    build_title_bulk_actions_router,
    build_title_media_info_router,
    build_movie_manual_match_router,
    build_titles_router,
)

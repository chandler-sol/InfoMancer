from __future__ import annotations

from .security_hardening import build_router as build_security_hardening_router
from .resilience import build_router as build_resilience_router
from .final_polish import build_router as build_final_polish_router
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

ROUTER_BUILDERS = (
    # Install cross-cutting security and error-shaping hooks before domain routers
    # capture their live helpers or Jinja templates.
    build_security_hardening_router,
    build_resilience_router,
    # Small release-polish hooks replace live helpers before the domain routes use
    # them, while keeping the existing public route contracts intact.
    build_final_polish_router,
    # Remove only the historical official announcements that older 0.8 builds baked
    # into every installation. Librarian-authored messages and future official keys
    # remain untouched.
    build_release_081_announcements_router,
    # Final RC stabilization owns mapped-drive-safe undo validation plus additive
    # bulk collection and Smart Collection editing routes before domain routers are
    # constructed.
    build_release_081_stabilization_router,
    # Collection deletion needs to own its POST route before the broader Collections
    # router so the release build can provide a real one-shot Undo snapshot.
    build_release_081_collection_undo_router,
    # Library Health findings can carry both source and title ids. Install the UI
    # destination policy before review/domain routes so title-specific actions do
    # not fall through to the generic Sources destination.
    build_health_action_routing_router,
    # The single-title metadata action must own its URL before any broader or
    # legacy route bundle is registered. The Metadata maintenance UI expects this
    # handler to finish the bounded TVDB refresh inside the request and return a
    # final JSON result rather than enqueueing the old bulk IMDb worker.
    build_title_metadata_async_router,
    build_performance_router,
    build_system_router,
    build_operations_router,
    build_dashboard_router,
    build_bulk_match_progress_router,
    # Own focused Bulk Match URLs before Review registers its legacy handlers.
    # Apply handles full selected sets, while review renders the complete cached
    # queue instead of exposing the old 50-row pagination boundary.
    build_bulk_match_apply_router,
    build_bulk_match_review_router,
    build_review_router,
    build_library_router,
    build_inspector_media_router,
    build_recovery_router,
    build_scheduled_tasks_router,
    # Source creation must be registered before the broader Settings bundle so
    # POST /roots uses the same Windows/NFS-safe path validation as the browser.
    build_source_commit_router,
    build_source_health_router,
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

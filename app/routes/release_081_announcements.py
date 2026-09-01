from __future__ import annotations

from fastapi import APIRouter

from .context import RouteContext


LEGACY_PACKAGED_OFFICIAL_KEYS = (
    "release-notes-since-0.4-2026-08-06",
    "release-0.5.0-alpha.1",
    "release-0.4.0-alpha.1",
    "release-0.3.0",
)


def remove_legacy_packaged_announcements(database) -> int:
    """Remove the old baked-in release notices without touching real messages.

    0.8 previously seeded historical release announcements into every installation.
    Keep Librarian-authored installation messages and any future official source keys
    intact, but remove the four legacy package fixtures and their receipt history.
    """
    placeholders = ",".join("?" for _ in LEGACY_PACKAGED_OFFICIAL_KEYS)
    with database.connect() as conn:
        cursor = conn.execute(
            f"""DELETE FROM announcements
                WHERE source='official' AND source_key IN ({placeholders})""",
            LEGACY_PACKAGED_OFFICIAL_KEYS,
        )
        return int(cursor.rowcount or 0)


def build_router(ctx: RouteContext):
    # main.py still calls EngagementService.seed_official() for compatibility with
    # the existing engagement service/tests. Reconcile the 0.8 release install after
    # that seed runs so fresh and upgraded installations do not expose those old
    # announcements. A later developer-announcement transport can replace the seed.
    database = ctx.live("db")
    remove_legacy_packaged_announcements(database)
    return APIRouter(), {
        "remove_legacy_packaged_announcements": remove_legacy_packaged_announcements,
    }

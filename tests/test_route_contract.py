from __future__ import annotations

import os
import unittest
from collections import defaultdict

from fastapi.routing import APIRoute


# Route ownership is a composition contract, not an authentication behavior test.
# Disabled auth keeps import-time setup deterministic while still assembling the
# same application route table used by normal deployments.
os.environ.setdefault("INFOMANCER_AUTH_MODE", "disabled")

import app.main as main


CANONICAL_OWNERS = {
    ("GET", "/maintenance/diagnostics"):
        "app.routes.security_hardening.download_sanitized_diagnostics",
    ("GET", "/movies/bulk-match"):
        "app.routes.bulk_match_review.bulk_movie_match_review",
    ("GET", "/shows/bulk-match"):
        "app.routes.bulk_match_review.bulk_tv_match_review",
    ("POST", "/api/titles/{title_id}/favorite"):
        "app.routes.library.workspace_toggle_favorite",
    ("POST", "/collections/{collection_id}/delete"):
        "app.routes.release_081_collection_undo.delete_collection_with_undo",
    ("POST", "/movies/bulk-match"):
        "app.routes.bulk_match_apply.bulk_movie_match_apply",
    ("POST", "/roots"):
        "app.routes.source_commit.add_root_safe",
    ("POST", "/shows/bulk-match"):
        "app.routes.bulk_match_apply.bulk_tv_match_apply",
    ("POST", "/titles/organize-bulk"):
        "app.routes.title_bulk_actions.organize_titles_bulk_action",
    ("POST", "/titles/{title_id}/imdb-refresh"):
        "app.routes.title_metadata_async.refresh_title_metadata",
    ("POST", "/titles/{title_id}/media-info"):
        "app.routes.title_media_info.inspect_title_media_action",
    ("POST", "/titles/{title_id}/movie/{movie_id}"):
        "app.routes.movie_manual_match.match_movie",
}


def route_owners() -> dict[tuple[str, str], list[str]]:
    owners: dict[tuple[str, str], list[str]] = defaultdict(list)
    for route in main.app.routes:
        if not isinstance(route, APIRoute):
            continue
        owner = f"{route.endpoint.__module__}.{route.endpoint.__name__}"
        for method in sorted(route.methods or ()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            owners[(method, route.path)].append(owner)
    return owners


class RouteOwnershipContractTests(unittest.TestCase):
    maxDiff = None

    def test_each_method_path_pair_has_one_effective_owner(self):
        owners = route_owners()
        duplicates = {
            f"{method} {path}": route_names
            for (method, path), route_names in sorted(owners.items())
            if len(route_names) > 1
        }
        self.assertEqual(
            duplicates,
            {},
            "Duplicate method/path ownership makes authorization and handler selection order-dependent.",
        )

    def test_hardened_and_focused_routes_keep_their_canonical_owner(self):
        owners = route_owners()
        observed = {
            key: route_names[0] if len(route_names) == 1 else route_names
            for key, route_names in owners.items()
            if key in CANONICAL_OWNERS
        }
        self.assertEqual(observed, CANONICAL_OWNERS)


if __name__ == "__main__":
    unittest.main()

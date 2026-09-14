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


class RouteOwnershipContractTests(unittest.TestCase):
    def test_each_method_path_pair_has_one_effective_owner(self):
        owners: dict[tuple[str, str], list[str]] = defaultdict(list)
        for route in main.app.routes:
            if not isinstance(route, APIRoute):
                continue
            owner = f"{route.endpoint.__module__}.{route.endpoint.__name__}"
            for method in sorted(route.methods or ()):
                if method in {"HEAD", "OPTIONS"}:
                    continue
                owners[(method, route.path)].append(owner)

        duplicates = {
            f"{method} {path}": route_owners
            for (method, path), route_owners in sorted(owners.items())
            if len(route_owners) > 1
        }
        self.assertEqual(
            duplicates,
            {},
            "Duplicate method/path ownership makes authorization and handler selection order-dependent.",
        )


if __name__ == "__main__":
    unittest.main()

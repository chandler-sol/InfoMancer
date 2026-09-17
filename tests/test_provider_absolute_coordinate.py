from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.media_identity.provider_cache import ProviderEpisodeCache


class AbsoluteOnlyTVDBTransport:
    def _get(
        self, path: str, params: dict | None = None, *,
        allow_not_found: bool = False, _retry_auth: bool = True,
    ) -> dict:
        if path == "/series/9001/extended":
            return {
                "data": {
                    "defaultSeasonType": 1,
                    "lastUpdated": "2026-09-17 12:00:00",
                    "seasons": [],
                }
            }
        if path == "/series/9001/episodes/default/eng":
            return {
                "data": {
                    "episodes": [
                        {
                            "id": 101,
                            "name": "Absolute One",
                            "overview": "First absolute episode",
                            "absoluteNumber": 1,
                        },
                        {
                            "id": 102,
                            "name": "Absolute Two",
                            "overview": "Second absolute episode",
                            "absoluteNumber": 2,
                        },
                    ]
                },
                "links": {"next": None},
            }
        if allow_not_found:
            return {}
        raise AssertionError(f"Unexpected TVDB request: {path}")


class ProviderAbsoluteCoordinateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        self.cache = ProviderEpisodeCache(self.database)
        self.cache.refresh_tvdb_series(9001, AbsoluteOnlyTVDBTransport())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_absolute_only_coordinate_resolves_exact_episode(self) -> None:
        first = self.cache.resolve_coordinate(
            "tvdb", "9001", "default", None, None, absolute_number=1,
        )
        second = self.cache.resolve_coordinate(
            "tvdb", "9001", "default", None, None, absolute_number=2,
        )

        self.assertFalse(first["ambiguous"])
        self.assertFalse(second["ambiguous"])
        self.assertEqual(
            [item["provider_episode_id"] for item in first["candidates"]],
            ["101"],
        )
        self.assertEqual(
            [item["provider_episode_id"] for item in second["candidates"]],
            ["102"],
        )
        self.assertEqual(
            first["candidates"][0]["mapping_variants"][0]["absolute_number"],
            1,
        )

    def test_lookup_without_any_coordinate_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires season/episode or an absolute number"):
            self.cache.resolve_coordinate("tvdb", "9001", "default", None, None)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from app.media_identity.tvdb_orders import episode_orders


class ExtendedSeriesTransport:
    def __init__(self, data: dict):
        self.data = data
        self.calls: list[str] = []

    def _get(
        self, path: str, params: dict | None = None, *,
        allow_not_found: bool = False, _retry_auth: bool = True,
    ) -> dict:
        self.calls.append(path)
        if path != "/series/9001/extended":
            raise AssertionError(f"Unexpected TVDB request: {path}")
        return {"data": self.data}


class TVDBOrderDiscoveryTests(unittest.TestCase):
    def test_discovers_and_deduplicates_types_from_real_series_seasons_shape(self) -> None:
        transport = ExtendedSeriesTransport({
            "defaultSeasonType": 1,
            "lastUpdated": "2026-09-17 12:00:00",
            "seasons": [
                {
                    "id": 100,
                    "number": 0,
                    "type": {"id": 1, "name": "Aired Order", "type": "official"},
                },
                {
                    "id": 101,
                    "number": 1,
                    "type": {"id": 1, "name": "Aired Order", "type": "official"},
                },
                {
                    "id": 201,
                    "number": 1,
                    "type": {"id": 2, "name": "DVD Order", "type": "dvd"},
                },
                {
                    "id": 301,
                    "number": 1,
                    "type": {
                        "id": 3,
                        "name": "Alternate Order",
                        "alternateName": "Story Order",
                        "type": "alternate",
                    },
                },
            ],
        })

        result = episode_orders(transport, 9001)

        self.assertEqual(transport.calls, ["/series/9001/extended"])
        self.assertEqual(
            [item["namespace"] for item in result["orders"]],
            ["default", "official", "alternate", "dvd"],
        )
        self.assertEqual(
            sum(item["namespace"] == "official" for item in result["orders"]),
            1,
            "the same season type repeated across numbered seasons must be deduplicated",
        )
        official = next(item for item in result["orders"] if item["namespace"] == "official")
        alternate = next(item for item in result["orders"] if item["namespace"] == "alternate")
        self.assertTrue(official["default"])
        self.assertEqual(official["provider_type_id"], 1)
        self.assertEqual(alternate["name"], "Story Order")
        self.assertEqual(result["provider_updated_at"], "2026-09-17 12:00:00")

    def test_keeps_top_level_season_types_compatibility(self) -> None:
        transport = ExtendedSeriesTransport({
            "defaultSeasonType": 1,
            "seasonTypes": [
                {"id": 1, "name": "Aired Order", "type": "official"},
                {"id": 2, "name": "DVD Order", "type": "dvd"},
            ],
            "seasons": [],
        })

        result = episode_orders(transport, 9001)

        self.assertEqual(
            [item["namespace"] for item in result["orders"]],
            ["default", "official", "dvd"],
        )
        self.assertTrue(next(
            item for item in result["orders"] if item["namespace"] == "official"
        )["default"])

    def test_merges_duplicate_types_across_both_response_shapes(self) -> None:
        transport = ExtendedSeriesTransport({
            "defaultSeasonType": 1,
            "seasonTypes": [
                {"id": 1, "name": "Aired Order", "type": "official"},
            ],
            "seasons": [
                {
                    "id": 101,
                    "number": 1,
                    "type": {"id": 1, "name": "Aired Order", "type": "official"},
                },
                {
                    "id": 201,
                    "number": 1,
                    "type": {"id": 2, "name": "DVD Order", "type": "dvd"},
                },
            ],
        })

        result = episode_orders(transport, 9001)

        self.assertEqual(
            [item["namespace"] for item in result["orders"]],
            ["default", "official", "dvd"],
        )


if __name__ == "__main__":
    unittest.main()

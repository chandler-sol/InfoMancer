from __future__ import annotations

import json
import unittest

from app.media_identity.sources.jellyfin import (
    JellyfinAdapterError,
    enumerate_preview_frames,
    external_paths_equal,
    parse_trickplay_variants,
    resolve_media_ref,
    select_trickplay_variant,
    trickplay_source_signature,
)


class JellyfinTrickplayFoundationTests(unittest.TestCase):
    def _item(self):
        return {
            "Id": "episode-1",
            "Etag": "etag-a",
            "Path": "/srv/tv/Show/Season 01/Episode.mkv",
            "ProviderIds": {"Tvdb": "12345"},
            "MediaSources": [
                {
                    "Id": "media-a",
                    "Path": "/srv/tv/Show/Season 01/Episode.mkv",
                }
            ],
            "Trickplay": {
                "media-a": {
                    "320": {
                        "Width": 320,
                        "Height": 180,
                        "TileWidth": 3,
                        "TileHeight": 2,
                        "ThumbnailCount": 7,
                        "Interval": 10_000,
                        "Bandwidth": 120_000,
                    },
                    "480": {
                        "Width": 480,
                        "Height": 270,
                        "TileWidth": 3,
                        "TileHeight": 2,
                        "ThumbnailCount": 7,
                        "Interval": 10_000,
                        "Bandwidth": 180_000,
                    },
                }
            },
        }

    def test_parse_and_select_highest_resolution_for_resolved_media_source(self):
        variants = parse_trickplay_variants(self._item())
        self.assertEqual(len(variants), 2)
        selected = select_trickplay_variant(variants, media_source_id="media-a")
        self.assertIsNotNone(selected)
        self.assertEqual(selected.width, 480)
        self.assertEqual(selected.height, 270)
        self.assertEqual(selected.tile_count, 2)

    def test_frame_enumeration_maps_timestamps_to_tile_cells(self):
        item = self._item()
        variant = select_trickplay_variant(
            parse_trickplay_variants(item), media_source_id="media-a"
        )
        frames = enumerate_preview_frames(
            item_id=item["Id"], item_etag=item["Etag"], variant=variant
        )
        self.assertEqual(len(frames), 7)
        self.assertEqual([frame.timestamp_ms for frame in frames], [0, 10000, 20000, 30000, 40000, 50000, 60000])

        fifth = json.loads(frames[5].asset_ref)
        sixth = json.loads(frames[6].asset_ref)
        self.assertEqual(fifth["tile_index"], 0)
        self.assertEqual((fifth["row"], fifth["column"]), (1, 2))
        self.assertEqual(sixth["tile_index"], 1)
        self.assertEqual((sixth["row"], sixth["column"]), (0, 0))
        self.assertEqual(frames[0].source_signature, frames[-1].source_signature)
        self.assertEqual(frames[0].width, 480)
        self.assertEqual(frames[0].height, 270)

    def test_source_signature_changes_with_etag_or_manifest(self):
        item = self._item()
        variant = select_trickplay_variant(
            parse_trickplay_variants(item), media_source_id="media-a"
        )
        first = trickplay_source_signature(item["Id"], "etag-a", variant)
        second = trickplay_source_signature(item["Id"], "etag-b", variant)
        changed = type(variant)(
            media_source_id=variant.media_source_id,
            width=variant.width,
            height=variant.height,
            tile_width=variant.tile_width,
            tile_height=variant.tile_height,
            thumbnail_count=variant.thumbnail_count,
            interval_ms=variant.interval_ms + 1,
            bandwidth=variant.bandwidth,
        )
        third = trickplay_source_signature(item["Id"], "etag-a", changed)
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, third)

    def test_malformed_variants_are_skipped_without_poisoning_valid_width(self):
        item = self._item()
        item["Trickplay"]["media-a"]["640"] = {
            "Width": 320,
            "Height": 360,
            "TileWidth": 4,
            "TileHeight": 4,
            "ThumbnailCount": 10,
            "Interval": 5000,
        }
        item["Trickplay"]["media-a"]["800"] = {
            "Width": 800,
            "Height": 450,
            "TileWidth": 0,
            "TileHeight": 4,
            "ThumbnailCount": 10,
            "Interval": 5000,
        }
        widths = [value.width for value in parse_trickplay_variants(item)]
        self.assertEqual(widths, [320, 480])

    def test_absurd_thumbnail_count_is_rejected_fail_closed(self):
        item = self._item()
        item["Trickplay"]["media-a"]["480"]["ThumbnailCount"] = 1_000_001
        variants = parse_trickplay_variants(item)
        self.assertEqual([value.width for value in variants], [320])

    def test_variant_selection_never_crosses_media_sources(self):
        item = self._item()
        item["Trickplay"]["media-b"] = {
            "1080": {
                "Width": 1080,
                "Height": 608,
                "TileWidth": 4,
                "TileHeight": 4,
                "ThumbnailCount": 10,
                "Interval": 5000,
            }
        }
        variants = parse_trickplay_variants(item)
        selected = select_trickplay_variant(variants, media_source_id="media-a")
        self.assertEqual(selected.width, 480)
        self.assertIsNone(select_trickplay_variant(variants, media_source_id=""))

    def test_windows_external_paths_compare_case_insensitively(self):
        self.assertTrue(
            external_paths_equal(
                r"D:\\TV\\Show\\Episode.mkv",
                r"d:\\tv\\show\\episode.mkv",
            )
        )

    def test_posix_external_paths_remain_case_sensitive(self):
        self.assertFalse(
            external_paths_equal(
                "/srv/TV/Show/Episode.mkv",
                "/srv/tv/Show/Episode.mkv",
            )
        )

    def test_media_resolution_requires_exact_mapped_path(self):
        ref = resolve_media_ref(
            [self._item()],
            expected_external_path="/srv/tv/Show/Season 01/Episode.mkv",
        )
        self.assertIsNotNone(ref)
        self.assertEqual(ref.item_id, "episode-1")
        self.assertEqual(ref.media_source_id, "media-a")
        self.assertEqual(ref.provider_ids, {"Tvdb": "12345"})
        self.assertIn("etag-a", ref.source_signature)

        miss = resolve_media_ref(
            [self._item()],
            expected_external_path="/srv/tv/Show/Season 01/Different.mkv",
        )
        self.assertIsNone(miss)

    def test_duplicate_exact_item_path_fails_closed(self):
        first = self._item()
        second = dict(first)
        second["Id"] = "episode-2"
        with self.assertRaisesRegex(JellyfinAdapterError, "ambiguous"):
            resolve_media_ref(
                [first, second],
                expected_external_path=first["Path"],
            )

    def test_media_source_path_match_beats_unrelated_alternate_version(self):
        item = self._item()
        item["MediaSources"] = [
            {"Id": "media-other", "Path": "/srv/other/Episode.mkv"},
            {"Id": "media-a", "Path": item["Path"]},
        ]
        ref = resolve_media_ref([item], expected_external_path=item["Path"])
        self.assertEqual(ref.media_source_id, "media-a")

    def test_ambiguous_media_source_path_fails_closed(self):
        item = self._item()
        item["MediaSources"] = [
            {"Id": "media-a", "Path": item["Path"]},
            {"Id": "media-b", "Path": item["Path"]},
        ]
        with self.assertRaisesRegex(JellyfinAdapterError, "media source.*ambiguous"):
            resolve_media_ref([item], expected_external_path=item["Path"])


if __name__ == "__main__":
    unittest.main()

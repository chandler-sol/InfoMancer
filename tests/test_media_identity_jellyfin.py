from __future__ import annotations

from io import BytesIO
import json
import tempfile
import unittest
import urllib.error
import urllib.request
import urllib.parse
from unittest.mock import patch
from pathlib import Path

from PIL import Image

from app.media_identity.external import (
    ExternalPreviewUnavailable,
    ExternalSourceFailure,
)
from app.media_identity.models import (
    AnalyzerContext,
    IdentityProfile,
    IdentityReference,
    MediaIdentityFile,
)
from app.path_mapping import ExternalPathMapper, PathMapping

from app.media_identity.sources.jellyfin import (
    JellyfinAdapterError,
    JellyfinPreviewUnavailable,
    JellyfinSourceFailure,
    JellyfinTrickplaySource,
    crop_trickplay_frame,
    enumerate_preview_frames,
    external_paths_equal,
    fetch_episode_candidates,
    fetch_trickplay_tile,
    parse_trickplay_variants,
    resolve_media_ref,
    read_trickplay_preview,
    select_trickplay_variant,
    trickplay_source_signature,
    trickplay_tile_url,
)





class DummyHeaders(dict):
    def get_content_type(self):
        return str(self.get("Content-Type") or "").split(";", 1)[0].strip().casefold()


class DummyResponse:
    def __init__(self, payload: bytes, *, status: int = 200, content_type: str = "image/jpeg", content_length: str | None = None):
        self.status = status
        self.payload = payload
        self.headers = DummyHeaders({"Content-Type": content_type})
        if content_length is not None:
            self.headers["Content-Length"] = content_length

    def read(self, limit: int = -1):
        return self.payload if limit < 0 else self.payload[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class DummyOpener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.request = None
        self.timeout = None

    def open(self, request, timeout=None):
        self.request = request
        self.timeout = timeout
        if self.error is not None:
            raise self.error
        return self.response


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
        item["Trickplay"]["media-a"]["480"]["ThumbnailCount"] = 50_001
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

    def test_alternate_media_source_path_resolves_exact_version(self):
        item = self._item()
        expected = "/srv/tv/Show/Season 01/Alternate.mkv"
        item["Path"] = "/srv/tv/Show/Season 01/Primary.mkv"
        item["MediaSources"] = [
            {"Id": "media-primary", "Path": item["Path"]},
            {"Id": "media-alt", "Path": expected},
        ]

        ref = resolve_media_ref([item], expected_external_path=expected)

        self.assertIsNotNone(ref)
        self.assertEqual(ref.path, expected)
        self.assertEqual(ref.media_source_id, "media-alt")

    def test_malformed_media_source_entry_fails_closed(self):
        item = self._item()
        item["MediaSources"] = ["malformed"]
        with self.assertRaisesRegex(
            JellyfinAdapterError, "malformed media source entry"
        ):
            resolve_media_ref([item], expected_external_path=item["Path"])

    def test_media_source_without_id_fails_closed_before_fallback(self):
        item = self._item()
        item["MediaSources"] = [
            {"Path": "/srv/tv/Show/Season 01/Other.mkv"},
            {"Id": "media-only"},
        ]
        with self.assertRaisesRegex(
            JellyfinAdapterError, "without a stable id"
        ):
            resolve_media_ref([item], expected_external_path=item["Path"])

    def test_single_declared_nonmatching_media_source_does_not_fallback(self):
        item = self._item()
        item["MediaSources"] = [
            {"Id": "media-only", "Path": "/srv/tv/Other/Version.mkv"},
        ]
        ref = resolve_media_ref(
            [item],
            expected_external_path=item["Path"],
        )
        self.assertIsNotNone(ref)
        self.assertEqual(ref.media_source_id, "")

    def test_episode_candidate_query_is_bounded_read_only_and_token_safe(self):
        payload = json.dumps(
            {
                "Items": [
                    {
                        "Id": "11111111111111111111111111111111",
                        "Path": "/srv/tv/Show/Season 01/Episode.mkv",
                    }
                ],
                "TotalRecordCount": 1,
            }
        ).encode("utf-8")
        opener = DummyOpener(
            DummyResponse(payload, content_type="application/json")
        )
        with patch(
            "app.media_identity.sources.jellyfin.urllib.request.build_opener",
            return_value=opener,
        ) as builder:
            items = fetch_episode_candidates(
                "https://jellyfin.local:8096",
                "top-secret",
                season=1,
                episode=2,
                timeout=3,
            )

        self.assertEqual(len(items), 1)
        parsed = urllib.parse.urlsplit(opener.request.full_url)
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/Items")
        self.assertEqual(query["recursive"], ["true"])
        self.assertEqual(query["includeItemTypes"], ["Episode"])
        self.assertEqual(query["parentIndexNumber"], ["1"])
        self.assertEqual(query["indexNumber"], ["2"])
        self.assertEqual(query["fields"], ["Path,ProviderIds,MediaSources"])
        self.assertEqual(query["startIndex"], ["0"])
        self.assertEqual(query["limit"], ["256"])
        self.assertNotIn("top-secret", opener.request.full_url)
        self.assertEqual(opener.request.get_header("X-emby-token"), "top-secret")
        proxy_handlers = [
            handler
            for handler in builder.call_args.args
            if isinstance(handler, urllib.request.ProxyHandler)
        ]
        self.assertEqual(len(proxy_handlers), 1)
        self.assertEqual(proxy_handlers[0].proxies, {})

    def test_episode_candidate_query_rejects_missing_continuation_page(self):
        starts = []

        def paged_read(
            _server,
            _token,
            _path,
            *,
            query,
            timeout,
            allow_insecure_http,
        ):
            start = int(query["startIndex"])
            starts.append(start)
            if start == 0:
                return {
                    "Items": [
                        {
                            "Id": "11111111111111111111111111111111",
                            "Path": "/srv/tv/Show/Season 01/Episode.mkv",
                        }
                    ],
                    "TotalRecordCount": 2,
                    "StartIndex": 0,
                }
            if start == 1:
                return {
                    "Items": [],
                    "TotalRecordCount": 2,
                    "StartIndex": 1,
                }
            self.fail(f"unexpected page start {start}")

        with patch(
            "app.media_identity.sources.jellyfin._read_jellyfin_json",
            side_effect=paged_read,
        ):
            with self.assertRaises(JellyfinSourceFailure) as caught:
                fetch_episode_candidates(
                    "https://jellyfin.local:8096",
                    "token",
                    season=1,
                    episode=2,
                )
        self.assertEqual(starts, [0, 1])
        self.assertIsInstance(caught.exception, ExternalSourceFailure)
        self.assertIn("ended before all candidates", str(caught.exception))

    def test_episode_candidate_query_rejects_malformed_entries(self):
        payload = json.dumps(
            {
                "Items": [
                    {
                        "Id": "11111111111111111111111111111111",
                        "Path": "/srv/tv/Show/Season 01/Episode.mkv",
                    },
                    "malformed",
                ],
                "TotalRecordCount": 2,
            }
        ).encode("utf-8")
        opener = DummyOpener(
            DummyResponse(payload, content_type="application/json")
        )
        with patch(
            "app.media_identity.sources.jellyfin.urllib.request.build_opener",
            return_value=opener,
        ):
            with self.assertRaisesRegex(JellyfinAdapterError, "malformed candidate"):
                fetch_episode_candidates(
                    "https://jellyfin.local:8096",
                    "token",
                    season=1,
                    episode=2,
                )

    def test_configured_source_resolves_exact_mapped_episode_and_enumerates_trickplay(self):
        item_id = "11111111111111111111111111111111"
        media_source_id = "22222222222222222222222222222222"
        external_path = "/srv/tv/Show/Season 01/Episode.mkv"
        with tempfile.TemporaryDirectory() as temporary:
            local_root = Path(temporary) / "tv"
            local_path = local_root / "Show" / "Season 01" / "Episode.mkv"
            mapper = ExternalPathMapper(
                [PathMapping("jellyfin", "/srv/tv", str(local_root))]
            )
            source = JellyfinTrickplaySource(
                "https://jellyfin.local:8096",
                "jf-secret",
                mapper,
            )
            context = AnalyzerContext(
                media=MediaIdentityFile(
                    file_id=1,
                    title_id=1,
                    path=str(local_path),
                    size_bytes=1,
                    modified_at=1.0,
                ),
                claimed_identity=IdentityReference(
                    identity_kind="episode",
                    season=1,
                    episode=2,
                    display_name="Episode",
                ),
                profile=IdentityProfile.DEEP,
            )
            candidate = {
                "Id": "11111111-1111-1111-1111-111111111111",
                "Path": external_path,
            }
            detail = {
                "Id": item_id,
                "Etag": "etag-source",
                "Path": external_path,
                "ProviderIds": {"Tvdb": "12345"},
                "MediaSources": [
                    {
                        "Id": media_source_id,
                        "Path": external_path,
                    }
                ],
                "Trickplay": {
                    media_source_id: {
                        "8": {
                            "Width": 8,
                            "Height": 6,
                            "TileWidth": 2,
                            "TileHeight": 2,
                            "ThumbnailCount": 4,
                            "Interval": 1000,
                            "Bandwidth": 1000,
                        }
                    }
                },
            }

            with (
                patch(
                    "app.media_identity.sources.jellyfin.fetch_episode_candidates",
                    return_value=(candidate,),
                ) as candidates,
                patch(
                    "app.media_identity.sources.jellyfin.fetch_item",
                    return_value=detail,
                ) as item_fetch,
            ):
                media = source.resolve_media(context)
                self.assertIsNotNone(media)
                frames = source.preview_frames(media)

            candidates.assert_called_once_with(
                "https://jellyfin.local:8096",
                "jf-secret",
                season=1,
                episode=2,
                allow_insecure_http=False,
            )
            self.assertEqual(item_fetch.call_count, 2)
            self.assertEqual(media.item_id, item_id)
            self.assertEqual(media.media_source_id, media_source_id)
            self.assertEqual(media.provider_ids, {"Tvdb": "12345"})
            self.assertEqual(len(frames), 4)
            self.assertEqual(
                [frame.timestamp_ms for frame in frames],
                [0, 1000, 2000, 3000],
            )

    def test_configured_source_resolves_alternate_media_version_path(self):
        item_id = "11111111111111111111111111111111"
        primary_id = "22222222222222222222222222222222"
        alternate_id = "33333333333333333333333333333333"
        primary_path = "/srv/tv/Show/Season 01/Primary.mkv"
        external_path = "/srv/tv/Show/Season 01/Alternate.mkv"
        with tempfile.TemporaryDirectory() as temporary:
            local_root = Path(temporary) / "tv"
            local_path = local_root / "Show" / "Season 01" / "Alternate.mkv"
            mapper = ExternalPathMapper(
                [PathMapping("jellyfin", "/srv/tv", str(local_root))]
            )
            source = JellyfinTrickplaySource(
                "https://jellyfin.local:8096",
                "jf-secret",
                mapper,
            )
            context = AnalyzerContext(
                media=MediaIdentityFile(
                    file_id=1,
                    title_id=1,
                    path=str(local_path),
                    size_bytes=1,
                    modified_at=1.0,
                ),
                claimed_identity=IdentityReference(
                    identity_kind="episode",
                    season=1,
                    episode=2,
                    display_name="Episode",
                ),
                profile=IdentityProfile.DEEP,
            )
            media_sources = [
                {"Id": primary_id, "Path": primary_path},
                {"Id": alternate_id, "Path": external_path},
            ]
            candidate = {
                "Id": item_id,
                "Path": primary_path,
                "MediaSources": media_sources,
            }
            detail = {
                "Id": item_id,
                "Etag": "etag-alt",
                "Path": primary_path,
                "ProviderIds": {"Tvdb": "12345"},
                "MediaSources": media_sources,
                "Trickplay": {
                    alternate_id: {
                        "8": {
                            "Width": 8,
                            "Height": 6,
                            "TileWidth": 2,
                            "TileHeight": 2,
                            "ThumbnailCount": 4,
                            "Interval": 1000,
                            "Bandwidth": 1000,
                        }
                    }
                },
            }

            with (
                patch(
                    "app.media_identity.sources.jellyfin.fetch_episode_candidates",
                    return_value=(candidate,),
                ),
                patch(
                    "app.media_identity.sources.jellyfin.fetch_item",
                    return_value=detail,
                ),
            ):
                media = source.resolve_media(context)
                self.assertIsNotNone(media)
                frames = source.preview_frames(media)

            self.assertEqual(media.path, external_path)
            self.assertEqual(media.media_source_id, alternate_id)
            self.assertEqual(len(frames), 4)

    def test_configured_source_resolution_fails_closed_on_external_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            local_root = Path(temporary) / "tv"
            mapper = ExternalPathMapper(
                [PathMapping("jellyfin", "/srv/tv", str(local_root))]
            )
            source = JellyfinTrickplaySource(
                "https://jellyfin.local:8096",
                "jf-secret",
                mapper,
            )
            context = AnalyzerContext(
                media=MediaIdentityFile(
                    file_id=1,
                    title_id=1,
                    path=str(local_root / "Show" / "Episode.mkv"),
                    size_bytes=1,
                    modified_at=1.0,
                ),
                claimed_identity=IdentityReference(
                    identity_kind="episode",
                    season=1,
                    episode=1,
                    display_name="Episode",
                ),
                profile=IdentityProfile.DEEP,
            )
            with patch(
                "app.media_identity.sources.jellyfin.fetch_episode_candidates",
                side_effect=JellyfinAdapterError("offline"),
            ):
                with self.assertRaisesRegex(JellyfinAdapterError, "offline"):
                    source.resolve_media(context)

    def test_unavailable_source_does_not_attempt_network_resolution(self):
        with tempfile.TemporaryDirectory() as temporary:
            local_root = Path(temporary) / "tv"
            mapper = ExternalPathMapper(
                [PathMapping("jellyfin", "/srv/tv", str(local_root))]
            )
            source = JellyfinTrickplaySource(
                "https://jellyfin.local:8096",
                "jf-secret",
                mapper,
                enabled=False,
            )
            context = AnalyzerContext(
                media=MediaIdentityFile(
                    file_id=1,
                    title_id=1,
                    path=str(local_root / "Show" / "Episode.mkv"),
                    size_bytes=1,
                    modified_at=1.0,
                ),
                claimed_identity=IdentityReference(
                    identity_kind="episode",
                    season=1,
                    episode=1,
                    display_name="Episode",
                ),
                profile=IdentityProfile.DEEP,
            )
            with patch(
                "app.media_identity.sources.jellyfin.fetch_episode_candidates"
            ) as candidates:
                self.assertIsNone(source.resolve_media(context))
            candidates.assert_not_called()

    def _network_frame(self):
        item_id = "11111111111111111111111111111111"
        media_source_id = "22222222222222222222222222222222"
        variant = type(select_trickplay_variant(
            parse_trickplay_variants(self._item()), media_source_id="media-a"
        ))(
            media_source_id=media_source_id,
            width=8,
            height=6,
            tile_width=2,
            tile_height=2,
            thumbnail_count=4,
            interval_ms=1000,
            bandwidth=1000,
        )
        return enumerate_preview_frames(
            item_id=item_id,
            item_etag="etag-network",
            variant=variant,
        )[3]

    @staticmethod
    def _tile_jpeg():
        image = Image.new("RGB", (16, 12))
        colors = (
            (240, 20, 20),
            (20, 240, 20),
            (20, 20, 240),
            (220, 180, 20),
        )
        for index, color in enumerate(colors):
            row, column = divmod(index, 2)
            cell = Image.new("RGB", (8, 6), color)
            image.paste(cell, (column * 8, row * 6))
        output = BytesIO()
        image.save(output, format="JPEG", quality=95, subsampling=0)
        return output.getvalue()

    def test_tile_url_uses_read_only_route_and_media_source_query(self):
        frame = self._network_frame()
        url = trickplay_tile_url("https://jellyfin.local:8096/base", frame)
        self.assertEqual(
            url,
            "https://jellyfin.local:8096/base/Videos/11111111111111111111111111111111/Trickplay/8/0.jpg?mediaSourceId=22222222222222222222222222222222",
        )

    def test_credential_bearing_requests_reject_plain_http_by_default(self):
        frame = self._network_frame()
        with patch(
            "app.media_identity.sources.jellyfin.urllib.request.build_opener"
        ) as builder:
            with self.assertRaisesRegex(JellyfinAdapterError, "plain HTTP"):
                fetch_trickplay_tile(
                    "http://jellyfin.local:8096",
                    "top-secret",
                    frame,
                )
        builder.assert_not_called()

        with patch(
            "app.media_identity.sources.jellyfin.urllib.request.build_opener"
        ) as builder:
            with self.assertRaisesRegex(JellyfinAdapterError, "plain HTTP"):
                fetch_episode_candidates(
                    "http://jellyfin.local:8096",
                    "top-secret",
                    season=1,
                    episode=2,
                )
        builder.assert_not_called()

    def test_tile_fetch_uses_header_token_no_proxy_and_no_token_in_url(self):
        frame = self._network_frame()
        opener = DummyOpener(DummyResponse(self._tile_jpeg()))
        with patch(
            "app.media_identity.sources.jellyfin.urllib.request.build_opener",
            return_value=opener,
        ) as builder:
            payload = fetch_trickplay_tile(
                "https://jellyfin.local:8096",
                "top-secret",
                frame,
                timeout=3,
            )
        self.assertTrue(payload.startswith(b"\xff\xd8"))
        self.assertNotIn("top-secret", opener.request.full_url)
        self.assertEqual(opener.request.get_header("X-emby-token"), "top-secret")
        proxy_handlers = [
            handler
            for handler in builder.call_args.args
            if isinstance(handler, urllib.request.ProxyHandler)
        ]
        self.assertEqual(len(proxy_handlers), 1)
        self.assertEqual(proxy_handlers[0].proxies, {})
        self.assertLessEqual(opener.timeout, 15.0)

    def test_read_preview_crops_only_requested_cell(self):
        frame = self._network_frame()
        opener = DummyOpener(DummyResponse(self._tile_jpeg()))
        with patch(
            "app.media_identity.sources.jellyfin.urllib.request.build_opener",
            return_value=opener,
        ):
            preview = read_trickplay_preview(
                "https://jellyfin.local:8096",
                "token",
                frame,
            )
        with Image.open(BytesIO(preview)) as image:
            self.assertEqual(image.size, (8, 6))
            red, green, blue = image.convert("RGB").getpixel((4, 3))
        self.assertGreater(red, 150)
        self.assertGreater(green, 120)
        self.assertLess(blue, 100)

    def test_crop_rejects_wrong_sheet_dimensions_and_corrupt_jpeg(self):
        frame = self._network_frame()
        wrong = Image.new("RGB", (8, 6), (1, 2, 3))
        output = BytesIO()
        wrong.save(output, format="JPEG")
        with self.assertRaisesRegex(JellyfinAdapterError, "dimensions"):
            crop_trickplay_frame(output.getvalue(), frame)
        with self.assertRaisesRegex(JellyfinAdapterError, "decoded safely"):
            crop_trickplay_frame(b"not-a-jpeg", frame)

    def test_fetch_rejects_wrong_content_type_and_oversized_response(self):
        frame = self._network_frame()
        for response, pattern in (
            (DummyResponse(b"{}", content_type="application/json"), "not a JPEG"),
            (
                DummyResponse(
                    self._tile_jpeg(),
                    content_length=str(32 * 1024 * 1024 + 1),
                ),
                "response-size",
            ),
        ):
            opener = DummyOpener(response)
            with patch(
                "app.media_identity.sources.jellyfin.urllib.request.build_opener",
                return_value=opener,
            ):
                with self.assertRaisesRegex(JellyfinAdapterError, pattern):
                    fetch_trickplay_tile(
                        "https://jellyfin.local:8096",
                        "token",
                        frame,
                    )

    def test_preview_absence_and_source_failure_use_distinct_shared_types(self):
        frame = self._network_frame()
        cases = (
            (404, JellyfinPreviewUnavailable, ExternalPreviewUnavailable),
            (401, JellyfinSourceFailure, ExternalSourceFailure),
        )
        for code, adapter_type, shared_type in cases:
            with self.subTest(code=code):
                error = urllib.error.HTTPError(
                    "https://jellyfin.local/test",
                    code,
                    "error",
                    {},
                    None,
                )
                opener = DummyOpener(error=error)
                with patch(
                    "app.media_identity.sources.jellyfin.urllib.request.build_opener",
                    return_value=opener,
                ):
                    with self.assertRaises(adapter_type) as caught:
                        fetch_trickplay_tile(
                            "https://jellyfin.local:8096",
                            "token",
                            frame,
                        )
                self.assertIsInstance(caught.exception, shared_type)

    def test_fetch_rejects_redirect_auth_and_missing_tile(self):
        frame = self._network_frame()
        for code, pattern in (
            (302, "redirected"),
            (401, "rejected"),
            (404, "did not have"),
        ):
            error = urllib.error.HTTPError(
                "https://jellyfin.local/test",
                code,
                "error",
                {},
                None,
            )
            opener = DummyOpener(error=error)
            with patch(
                "app.media_identity.sources.jellyfin.urllib.request.build_opener",
                return_value=opener,
            ):
                with self.assertRaisesRegex(JellyfinAdapterError, pattern):
                    fetch_trickplay_tile(
                        "https://jellyfin.local:8096",
                        "token",
                        frame,
                    )

    def test_saved_preview_revalidates_etag_manifest_and_media_source(self):
        frame = self._network_frame()
        media_source_id = json.loads(frame.asset_ref)["media_source_id"]
        base_item = {
            "Id": frame.item_id,
            "Etag": "etag-network",
            "MediaSources": [
                {
                    "Id": media_source_id,
                    "Path": "/srv/tv/Show/Season 01/Episode.mkv",
                }
            ],
            "Trickplay": {
                media_source_id: {
                    "8": {
                        "Width": 8,
                        "Height": 6,
                        "TileWidth": 2,
                        "TileHeight": 2,
                        "ThumbnailCount": 4,
                        "Interval": 1000,
                        "Bandwidth": 1000,
                    }
                }
            },
        }

        with tempfile.TemporaryDirectory() as temporary:
            source = JellyfinTrickplaySource(
                "https://jellyfin.local:8096",
                "token",
                ExternalPathMapper(
                    [PathMapping("jellyfin", "/srv/tv", temporary)]
                ),
            )

            changed_etag = json.loads(json.dumps(base_item))
            changed_etag["Etag"] = "etag-new"

            changed_manifest = json.loads(json.dumps(base_item))
            changed_manifest["Trickplay"][media_source_id]["8"]["Interval"] = 2000

            changed_source = json.loads(json.dumps(base_item))
            changed_source["MediaSources"] = [
                {
                    "Id": "33333333333333333333333333333333",
                    "Path": "/srv/tv/Show/Season 01/Episode.mkv",
                }
            ]

            for item, pattern in (
                (changed_etag, "item or Trickplay manifest changed"),
                (changed_manifest, "timing changed"),
                (changed_source, "media source changed"),
            ):
                with self.subTest(pattern=pattern):
                    with (
                        patch(
                            "app.media_identity.sources.jellyfin.fetch_item",
                            return_value=item,
                        ),
                        patch(
                            "app.media_identity.sources.jellyfin.read_trickplay_preview"
                        ) as preview_read,
                    ):
                        with self.assertRaisesRegex(
                            JellyfinPreviewUnavailable,
                            pattern,
                        ) as caught:
                            source.read_preview(frame)
                        self.assertIsInstance(
                            caught.exception,
                            ExternalPreviewUnavailable,
                        )
                        preview_read.assert_not_called()

    def test_manifest_rejects_unsafe_total_tile_sheet_size(self):
        item = self._item()
        item["Trickplay"]["media-a"]["480"].update(
            {
                "Width": 480,
                "Height": 270,
                "TileWidth": 100,
                "TileHeight": 100,
            }
        )
        widths = [value.width for value in parse_trickplay_variants(item)]
        self.assertEqual(widths, [320])

    def test_asset_reference_bounds_and_guid_validation_fail_closed(self):
        frame = self._network_frame()
        payload = json.loads(frame.asset_ref)
        payload["column"] = payload["tile_width"]
        bad_frame = type(frame)(
            source_key=frame.source_key,
            item_id=frame.item_id,
            timestamp_ms=frame.timestamp_ms,
            asset_ref=json.dumps(payload),
            source_signature=frame.source_signature,
            width=frame.width,
            height=frame.height,
        )
        with self.assertRaisesRegex(JellyfinAdapterError, "tile bounds"):
            trickplay_tile_url("https://jellyfin.local:8096", bad_frame)

        invalid_item = type(frame)(
            source_key=frame.source_key,
            item_id="not-a-guid",
            timestamp_ms=frame.timestamp_ms,
            asset_ref=frame.asset_ref,
            source_signature=frame.source_signature,
            width=frame.width,
            height=frame.height,
        )
        with self.assertRaisesRegex(JellyfinAdapterError, "valid Jellyfin GUID"):
            trickplay_tile_url("https://jellyfin.local:8096", invalid_item)

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

from __future__ import annotations

from pathlib import Path
import json
import struct
import tempfile
import unittest
import urllib.parse
import urllib.request
from unittest.mock import patch

from app.media_identity.external import ExternalCapability
from app.media_identity.models import (
    AnalyzerContext,
    IdentityProfile,
    IdentityReference,
    MediaIdentityFile,
)
from app.path_mapping import ExternalPathMapper, PathMapping
from app.media_identity.sources.plex import (
    PlexBifError,
    PlexBifSource,
    enumerate_bif_preview_frames,
    enumerate_plex_http_preview_frames,
    fetch_plex_bif_image,
    fetch_plex_bif_index,
    fetch_plex_episode_candidates,
    fetch_plex_item,
    normalize_plex_metadata_root,
    parse_bif_index,
    plex_bif_path_for_bundle,
    read_bif_index,
    resolve_plex_media_ref,
)


_MAGIC = b"\x89BIF\r\n\x1a\n"


class DummyResponse:
    def __init__(self, payload: bytes, *, content_type: str, status: int = 200):
        self.payload = payload
        self.status = status
        self.headers = {"Content-Type": content_type}

    def read(self, size=-1):
        if size is None or size < 0:
            return self.payload
        return self.payload[:size]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class DummyOpener:
    def __init__(self, response):
        self.response = response
        self.request = None
        self.timeout = None

    def open(self, request, timeout=None):
        self.request = request
        self.timeout = timeout
        return self.response


def plex_episode_item(
    *,
    rating_key: str = "101",
    path: str = "/srv/tv/Show/Season 01/Episode.mkv",
    part_id: str = "501",
    indexes: str = "sd",
):
    return {
        "ratingKey": rating_key,
        "updatedAt": 123456,
        "Guid": [{"id": "tvdb://12345"}, {"id": "imdb://tt1234567"}],
        "Media": [
            {
                "id": 301,
                "Part": [
                    {
                        "id": part_id,
                        "file": path,
                        "key": f"/library/parts/{part_id}/123/file.mkv",
                        "indexes": indexes,
                    }
                ],
            }
        ],
    }



def build_bif(
    frames: tuple[tuple[int, bytes], ...] = (
        (0, b"\xff\xd8frame-zero\xff\xd9"),
        (10, b"\xff\xd8frame-one\xff\xd9"),
    ),
    *,
    multiplier: int = 1000,
    version: int = 0,
    reserved: bytes | None = None,
) -> bytes:
    count = len(frames)
    reserved_bytes = reserved if reserved is not None else b"\x00" * 44
    header = (
        _MAGIC
        + struct.pack("<III", version, count, multiplier)
        + reserved_bytes
    )
    index_size = (count + 1) * 8
    offset = 64 + index_size
    entries = []
    payload = bytearray()
    for timestamp, image in frames:
        entries.append(struct.pack("<II", timestamp, offset))
        payload.extend(image)
        offset += len(image)
    entries.append(struct.pack("<II", 0xFFFFFFFF, offset))
    return header + b"".join(entries) + bytes(payload)


def index_prefix(bif: bytes) -> bytes:
    image_count = struct.unpack_from("<I", bif, 12)[0]
    size = 64 + (image_count + 1) * 8
    return bif[:size]


class PlexBifFoundationTests(unittest.TestCase):
    def test_parses_version_zero_index_without_jpeg_payload(self):
        bif = build_bif()
        parsed = parse_bif_index(
            index_prefix(bif),
            file_size=len(bif),
            source_mtime_ns=123,
        )

        self.assertEqual(parsed.version, 0)
        self.assertEqual(parsed.image_count, 2)
        self.assertEqual(parsed.timestamp_multiplier_ms, 1000)
        self.assertEqual(parsed.source_mtime_ns, 123)
        self.assertEqual(
            [frame.timestamp_ms for frame in parsed.frames],
            [0, 10_000],
        )
        self.assertEqual(
            sum(frame.length for frame in parsed.frames),
            len(b"\xff\xd8frame-zero\xff\xd9")
            + len(b"\xff\xd8frame-one\xff\xd9"),
        )

    def test_zero_multiplier_uses_roku_default_1000ms(self):
        bif = build_bif(multiplier=0)
        parsed = parse_bif_index(index_prefix(bif), file_size=len(bif))
        self.assertEqual(parsed.timestamp_multiplier_ms, 1000)
        self.assertEqual(parsed.frames[1].timestamp_ms, 10_000)

    def test_read_bif_index_reads_valid_file_metadata(self):
        bif = build_bif()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "index-sd.bif"
            path.write_bytes(bif)
            parsed = read_bif_index(path)

        self.assertEqual(parsed.image_count, 2)
        self.assertEqual(parsed.file_size, len(bif))
        self.assertTrue(parsed.index_digest)

    def test_preview_enumeration_keeps_ranges_lazy(self):
        bif = build_bif()
        parsed = parse_bif_index(index_prefix(bif), file_size=len(bif))
        frames = enumerate_bif_preview_frames(
            item_id="plex-item-1",
            bif_path="/plex/Media/localhost/a/test.bundle/Contents/Indexes/index-sd.bif",
            index=parsed,
        )

        self.assertEqual(len(frames), 2)
        self.assertEqual([frame.timestamp_ms for frame in frames], [0, 10_000])
        first = json.loads(frames[0].asset_ref)
        self.assertEqual(first["kind"], "plex_bif")
        self.assertGreater(first["offset"], 64)
        self.assertGreater(first["length"], 0)
        self.assertTrue(frames[0].source_signature.startswith("plex-bif:"))
        self.assertEqual(
            frames[0].source_signature,
            frames[1].source_signature,
        )

    def test_rejects_wrong_magic_version_and_reserved_bytes(self):
        valid = build_bif()
        cases = (
            (b"NOTABIF!" + valid[8:], "magic"),
            (build_bif(version=1), "unsupported"),
            (build_bif(reserved=b"\x01" + b"\x00" * 43), "reserved"),
        )
        for bif, pattern in cases:
            with self.subTest(pattern=pattern):
                with self.assertRaisesRegex(PlexBifError, pattern):
                    parse_bif_index(index_prefix(bif), file_size=len(bif))

    def test_rejects_truncated_index_and_bad_end_marker(self):
        bif = build_bif()
        prefix = index_prefix(bif)

        with self.assertRaisesRegex(PlexBifError, "unexpected size"):
            parse_bif_index(prefix[:-1], file_size=len(bif))

        broken = bytearray(prefix)
        struct.pack_into("<I", broken, len(broken) - 8, 123)
        with self.assertRaisesRegex(PlexBifError, "end-of-data marker"):
            parse_bif_index(bytes(broken), file_size=len(bif))

    def test_rejects_nonmonotonic_offsets_and_wrong_final_size(self):
        bif = build_bif()
        prefix = bytearray(index_prefix(bif))
        first_offset = struct.unpack_from("<I", prefix, 68)[0]
        struct.pack_into("<I", prefix, 76, first_offset)

        with self.assertRaisesRegex(PlexBifError, "strictly increasing"):
            parse_bif_index(bytes(prefix), file_size=len(bif))

        valid_prefix = index_prefix(bif)
        with self.assertRaisesRegex(PlexBifError, "file size"):
            parse_bif_index(valid_prefix, file_size=len(bif) + 1)

    def test_metadata_root_and_bundle_path_are_contained(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = normalize_plex_metadata_root(temporary)
            candidate = plex_bif_path_for_bundle(
                root,
                "a/abcdef.bundle",
            )

            self.assertEqual(
                candidate,
                root
                / "Media"
                / "localhost"
                / "a"
                / "abcdef.bundle"
                / "Contents"
                / "Indexes"
                / "index-sd.bif",
            )

    def test_bundle_path_rejects_absolute_traversal_and_non_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            for value in (
                "/a/abcdef.bundle",
                r"C:\\a\\abcdef.bundle",
                "../abcdef.bundle",
                "a/../abcdef.bundle",
                "a/abcdef",
            ):
                with self.subTest(value=value):
                    with self.assertRaises(PlexBifError):
                        plex_bif_path_for_bundle(temporary, value)

    def test_metadata_root_requires_absolute_path(self):
        with self.assertRaisesRegex(PlexBifError, "absolute"):
            normalize_plex_metadata_root("relative/plex")


    def test_resolves_exact_plex_media_part_and_provider_ids(self):
        expected = "/srv/tv/Show/Season 01/Alternate.mkv"
        item = plex_episode_item(path="/srv/tv/Show/Season 01/Primary.mkv")
        item["Media"][0]["Part"].append(
            {
                "id": "502",
                "file": expected,
                "key": "/library/parts/502/123/file.mkv",
                "indexes": "sd",
            }
        )

        ref = resolve_plex_media_ref(
            (item,),
            expected_external_path=expected,
        )

        self.assertIsNotNone(ref)
        self.assertEqual(ref.item_id, "101")
        self.assertEqual(ref.media_source_id, "502")
        self.assertEqual(ref.path, expected)
        self.assertEqual(
            ref.provider_ids,
            {"tvdb": "12345", "imdb": "tt1234567"},
        )
        self.assertTrue(ref.source_signature.startswith("plex-item:"))

    def test_duplicate_plex_media_part_path_fails_closed(self):
        expected = "/srv/tv/Show/Season 01/Episode.mkv"
        first = plex_episode_item(rating_key="101", part_id="501", path=expected)
        second = plex_episode_item(rating_key="102", part_id="502", path=expected)

        with self.assertRaisesRegex(PlexBifError, "ambiguous"):
            resolve_plex_media_ref(
                (first, second),
                expected_external_path=expected,
            )

    def test_plex_episode_candidate_query_is_bounded_and_token_safe(self):
        payload = json.dumps(
            {
                "MediaContainer": {
                    "totalSize": 1,
                    "Metadata": [plex_episode_item()],
                }
            }
        ).encode("utf-8")
        opener = DummyOpener(
            DummyResponse(payload, content_type="application/json")
        )

        with patch(
            "app.media_identity.sources.plex.urllib.request.build_opener",
            return_value=opener,
        ) as builder:
            items = fetch_plex_episode_candidates(
                "http://plex.local:32400",
                "top-secret",
                season=1,
                episode=2,
                timeout=3,
            )

        self.assertEqual(len(items), 1)
        parsed = urllib.parse.urlsplit(opener.request.full_url)
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/library/all")
        self.assertEqual(query["type"], ["4"])
        self.assertEqual(query["parentIndex"], ["1"])
        self.assertEqual(query["index"], ["2"])
        self.assertEqual(query["limit"], ["4097"])
        self.assertNotIn("top-secret", opener.request.full_url)
        self.assertEqual(opener.request.get_header("X-plex-token"), "top-secret")
        proxy_handlers = [
            handler
            for handler in builder.call_args.args
            if isinstance(handler, urllib.request.ProxyHandler)
        ]
        self.assertEqual(len(proxy_handlers), 1)
        self.assertEqual(proxy_handlers[0].proxies, {})

    def test_fetch_plex_item_requires_exact_requested_rating_key(self):
        payload = json.dumps(
            {
                "MediaContainer": {
                    "Metadata": [plex_episode_item(rating_key="102")]
                }
            }
        ).encode("utf-8")
        opener = DummyOpener(
            DummyResponse(payload, content_type="application/json")
        )

        with patch(
            "app.media_identity.sources.plex.urllib.request.build_opener",
            return_value=opener,
        ):
            with self.assertRaisesRegex(PlexBifError, "different item"):
                fetch_plex_item(
                    "http://plex.local:32400",
                    "token",
                    "101",
                )

    def test_fetch_plex_bif_index_parses_read_only_http_asset(self):
        bif = build_bif()
        opener = DummyOpener(
            DummyResponse(bif, content_type="application/octet-stream")
        )

        with patch(
            "app.media_identity.sources.plex.urllib.request.build_opener",
            return_value=opener,
        ):
            parsed = fetch_plex_bif_index(
                "http://plex.local:32400",
                "secret",
                "501",
            )

        self.assertEqual(parsed.image_count, 2)
        self.assertEqual(parsed.file_size, len(bif))
        self.assertEqual(
            urllib.parse.urlsplit(opener.request.full_url).path,
            "/library/parts/501/indexes/sd",
        )
        self.assertEqual(opener.request.get_header("X-plex-token"), "secret")

    def test_http_preview_refs_use_part_id_and_timestamp_not_raw_token(self):
        bif = build_bif()
        index = parse_bif_index(index_prefix(bif), file_size=len(bif))
        frames = enumerate_plex_http_preview_frames(
            item_id="101",
            part_id="501",
            index=index,
        )

        first = json.loads(frames[0].asset_ref)
        self.assertEqual(first["kind"], "plex_bif_http")
        self.assertEqual(first["part_id"], "501")
        self.assertEqual(first["timestamp_ms"], 0)
        self.assertTrue(
            frames[0].source_signature.startswith("plex-bif-http:")
        )

    def test_fetch_plex_bif_image_reads_only_requested_timestamp(self):
        jpeg = b"\xff\xd8requested-frame\xff\xd9"
        opener = DummyOpener(
            DummyResponse(jpeg, content_type="image/jpeg")
        )

        with patch(
            "app.media_identity.sources.plex.urllib.request.build_opener",
            return_value=opener,
        ):
            result = fetch_plex_bif_image(
                "http://plex.local:32400",
                "secret",
                "501",
                10_000,
            )

        self.assertEqual(result, jpeg)
        self.assertEqual(
            urllib.parse.urlsplit(opener.request.full_url).path,
            "/library/parts/501/indexes/sd/10000",
        )
        self.assertNotIn("secret", opener.request.full_url)

    def test_fetch_plex_bif_image_rejects_non_jpeg(self):
        opener = DummyOpener(
            DummyResponse(b"not-a-jpeg", content_type="image/jpeg")
        )
        with patch(
            "app.media_identity.sources.plex.urllib.request.build_opener",
            return_value=opener,
        ):
            with self.assertRaisesRegex(PlexBifError, "invalid JPEG"):
                fetch_plex_bif_image(
                    "http://plex.local:32400",
                    "secret",
                    "501",
                    0,
                )

    def test_configured_plex_source_resolves_and_reads_exact_part_preview(self):
        bif = build_bif()
        expected_external = "/srv/tv/Show/Season 01/Episode.mkv"
        candidate = plex_episode_item(
            rating_key="101",
            path=expected_external,
            part_id="501",
        )
        with tempfile.TemporaryDirectory() as temporary:
            local_root = Path(temporary) / "tv"
            local_path = local_root / "Show" / "Season 01" / "Episode.mkv"
            mapper = ExternalPathMapper(
                [PathMapping("plex", "/srv/tv", str(local_root))]
            )
            source = PlexBifSource(
                "http://plex.local:32400",
                "secret",
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

            with (
                patch(
                    "app.media_identity.sources.plex.fetch_plex_episode_candidates",
                    return_value=(candidate,),
                ),
                patch(
                    "app.media_identity.sources.plex.fetch_plex_item",
                    return_value=candidate,
                ),
                patch(
                    "app.media_identity.sources.plex.fetch_plex_bif_index",
                    return_value=parse_bif_index(
                        index_prefix(bif),
                        file_size=len(bif),
                    ),
                ),
                patch(
                    "app.media_identity.sources.plex.fetch_plex_bif_image",
                    return_value=b"\xff\xd8frame\xff\xd9",
                ) as image_fetch,
            ):
                media = source.resolve_media(context)
                self.assertIsNotNone(media)
                frames = source.preview_frames(media)
                image = source.read_preview(frames[1])

            self.assertEqual(media.item_id, "101")
            self.assertEqual(media.media_source_id, "501")
            self.assertEqual(len(frames), 2)
            self.assertEqual(image, b"\xff\xd8frame\xff\xd9")
            image_fetch.assert_called_once_with(
                "http://plex.local:32400",
                "secret",
                "501",
                10_000,
            )
            self.assertEqual(source.status().capabilities, frozenset())
            self.assertEqual(
                tuple(source.status().capabilities),
                (),
            )
            self.assertNotIn(
                ExternalCapability.PREVIEW_FRAMES,
                source.status().capabilities,
            )

    def test_stale_plex_media_version_disables_preview_reuse(self):
        expected = "/srv/tv/Show/Season 01/Episode.mkv"
        current = plex_episode_item(
            rating_key="101",
            path=expected,
            part_id="502",
        )
        with tempfile.TemporaryDirectory() as temporary:
            local_root = Path(temporary) / "tv"
            mapper = ExternalPathMapper(
                [PathMapping("plex", "/srv/tv", str(local_root))]
            )
            source = PlexBifSource(
                "http://plex.local:32400",
                "secret",
                mapper,
            )
            from app.media_identity.external import ExternalMediaRef
            media = ExternalMediaRef(
                source_key="plex",
                item_id="101",
                path=expected,
                media_source_id="501",
            )
            with (
                patch(
                    "app.media_identity.sources.plex.fetch_plex_item",
                    return_value=current,
                ),
                patch(
                    "app.media_identity.sources.plex.fetch_plex_bif_index"
                ) as bif_fetch,
            ):
                frames = source.preview_frames(media)

            self.assertEqual(frames, ())
            bif_fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()

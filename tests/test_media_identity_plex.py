from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import hashlib
import json
import sqlite3
import struct
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch

from PIL import Image

from app.media_identity.external import (
    ExternalCapability,
    ExternalPreviewUnavailable,
    ExternalSourceFailure,
)
from app.media_identity.visual_budget import (
    VisualAttemptBudget,
    VisualBudgetExceeded,
    visual_budget_scope,
)
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
    PlexPreviewUnavailable,
    PlexSourceFailure,
    bif_source_signature,
    enumerate_bif_preview_frames,
    enumerate_plex_http_preview_frames,
    fetch_plex_bif_image,
    fetch_plex_bif_index,
    fetch_plex_episode_candidates,
    fetch_plex_item,
    fetch_plex_path_candidates,
    normalize_plex_metadata_root,
    parse_bif_index,
    plex_bif_path_for_bundle,
    read_bif_index,
    read_verified_bif_preview,
    resolve_local_bif_path,
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


class ErrorOpener:
    def __init__(self, error):
        self.error = error
        self.request = None

    def open(self, request, timeout=None):
        self.request = request
        raise self.error


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



def jpeg_frame(color: tuple[int, int, int]) -> bytes:
    output = BytesIO()
    Image.new("RGB", (8, 6), color).save(
        output,
        format="JPEG",
        quality=90,
        subsampling=0,
    )
    return output.getvalue()


FRAME_ZERO = jpeg_frame((220, 30, 30))
FRAME_ONE = jpeg_frame((30, 180, 60))


def build_bif(
    frames: tuple[tuple[int, bytes], ...] = (
        (0, FRAME_ZERO),
        (10, FRAME_ONE),
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


def http_bif_index(bif: bytes):
    parsed = parse_bif_index(index_prefix(bif), file_size=len(bif))
    return replace(
        parsed,
        index_digest=hashlib.sha256(bif).hexdigest(),
        frame_digests=tuple(
            hashlib.sha256(
                bif[frame.offset : frame.offset + frame.length]
            ).hexdigest()
            for frame in parsed.frames
        ),
    )


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
            len(FRAME_ZERO)
            + len(FRAME_ONE),
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

    def test_local_bif_reads_share_source_budget_and_attempt_cache(self):
        bif = build_bif()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "index-sd.bif"
            path.write_bytes(bif)
            index_bytes = len(index_prefix(bif))
            budget = VisualAttemptBudget(
                max_frame_attempts=4,
                max_source_bytes=index_bytes + len(FRAME_ZERO) + 1,
                max_image_bytes=1,
                max_text_chars=1,
            )
            with visual_budget_scope(budget):
                parsed = read_bif_index(path)
                self.assertEqual(budget.source_bytes, index_bytes)

                repeated = read_bif_index(path)
                self.assertEqual(
                    repeated.index_digest,
                    parsed.index_digest,
                )
                self.assertEqual(budget.source_bytes, index_bytes)

                frame = parsed.frames[0]
                signature = bif_source_signature(path, parsed)
                payload = read_verified_bif_preview(
                    path,
                    expected_signature=signature,
                    timestamp_ms=frame.timestamp_ms,
                    offset=frame.offset,
                    length=frame.length,
                )
                self.assertEqual(payload, FRAME_ZERO)
                self.assertEqual(
                    budget.source_bytes,
                    index_bytes + len(FRAME_ZERO),
                )

                repeated_payload = read_verified_bif_preview(
                    path,
                    expected_signature=signature,
                    timestamp_ms=frame.timestamp_ms,
                    offset=frame.offset,
                    length=frame.length,
                )
                self.assertEqual(repeated_payload, FRAME_ZERO)
                self.assertEqual(
                    budget.source_bytes,
                    index_bytes + len(FRAME_ZERO),
                )

    def test_local_bif_index_fails_before_read_when_source_budget_is_tiny(self):
        bif = build_bif()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "index-sd.bif"
            path.write_bytes(bif)
            budget = VisualAttemptBudget(
                max_frame_attempts=1,
                max_source_bytes=1,
                max_image_bytes=1,
                max_text_chars=1,
            )
            with visual_budget_scope(budget):
                with self.assertRaises(VisualBudgetExceeded):
                    read_bif_index(path)

            self.assertTrue(budget.exhausted)
            self.assertEqual(budget.source_bytes, 0)

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

    def test_rejects_duplicate_bif_timestamps(self):
        bif = build_bif(
            frames=(
                (10, b"\xff\xd8first\xff\xd9"),
                (10, b"\xff\xd8second\xff\xd9"),
            )
        )
        with self.assertRaisesRegex(PlexBifError, "strictly increasing"):
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

    def test_plex_path_candidate_query_uses_exact_full_path(self):
        expected = "/srv/tv/Show/Season 99/Wrong Number.mkv"
        payload = json.dumps(
            {
                "MediaContainer": {
                    "totalSize": 1,
                    "Metadata": [plex_episode_item(path=expected)],
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
            items = fetch_plex_path_candidates(
                "https://plex.local:32400",
                "secret",
                path=expected,
            )

        self.assertEqual(len(items), 1)
        parsed = urllib.parse.urlsplit(opener.request.full_url)
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/library/all")
        self.assertEqual(query["type"], ["4"])
        self.assertEqual(query["path"], [expected])
        self.assertEqual(query["includeGuids"], ["1"])
        self.assertNotIn("parentIndex", query)
        self.assertNotIn("index", query)

    def test_plex_episode_candidate_query_is_bounded_and_token_safe(self):
        payload = json.dumps(
            {
                "MediaContainer": {
                    "totalSize": 1,
                    "offset": 0,
                    "size": 1,
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
                "https://plex.local:32400",
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
        self.assertEqual(query["X-Plex-Container-Start"], ["0"])
        self.assertEqual(query["X-Plex-Container-Size"], ["256"])
        self.assertNotIn("top-secret", opener.request.full_url)
        self.assertEqual(opener.request.get_header("X-plex-token"), "top-secret")
        proxy_handlers = [
            handler
            for handler in builder.call_args.args
            if isinstance(handler, urllib.request.ProxyHandler)
        ]
        self.assertEqual(len(proxy_handlers), 1)
        self.assertEqual(proxy_handlers[0].proxies, {})

    def test_plex_episode_candidates_follow_pagination(self):
        first = plex_episode_item(rating_key="101", part_id="501")
        second = plex_episode_item(
            rating_key="102",
            part_id="502",
            path="/srv/tv/Show/Season 01/Target.mkv",
        )
        calls = []

        def paged_read(
            _server,
            _token,
            _path,
            *,
            query,
            timeout,
            allow_insecure_http,
        ):
            calls.append(dict(query))
            start = int(query["X-Plex-Container-Start"])
            if start == 0:
                return {
                    "MediaContainer": {
                        "totalSize": 2,
                        "offset": 0,
                        "size": 1,
                        "Metadata": [first],
                    }
                }
            if start == 1:
                return {
                    "MediaContainer": {
                        "totalSize": 2,
                        "offset": 1,
                        "size": 1,
                        "Metadata": [second],
                    }
                }
            self.fail(f"unexpected page start {start}")

        with patch(
            "app.media_identity.sources.plex._read_plex_json",
            side_effect=paged_read,
        ):
            items = fetch_plex_episode_candidates(
                "https://plex.local:32400",
                "secret",
                season=1,
                episode=2,
            )

        self.assertEqual([item["ratingKey"] for item in items], ["101", "102"])
        self.assertEqual(
            [call["X-Plex-Container-Start"] for call in calls],
            [0, 1],
        )
        resolved = resolve_plex_media_ref(
            items,
            expected_external_path="/srv/tv/Show/Season 01/Target.mkv",
        )
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.item_id, "102")
        self.assertEqual(resolved.media_source_id, "502")

    def test_plex_path_lookup_rejects_missing_continuation_page(self):
        expected = "/srv/tv/Show/Season 01/Episode.mkv"
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
            start = int(query["X-Plex-Container-Start"])
            starts.append(start)
            if start == 0:
                return {
                    "MediaContainer": {
                        "offset": 0,
                        "size": 1,
                        "totalSize": 2,
                        "Metadata": [plex_episode_item(path=expected)],
                    }
                }
            if start == 1:
                return {
                    "MediaContainer": {
                        "offset": 1,
                        "size": 0,
                        "totalSize": 2,
                        "Metadata": [],
                    }
                }
            self.fail(f"unexpected page start {start}")

        with patch(
            "app.media_identity.sources.plex._read_plex_json",
            side_effect=paged_read,
        ):
            with self.assertRaises(PlexSourceFailure) as caught:
                fetch_plex_path_candidates(
                    "https://plex.local:32400",
                    "secret",
                    path=expected,
                )
        self.assertEqual(starts, [0, 1])
        self.assertIsInstance(caught.exception, ExternalSourceFailure)
        self.assertIn("ended before all candidates", str(caught.exception))

    def test_plex_episode_candidates_page_until_empty_without_total_size(self):
        page = [
            plex_episode_item(
                rating_key=str(1000 + index),
                part_id=str(5000 + index),
                path=f"/srv/tv/Show {index}/Season 01/Episode.mkv",
            )
            for index in range(256)
        ]
        target = plex_episode_item(
            rating_key="9001",
            part_id="9901",
            path="/srv/tv/Target/Season 01/Episode.mkv",
        )
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
            start = int(query["X-Plex-Container-Start"])
            starts.append(start)
            if start == 0:
                return {
                    "MediaContainer": {
                        "offset": 0,
                        "size": len(page),
                        "Metadata": page,
                    }
                }
            if start == 256:
                return {
                    "MediaContainer": {
                        "offset": 256,
                        "size": 1,
                        "Metadata": [target],
                    }
                }
            if start == 257:
                return {
                    "MediaContainer": {
                        "offset": 257,
                        "size": 0,
                        "Metadata": [],
                    }
                }
            self.fail(f"unexpected page start {start}")

        with patch(
            "app.media_identity.sources.plex._read_plex_json",
            side_effect=paged_read,
        ):
            items = fetch_plex_episode_candidates(
                "https://plex.local:32400",
                "secret",
                season=1,
                episode=1,
            )

        self.assertEqual(len(items), 257)
        self.assertEqual(starts, [0, 256, 257])
        resolved = resolve_plex_media_ref(
            items,
            expected_external_path="/srv/tv/Target/Season 01/Episode.mkv",
        )
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.item_id, "9001")
        self.assertEqual(resolved.media_source_id, "9901")

    def test_plex_helpers_reject_plain_http_before_sending_token(self):
        with patch(
            "app.media_identity.sources.plex.urllib.request.build_opener"
        ) as opener:
            with self.assertRaisesRegex(
                PlexBifError, "will not be sent over plain HTTP"
            ):
                fetch_plex_item(
                    "http://plex.local:32400",
                    "secret",
                    "101",
                )

        opener.assert_not_called()

    def test_plex_helpers_allow_explicit_trusted_http_opt_in(self):
        payload = json.dumps(
            {
                "MediaContainer": {
                    "Metadata": [plex_episode_item(rating_key="101")]
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
            item = fetch_plex_item(
                "http://plex.local:32400",
                "secret",
                "101",
                allow_insecure_http=True,
            )

        self.assertEqual(item["ratingKey"], "101")
        self.assertEqual(opener.request.get_header("X-plex-token"), "secret")

    def test_missing_plex_item_404_is_not_preview_unavailable(self):
        error = urllib.error.HTTPError(
            "https://plex.local:32400/library/metadata/101",
            404,
            "Not Found",
            {},
            None,
        )
        opener = ErrorOpener(error)
        with patch(
            "app.media_identity.sources.plex.urllib.request.build_opener",
            return_value=opener,
        ):
            with self.assertRaises(PlexBifError) as caught:
                fetch_plex_item(
                    "https://plex.local:32400",
                    "secret",
                    "101",
                )

        self.assertNotIsInstance(caught.exception, PlexPreviewUnavailable)
        self.assertIsInstance(caught.exception, PlexSourceFailure)
        self.assertIsInstance(caught.exception, ExternalSourceFailure)

    def test_missing_bif_404_is_preview_unavailable(self):
        error = urllib.error.HTTPError(
            "https://plex.local:32400/library/parts/501/indexes/sd",
            404,
            "Not Found",
            {},
            None,
        )
        opener = ErrorOpener(error)
        with patch(
            "app.media_identity.sources.plex.urllib.request.build_opener",
            return_value=opener,
        ):
            with self.assertRaisesRegex(
                PlexPreviewUnavailable, "no BIF preview asset"
            ) as caught:
                fetch_plex_bif_index(
                    "https://plex.local:32400",
                    "secret",
                    "501",
                )
        self.assertIsInstance(caught.exception, ExternalPreviewUnavailable)
        self.assertNotIsInstance(caught.exception, ExternalSourceFailure)

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
                    "https://plex.local:32400",
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
                "https://plex.local:32400",
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
        index = http_bif_index(bif)
        frames = enumerate_plex_http_preview_frames(
            item_id="101",
            part_id="501",
            index=index,
        )

        first = json.loads(frames[0].asset_ref)
        self.assertEqual(first["kind"], "plex_bif_http")
        self.assertEqual(first["part_id"], "501")
        self.assertEqual(first["timestamp_ms"], 0)
        self.assertEqual(
            first["sha256"],
            hashlib.sha256(FRAME_ZERO).hexdigest(),
        )
        self.assertTrue(
            frames[0].source_signature.startswith("plex-bif-http:")
        )

    def test_fetch_plex_bif_image_reads_only_requested_timestamp(self):
        jpeg = jpeg_frame((80, 90, 200))
        opener = DummyOpener(
            DummyResponse(jpeg, content_type="image/jpeg")
        )

        with patch(
            "app.media_identity.sources.plex.urllib.request.build_opener",
            return_value=opener,
        ):
            result = fetch_plex_bif_image(
                "https://plex.local:32400",
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

    def test_bif_download_cannot_exceed_shared_visual_source_budget(self):
        bif = build_bif()
        response = DummyResponse(
            bif,
            content_type="application/octet-stream",
        )
        response.headers["Content-Length"] = str(len(bif))
        opener = DummyOpener(response)
        budget = VisualAttemptBudget(
            max_frame_attempts=12,
            max_source_bytes=max(1, len(bif) - 1),
            max_image_bytes=48 * 1024 * 1024,
            max_text_chars=64_000,
        )

        with (
            patch(
                "app.media_identity.sources.plex.urllib.request.build_opener",
                return_value=opener,
            ),
            visual_budget_scope(budget),
        ):
            with self.assertRaises(VisualBudgetExceeded):
                fetch_plex_bif_index(
                    "https://plex.local:32400",
                    "secret",
                    "501",
                )

        self.assertEqual(budget.source_bytes, 0)
        self.assertTrue(budget.exhausted)

    def test_fetch_plex_bif_image_rejects_non_jpeg(self):
        opener = DummyOpener(
            DummyResponse(b"not-a-jpeg", content_type="image/jpeg")
        )
        with patch(
            "app.media_identity.sources.plex.urllib.request.build_opener",
            return_value=opener,
        ):
            with self.assertRaisesRegex(PlexPreviewUnavailable, "invalid JPEG"):
                fetch_plex_bif_image(
                    "https://plex.local:32400",
                    "secret",
                    "501",
                    0,
                )

    def test_fetch_plex_bif_image_rejects_marker_wrapped_corrupt_jpeg(self):
        opener = DummyOpener(
            DummyResponse(
                b"\xff\xd8not-really-jpeg\xff\xd9",
                content_type="image/jpeg",
            )
        )
        with patch(
            "app.media_identity.sources.plex.urllib.request.build_opener",
            return_value=opener,
        ):
            with self.assertRaisesRegex(
                PlexPreviewUnavailable, "decoded safely"
            ) as caught:
                fetch_plex_bif_image(
                    "https://plex.local:32400",
                    "secret",
                    "501",
                    0,
                )
        self.assertIsInstance(caught.exception, ExternalPreviewUnavailable)

    def test_fetch_plex_bif_image_enforces_dimension_and_pixel_ceilings(self):
        class FakeImage:
            format = "JPEG"

            def __init__(self, size):
                self.size = size
                self.width, self.height = size

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def load(self):
                raise AssertionError("unsafe image must be rejected before decode")

        cases = (
            ((16_385, 1), "dimensions"),
            ((9_000, 8_000), "dimensions"),
        )
        for size, pattern in cases:
            with self.subTest(size=size):
                opener = DummyOpener(
                    DummyResponse(
                        b"\xff\xd8bounded\xff\xd9",
                        content_type="image/jpeg",
                    )
                )
                with (
                    patch(
                        "app.media_identity.sources.plex.urllib.request.build_opener",
                        return_value=opener,
                    ),
                    patch(
                        "app.media_identity.sources.plex.Image.open",
                        return_value=FakeImage(size),
                    ),
                ):
                    with self.assertRaisesRegex(
                        PlexPreviewUnavailable,
                        pattern,
                    ):
                        fetch_plex_bif_image(
                            "https://plex.local:32400",
                            "secret",
                            "501",
                            0,
                        )

    def test_ambiguous_path_mapping_is_normalized_as_plex_source_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            local_root = Path(temporary) / "tv"
            local_path = local_root / "Show" / "Episode.mkv"
            mapper = ExternalPathMapper([
                PathMapping("plex", "/srv/tv-a", str(local_root), priority=1),
                PathMapping("plex", "/srv/tv-b", str(local_root), priority=1),
            ])
            source = PlexBifSource(
                "https://plex.local:32400",
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
                    episode=1,
                    display_name="Episode",
                ),
                profile=IdentityProfile.NORMAL,
            )
            with self.assertRaisesRegex(
                PlexSourceFailure,
                "path mapping.*unambiguously",
            ) as caught:
                source.resolve_media(context)
            self.assertIsInstance(caught.exception, ExternalSourceFailure)

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
                "https://plex.local:32400",
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
                    "app.media_identity.sources.plex.fetch_plex_path_candidates",
                    return_value=(candidate,),
                ) as path_candidates,
                patch(
                    "app.media_identity.sources.plex.fetch_plex_episode_candidates"
                ) as episode_candidates,
                patch(
                    "app.media_identity.sources.plex.fetch_plex_item",
                    return_value=candidate,
                ),
                patch(
                    "app.media_identity.sources.plex.fetch_plex_bif_index",
                    return_value=http_bif_index(bif),
                ) as bif_fetch,
                patch(
                    "app.media_identity.sources.plex.fetch_plex_bif_image",
                    return_value=FRAME_ONE,
                ) as image_fetch,
            ):
                media = source.resolve_media(context)
                self.assertIsNotNone(media)
                frames = source.preview_frames(media)
                image = source.read_preview(frames[1])

            path_candidates.assert_called_once_with(
                "https://plex.local:32400",
                "secret",
                path=expected_external,
                allow_insecure_http=False,
            )
            episode_candidates.assert_not_called()
            self.assertEqual(media.item_id, "101")
            self.assertEqual(media.media_source_id, "501")
            self.assertEqual(len(frames), 2)
            self.assertEqual(image, FRAME_ONE)
            bif_fetch.assert_called_once_with(
                "https://plex.local:32400",
                "secret",
                "501",
                allow_insecure_http=False,
            )
            image_fetch.assert_called_once_with(
                "https://plex.local:32400",
                "secret",
                "501",
                10_000,
                allow_insecure_http=False,
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

    def test_path_first_resolution_ignores_wrong_claimed_episode_coordinates(self):
        expected_external = "/srv/tv/Show/Season 01/Actually Episode 05.mkv"
        candidate = plex_episode_item(
            rating_key="105",
            path=expected_external,
            part_id="505",
        )
        with tempfile.TemporaryDirectory() as temporary:
            local_root = Path(temporary) / "tv"
            local_path = local_root / "Show" / "Season 01" / "Actually Episode 05.mkv"
            source = PlexBifSource(
                "https://plex.local:32400",
                "secret",
                ExternalPathMapper(
                    [PathMapping("plex", "/srv/tv", str(local_root))]
                ),
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
                    season=9,
                    episode=99,
                    display_name="Wrong claimed coordinates",
                ),
                profile=IdentityProfile.NORMAL,
            )

            with (
                patch(
                    "app.media_identity.sources.plex.fetch_plex_path_candidates",
                    return_value=(candidate,),
                ) as path_candidates,
                patch(
                    "app.media_identity.sources.plex.fetch_plex_episode_candidates"
                ) as episode_candidates,
                patch(
                    "app.media_identity.sources.plex.fetch_plex_item",
                    return_value=candidate,
                ),
            ):
                resolved = source.resolve_media(context)

            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.item_id, "105")
            self.assertEqual(resolved.media_source_id, "505")
            path_candidates.assert_called_once()
            episode_candidates.assert_not_called()

    def test_path_lookup_can_fall_back_to_claimed_episode_candidates(self):
        expected_external = "/srv/tv/Show/Season 01/Episode.mkv"
        candidate = plex_episode_item(
            rating_key="101",
            path=expected_external,
            part_id="501",
        )
        with tempfile.TemporaryDirectory() as temporary:
            local_root = Path(temporary) / "tv"
            local_path = local_root / "Show" / "Season 01" / "Episode.mkv"
            source = PlexBifSource(
                "https://plex.local:32400",
                "secret",
                ExternalPathMapper(
                    [PathMapping("plex", "/srv/tv", str(local_root))]
                ),
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
                profile=IdentityProfile.NORMAL,
            )
            with (
                patch(
                    "app.media_identity.sources.plex.fetch_plex_path_candidates",
                    return_value=(),
                ) as path_candidates,
                patch(
                    "app.media_identity.sources.plex.fetch_plex_episode_candidates",
                    return_value=(candidate,),
                ) as episode_candidates,
                patch(
                    "app.media_identity.sources.plex.fetch_plex_item",
                    return_value=candidate,
                ),
            ):
                resolved = source.resolve_media(context)

            self.assertIsNotNone(resolved)
            path_candidates.assert_called_once()
            episode_candidates.assert_called_once_with(
                "https://plex.local:32400",
                "secret",
                season=1,
                episode=2,
                allow_insecure_http=False,
            )

    def test_http_frame_hash_rejects_changed_jpeg_without_refetching_bif(self):
        original = jpeg_frame((10, 10, 10))
        changed = jpeg_frame((20, 20, 20))
        bif = build_bif(frames=((0, original),))
        frames = enumerate_plex_http_preview_frames(
            item_id="101",
            part_id="501",
            index=http_bif_index(bif),
        )
        with tempfile.TemporaryDirectory() as temporary:
            local_root = Path(temporary) / "tv"
            source = PlexBifSource(
                "https://plex.local:32400",
                "secret",
                ExternalPathMapper(
                    [PathMapping("plex", "/srv/tv", str(local_root))]
                ),
            )
            with (
                patch(
                    "app.media_identity.sources.plex.fetch_plex_bif_index"
                ) as bif_fetch,
                patch(
                    "app.media_identity.sources.plex.fetch_plex_bif_image",
                    return_value=changed,
                ) as image_fetch,
            ):
                with self.assertRaisesRegex(
                    PlexPreviewUnavailable, "frame content changed"
                ):
                    source.read_preview(frames[0])

            bif_fetch.assert_not_called()
            image_fetch.assert_called_once_with(
                "https://plex.local:32400",
                "secret",
                "501",
                0,
                allow_insecure_http=False,
            )

    def test_local_bif_discovery_rejects_symlink_escape(self):
        expected = "/srv/tv/Show/Season 01/Episode.mkv"
        media_hash = "a" + "b" * 39
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Plex Media Server"
            database_path = (
                root
                / "Plug-in Support"
                / "Databases"
                / "com.plexapp.plugins.library.db"
            )
            database_path.parent.mkdir(parents=True)
            with closing(sqlite3.connect(database_path)) as connection, connection:
                connection.execute(
                    "CREATE TABLE media_parts (id INTEGER PRIMARY KEY, hash TEXT, file TEXT)"
                )
                connection.execute(
                    "INSERT INTO media_parts(id,hash,file) VALUES (?,?,?)",
                    (501, media_hash, expected),
                )

            bundle = (
                root
                / "Media"
                / "localhost"
                / media_hash[0]
                / f"{media_hash[1:]}.bundle"
            )
            outside = Path(temporary) / "outside-bundle"
            outside_index = outside / "Contents" / "Indexes" / "index-sd.bif"
            outside_index.parent.mkdir(parents=True)
            outside_index.write_bytes(build_bif())
            bundle.parent.mkdir(parents=True)
            try:
                bundle.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")

            with self.assertRaisesRegex(PlexBifError, "escapes"):
                resolve_local_bif_path(
                    root,
                    part_id="501",
                    expected_external_path=expected,
                )

    def test_custom_metadata_root_falls_back_to_exact_local_bif(self):
        expected = "/srv/tv/Show/Season 01/Episode.mkv"
        candidate = plex_episode_item(
            rating_key="101",
            path=expected,
            part_id="501",
        )
        media_hash = "a" + "b" * 39
        bif = build_bif()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Plex Media Server"
            database_path = (
                root
                / "Plug-in Support"
                / "Databases"
                / "com.plexapp.plugins.library.db"
            )
            database_path.parent.mkdir(parents=True)
            with closing(sqlite3.connect(database_path)) as connection, connection:
                connection.execute(
                    "CREATE TABLE media_parts (id INTEGER PRIMARY KEY, hash TEXT, file TEXT)"
                )
                connection.execute(
                    "INSERT INTO media_parts(id,hash,file) VALUES (?,?,?)",
                    (501, media_hash, expected),
                )

            bif_path = (
                root
                / "Media"
                / "localhost"
                / media_hash[0]
                / f"{media_hash[1:]}.bundle"
                / "Contents"
                / "Indexes"
                / "index-sd.bif"
            )
            bif_path.parent.mkdir(parents=True)
            bif_path.write_bytes(bif)

            local_root = Path(temporary) / "tv"
            mapper = ExternalPathMapper(
                [PathMapping("plex", "/srv/tv", str(local_root))]
            )
            source = PlexBifSource(
                "https://plex.local:32400",
                "secret",
                mapper,
                metadata_root=str(root),
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
                    return_value=candidate,
                ),
                patch(
                    "app.media_identity.sources.plex.fetch_plex_bif_index",
                    side_effect=PlexPreviewUnavailable("HTTP BIF unavailable"),
                ),
            ):
                frames = source.preview_frames(media)

            with (
                patch(
                    "app.media_identity.sources.plex.read_bif_index",
                    side_effect=AssertionError(
                        "read_preview must not reopen the BIF index separately"
                    ),
                ),
                patch(
                    "app.media_identity.sources.plex.read_bif_preview_range",
                    side_effect=AssertionError(
                        "read_preview must not reopen the BIF payload separately"
                    ),
                ),
            ):
                image = source.read_preview(frames[1])

            self.assertEqual(len(frames), 2)
            asset = json.loads(frames[1].asset_ref)
            self.assertEqual(asset["kind"], "plex_bif")
            self.assertEqual(Path(asset["path"]).resolve(), bif_path.resolve())
            self.assertEqual(asset["part_id"], "501")
            self.assertEqual(asset["expected_external_path"], expected)
            self.assertEqual(Path(asset["metadata_root"]), root)
            self.assertEqual(image, FRAME_ONE)

    def test_blank_metadata_root_auto_detects_local_plex_fallback(self):
        expected = "/srv/tv/Show/Season 01/Episode.mkv"
        candidate = plex_episode_item(
            rating_key="101",
            path=expected,
            part_id="501",
        )
        media_hash = "a" + "b" * 39
        bif = build_bif()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Plex Media Server"
            database_path = (
                root
                / "Plug-in Support"
                / "Databases"
                / "com.plexapp.plugins.library.db"
            )
            database_path.parent.mkdir(parents=True)
            with closing(sqlite3.connect(database_path)) as connection, connection:
                connection.execute(
                    "CREATE TABLE media_parts (id INTEGER PRIMARY KEY, hash TEXT, file TEXT)"
                )
                connection.execute(
                    "INSERT INTO media_parts(id,hash,file) VALUES (?,?,?)",
                    (501, media_hash, expected),
                )
            bif_path = (
                root
                / "Media"
                / "localhost"
                / media_hash[0]
                / f"{media_hash[1:]}.bundle"
                / "Contents"
                / "Indexes"
                / "index-sd.bif"
            )
            bif_path.parent.mkdir(parents=True)
            bif_path.write_bytes(bif)

            local_root = Path(temporary) / "tv"
            source = PlexBifSource(
                "https://plex.local:32400",
                "secret",
                ExternalPathMapper(
                    [PathMapping("plex", "/srv/tv", str(local_root))]
                ),
                metadata_root="",
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
                    return_value=candidate,
                ),
                patch(
                    "app.media_identity.sources.plex.fetch_plex_bif_index",
                    side_effect=PlexPreviewUnavailable("HTTP BIF unavailable"),
                ),
                patch(
                    "app.media_identity.sources.plex.detect_plex_metadata_root",
                    return_value=root,
                ),
            ):
                frames = source.preview_frames(media)
                image = source.read_preview(frames[0])

            self.assertEqual(len(frames), 2)
            asset = json.loads(frames[0].asset_ref)
            self.assertEqual(Path(asset["metadata_root"]), root)
            self.assertEqual(image, FRAME_ZERO)

    def test_source_failure_does_not_fall_back_to_local_preview(self):
        expected = "/srv/tv/Show/Season 01/Episode.mkv"
        candidate = plex_episode_item(path=expected, part_id="501")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Plex Media Server"
            local_root = Path(temporary) / "tv"
            source = PlexBifSource(
                "https://plex.local:32400",
                "secret",
                ExternalPathMapper(
                    [PathMapping("plex", "/srv/tv", str(local_root))]
                ),
                metadata_root=str(root),
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
                    return_value=candidate,
                ),
                patch(
                    "app.media_identity.sources.plex.fetch_plex_bif_index",
                    side_effect=PlexBifError("Plex rejected the access token."),
                ),
                patch(
                    "app.media_identity.sources.plex.resolve_local_bif_path"
                ) as local_fallback,
            ):
                with self.assertRaisesRegex(
                    PlexBifError, "rejected the access token"
                ):
                    source.preview_frames(media)

            local_fallback.assert_not_called()

    def test_optional_preview_unavailable_without_local_fallback_returns_no_frames(self):
        expected = "/srv/tv/Show/Season 01/Episode.mkv"
        candidate = plex_episode_item(path=expected, part_id="501")
        with tempfile.TemporaryDirectory() as temporary:
            local_root = Path(temporary) / "tv"
            source = PlexBifSource(
                "https://plex.local:32400",
                "secret",
                ExternalPathMapper(
                    [PathMapping("plex", "/srv/tv", str(local_root))]
                ),
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
                    return_value=candidate,
                ),
                patch(
                    "app.media_identity.sources.plex.fetch_plex_bif_index",
                    side_effect=PlexPreviewUnavailable("missing BIF"),
                ),
                patch(
                    "app.media_identity.sources.plex.detect_plex_metadata_root",
                    return_value=None,
                ),
            ):
                frames = source.preview_frames(media)

            self.assertEqual(frames, ())

    def test_corrupt_local_fallback_is_optional_when_http_preview_is_unavailable(self):
        expected = "/srv/tv/Show/Season 01/Episode.mkv"
        candidate = plex_episode_item(path=expected, part_id="501")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Plex Media Server"
            local_root = Path(temporary) / "tv"
            source = PlexBifSource(
                "https://plex.local:32400",
                "secret",
                ExternalPathMapper(
                    [PathMapping("plex", "/srv/tv", str(local_root))]
                ),
                metadata_root=str(root),
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
                    return_value=candidate,
                ),
                patch(
                    "app.media_identity.sources.plex.fetch_plex_bif_index",
                    side_effect=PlexPreviewUnavailable("unsupported HTTP BIF"),
                ),
                patch(
                    "app.media_identity.sources.plex.resolve_local_bif_path",
                    side_effect=PlexBifError("unsupported local database layout"),
                ),
            ):
                frames = source.preview_frames(media)

            self.assertEqual(frames, ())

    def test_local_preview_reanchors_part_and_path_before_read(self):
        expected = "/srv/tv/Show/Season 01/Episode.mkv"
        candidate = plex_episode_item(
            rating_key="101",
            path=expected,
            part_id="501",
        )
        media_hash = "a" + "b" * 39
        bif = build_bif()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Plex Media Server"
            database_path = (
                root
                / "Plug-in Support"
                / "Databases"
                / "com.plexapp.plugins.library.db"
            )
            database_path.parent.mkdir(parents=True)
            with closing(sqlite3.connect(database_path)) as connection, connection:
                connection.execute(
                    "CREATE TABLE media_parts (id INTEGER PRIMARY KEY, hash TEXT, file TEXT)"
                )
                connection.execute(
                    "INSERT INTO media_parts(id,hash,file) VALUES (?,?,?)",
                    (501, media_hash, expected),
                )
            bif_path = (
                root
                / "Media"
                / "localhost"
                / media_hash[0]
                / f"{media_hash[1:]}.bundle"
                / "Contents"
                / "Indexes"
                / "index-sd.bif"
            )
            bif_path.parent.mkdir(parents=True)
            bif_path.write_bytes(bif)

            local_root = Path(temporary) / "tv"
            source = PlexBifSource(
                "https://plex.local:32400",
                "secret",
                ExternalPathMapper(
                    [PathMapping("plex", "/srv/tv", str(local_root))]
                ),
                metadata_root=str(root),
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
                    return_value=candidate,
                ),
                patch(
                    "app.media_identity.sources.plex.fetch_plex_bif_index",
                    side_effect=PlexPreviewUnavailable("HTTP BIF unavailable"),
                ),
            ):
                frames = source.preview_frames(media)

            with closing(sqlite3.connect(database_path)) as connection, connection:
                connection.execute(
                    "UPDATE media_parts SET file=? WHERE id=?",
                    ("/srv/tv/Other/Episode.mkv", 501),
                )

            with self.assertRaisesRegex(
                PlexPreviewUnavailable, "identity anchor changed"
            ):
                source.read_preview(frames[0])

    def test_local_bif_change_is_rejected_before_preview_read(self):
        expected = "/srv/tv/Show/Season 01/Episode.mkv"
        candidate = plex_episode_item(
            rating_key="101",
            path=expected,
            part_id="501",
        )
        media_hash = "a" + "b" * 39
        original_bif = build_bif()
        changed_bif = build_bif(
            frames=(
                (0, FRAME_ZERO),
                (11, FRAME_ONE),
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Plex Media Server"
            database_path = (
                root
                / "Plug-in Support"
                / "Databases"
                / "com.plexapp.plugins.library.db"
            )
            database_path.parent.mkdir(parents=True)
            with closing(sqlite3.connect(database_path)) as connection, connection:
                connection.execute(
                    "CREATE TABLE media_parts (id INTEGER PRIMARY KEY, hash TEXT, file TEXT)"
                )
                connection.execute(
                    "INSERT INTO media_parts(id,hash,file) VALUES (?,?,?)",
                    (501, media_hash, expected),
                )

            bif_path = (
                root
                / "Media"
                / "localhost"
                / media_hash[0]
                / f"{media_hash[1:]}.bundle"
                / "Contents"
                / "Indexes"
                / "index-sd.bif"
            )
            bif_path.parent.mkdir(parents=True)
            bif_path.write_bytes(original_bif)

            local_root = Path(temporary) / "tv"
            source = PlexBifSource(
                "https://plex.local:32400",
                "secret",
                ExternalPathMapper(
                    [PathMapping("plex", "/srv/tv", str(local_root))]
                ),
                metadata_root=str(root),
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
                    return_value=candidate,
                ),
                patch(
                    "app.media_identity.sources.plex.fetch_plex_bif_index",
                    side_effect=PlexPreviewUnavailable("HTTP BIF unavailable"),
                ),
            ):
                frames = source.preview_frames(media)

            bif_path.write_bytes(changed_bif)

            with self.assertRaisesRegex(
                PlexPreviewUnavailable, "changed after preview frames were enumerated"
            ):
                source.read_preview(frames[0])

    def test_custom_metadata_root_rejects_wrong_media_part_path(self):
        expected = "/srv/tv/Show/Season 01/Episode.mkv"
        candidate = plex_episode_item(
            rating_key="101",
            path=expected,
            part_id="501",
        )
        media_hash = "a" + "b" * 39
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Plex Media Server"
            database_path = (
                root
                / "Plug-in Support"
                / "Databases"
                / "com.plexapp.plugins.library.db"
            )
            database_path.parent.mkdir(parents=True)
            with closing(sqlite3.connect(database_path)) as connection, connection:
                connection.execute(
                    "CREATE TABLE media_parts (id INTEGER PRIMARY KEY, hash TEXT, file TEXT)"
                )
                connection.execute(
                    "INSERT INTO media_parts(id,hash,file) VALUES (?,?,?)",
                    (501, media_hash, "/srv/tv/Other/Episode.mkv"),
                )

            local_root = Path(temporary) / "tv"
            source = PlexBifSource(
                "https://plex.local:32400",
                "secret",
                ExternalPathMapper(
                    [PathMapping("plex", "/srv/tv", str(local_root))]
                ),
                metadata_root=str(root),
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
                    return_value=candidate,
                ),
                patch(
                    "app.media_identity.sources.plex.fetch_plex_bif_index",
                    side_effect=PlexPreviewUnavailable("HTTP BIF unavailable"),
                ),
            ):
                frames = source.preview_frames(media)

            self.assertEqual(frames, ())

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
                "https://plex.local:32400",
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

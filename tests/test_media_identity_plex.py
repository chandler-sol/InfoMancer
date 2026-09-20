from __future__ import annotations

from pathlib import Path
import json
import struct
import tempfile
import unittest

from app.media_identity.sources.plex import (
    PlexBifError,
    enumerate_bif_preview_frames,
    normalize_plex_metadata_root,
    parse_bif_index,
    plex_bif_path_for_bundle,
    read_bif_index,
)


_MAGIC = b"\x89BIF\r\n\x1a\n"


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


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.media_identity.text import (
    MAX_SIDECAR_COUNT,
    discover_sidecar_subtitles,
    read_sidecar_text,
    sidecar_identity,
)


class FastSidecarFreshnessTests(unittest.TestCase):
    def test_changed_sidecar_cannot_reuse_old_cache_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sidecar = Path(temporary) / "Episode.en.srt"
            sidecar.write_text(
                "1\n00:00:00,000 --> 00:00:02,000\nOriginal dialogue\n",
                encoding="utf-8",
            )
            original = sidecar_identity(sidecar)
            self.assertIsNotNone(original)
            assert original is not None
            first = read_sidecar_text(sidecar, original)
            self.assertIsNotNone(first)

            sidecar.write_text(
                "1\n00:00:00,000 --> 00:00:03,000\nChanged dialogue with extra bytes\n",
                encoding="utf-8",
            )
            current = sidecar_identity(sidecar)
            self.assertIsNotNone(current)
            assert current is not None

            self.assertNotEqual(current.cache_key, original.cache_key)
            self.assertIsNone(read_sidecar_text(sidecar, original))
            refreshed = read_sidecar_text(sidecar, current)
            self.assertIsNotNone(refreshed)
            assert refreshed is not None
            self.assertIn("changed dialogue", refreshed.normalized_text)


    def test_sidecar_read_is_hard_bounded_if_file_grows_during_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sidecar = Path(temporary) / "Episode.en.srt"
            sidecar.write_bytes(b"short subtitle")
            identity = sidecar_identity(sidecar)
            self.assertIsNotNone(identity)
            assert identity is not None

            read_sizes: list[int] = []
            real_read = os.read

            def simulate_growth(descriptor: int, count: int) -> bytes:
                read_sizes.append(count)
                return b"x" * count

            with (
                patch("app.media_identity.text.MAX_SIDECAR_BYTES", 64),
                patch("app.media_identity.text.os.read", side_effect=simulate_growth),
            ):
                result = read_sidecar_text(sidecar, identity)

            self.assertIsNone(result)
            self.assertTrue(read_sizes)
            self.assertLessEqual(max(read_sizes), 65)
            self.assertIsNotNone(real_read)

    def test_oversized_replacement_before_descriptor_read_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sidecar = Path(temporary) / "Episode.en.srt"
            sidecar.write_bytes(b"original")
            identity = sidecar_identity(sidecar)
            self.assertIsNotNone(identity)
            assert identity is not None

            real_open = os.open

            def replace_then_open(path, flags, *args, **kwargs):
                sidecar.write_bytes(b"x" * 128)
                return real_open(path, flags, *args, **kwargs)

            with (
                patch("app.media_identity.text.MAX_SIDECAR_BYTES", 64),
                patch("app.media_identity.text.os.open", side_effect=replace_then_open),
                patch("app.media_identity.text.os.read") as mocked_read,
            ):
                result = read_sidecar_text(sidecar, identity)

            self.assertIsNone(result)
            mocked_read.assert_not_called()

    def test_same_size_same_mtime_atomic_replacement_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sidecar = root / "Episode.en.srt"
            sidecar.write_bytes(b"old text")
            original_stat = sidecar.stat()
            identity = sidecar_identity(sidecar)
            self.assertIsNotNone(identity)
            assert identity is not None
            if identity.inode_id is None:
                self.skipTest("filesystem does not expose a meaningful inode")

            replacement = root / "replacement.tmp"
            replacement.write_bytes(b"new text")
            os.utime(
                replacement,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )
            real_open = os.open
            replaced = False

            def replace_then_open(path, flags, *args, **kwargs):
                nonlocal replaced
                if not replaced:
                    os.replace(replacement, sidecar)
                    replaced = True
                return real_open(path, flags, *args, **kwargs)

            with patch(
                "app.media_identity.text.os.open",
                side_effect=replace_then_open,
            ):
                result = read_sidecar_text(sidecar, identity)

            self.assertIsNone(result)
            refreshed = sidecar_identity(sidecar)
            self.assertIsNotNone(refreshed)
            assert refreshed is not None
            self.assertNotEqual(refreshed.inode_id, identity.inode_id)
            self.assertNotEqual(refreshed.cache_key, identity.cache_key)

    def test_sidecar_discovery_caps_matching_file_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media = root / "Episode.mkv"
            media.write_bytes(b"media")
            for index in range(MAX_SIDECAR_COUNT + 6):
                (root / f"Episode.lang{index:02d}.srt").write_bytes(b"subtitle")
            found = discover_sidecar_subtitles(media)
            self.assertEqual(len(found), MAX_SIDECAR_COUNT)

    def test_sidecar_discovery_sorts_bounded_pool_before_applying_caps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media = root / "Episode.mkv"
            media.write_bytes(b"media")
            zulu = root / "Episode.z.srt"
            alpha = root / "Episode.a.srt"
            middle = root / "Episode.m.srt"
            for sidecar in (zulu, alpha, middle):
                sidecar.write_bytes(b"subtitle")

            scrambled = iter([zulu, middle, alpha, media])
            with (
                patch.object(Path, "iterdir", return_value=scrambled),
                patch("app.media_identity.text.MAX_SIDECAR_COUNT", 2),
                patch("app.media_identity.text.MAX_SIDECAR_SELECTION_POOL", 2),
            ):
                found = discover_sidecar_subtitles(media)

            self.assertEqual(
                [path.name for path in found],
                ["Episode.a.srt", "Episode.m.srt"],
            )

    def test_sidecar_discovery_caps_cumulative_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media = root / "Episode.mkv"
            media.write_bytes(b"media")
            for index in range(4):
                (root / f"Episode.lang{index:02d}.srt").write_bytes(b"x" * 40)
            with patch("app.media_identity.text.MAX_TOTAL_SIDECAR_BYTES", 80):
                found = discover_sidecar_subtitles(media)
            self.assertEqual(len(found), 2)


if __name__ == "__main__":
    unittest.main()

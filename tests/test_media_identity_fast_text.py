from __future__ import annotations

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


    def test_sidecar_discovery_caps_matching_file_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media = root / "Episode.mkv"
            media.write_bytes(b"media")
            for index in range(MAX_SIDECAR_COUNT + 6):
                (root / f"Episode.lang{index:02d}.srt").write_bytes(b"subtitle")
            found = discover_sidecar_subtitles(media)
            self.assertEqual(len(found), MAX_SIDECAR_COUNT)

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

from __future__ import annotations

from dataclasses import replace
from io import BytesIO
import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.media_identity.local_frames import (
    LOCAL_FRAME_SOURCE_KEY,
    LocalFfmpegFrameSource,
    LocalFrameSourceFailure,
    LocalFrameUnavailable,
)
from app.media_identity.models import (
    AnalyzerContext,
    IdentityProfile,
    IdentityReference,
    MediaIdentityFile,
)


def jpeg_bytes(width=640, height=360):
    output = BytesIO()
    Image.new("RGB", (width, height), "white").save(
        output,
        format="JPEG",
        quality=85,
    )
    return output.getvalue()


class LocalFfmpegFrameSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.media = self.root / "episode.mkv"
        self.media.write_bytes(b"fixture-media" * 64)
        self.ffmpeg = self.root / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        self.ffmpeg.write_bytes(b"fixture-ffmpeg-v1")
        if os.name != "nt":
            self.ffmpeg.chmod(0o755)
        stat = self.media.stat()
        self.context = AnalyzerContext(
            media=MediaIdentityFile(
                file_id=7,
                title_id=3,
                path=str(self.media),
                size_bytes=stat.st_size,
                modified_at=stat.st_mtime,
                sha256=hashlib.sha256(self.media.read_bytes()).hexdigest(),
            ),
            claimed_identity=IdentityReference(
                identity_kind="episode",
                season=1,
                episode=2,
            ),
            profile=IdentityProfile.NORMAL,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def source(self, **kwargs):
        return LocalFfmpegFrameSource(
            self.context,
            1800.0,
            executable=kwargs.pop("executable", str(self.ffmpeg)),
            **kwargs,
        )

    def test_status_requires_ffmpeg_and_current_media_snapshot(self):
        status = self.source().status()
        self.assertTrue(status.available)
        self.assertIn("PREVIEW_FRAMES", repr(status.capabilities))

        missing = self.root / "missing-ffmpeg"
        status = self.source(executable=str(missing)).status()
        self.assertFalse(status.available)
        self.assertIn("FFmpeg", status.detail)

        self.media.write_bytes(self.media.read_bytes() + b"changed")
        status = self.source().status()
        self.assertFalse(status.available)
        self.assertIn("snapshot", status.detail)

    def test_preview_enumeration_is_deterministic_and_avoids_exact_edges(self):
        with patch(
            "app.media_identity.local_frames.shutil.which",
            return_value="/usr/bin/ffmpeg",
        ):
            source = self.source()
            media = source.resolve_media(self.context)
            self.assertIsNotNone(media)
            frames = source.preview_frames(media)

        self.assertEqual(len(frames), 40)
        self.assertTrue(all(frame.source_key == LOCAL_FRAME_SOURCE_KEY for frame in frames))
        self.assertGreater(frames[0].timestamp_ms, 0)
        self.assertLess(frames[-1].timestamp_ms, 1_800_000)
        self.assertEqual(
            [frame.timestamp_ms for frame in frames],
            sorted(frame.timestamp_ms for frame in frames),
        )
        self.assertEqual(
            len({frame.timestamp_ms for frame in frames}),
            len(frames),
        )

    def test_read_preview_runs_one_bounded_software_frame_extraction(self):
        payload = jpeg_bytes()
        result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=payload,
            stderr=b"",
        )
        with (
            patch(
                "app.media_identity.local_frames.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "app.media_identity.local_frames.subprocess.run",
                return_value=result,
            ) as run,
        ):
            source = self.source()
            media = source.resolve_media(self.context)
            frame = source.preview_frames(media)[10]
            extracted = source.read_preview(frame)

        self.assertEqual(extracted, payload)
        command = run.call_args.args[0]
        self.assertEqual(command[0], str(self.ffmpeg.resolve()))
        self.assertIn("-nostdin", command)
        self.assertIn("-ss", command)
        self.assertIn("-frames:v", command)
        self.assertIn("1", command)
        self.assertIn("-fs", command)
        self.assertIn("image2pipe", command)
        self.assertIn("mjpeg", command)
        if os.name == "nt":
            self.assertIn(str(self.media.resolve()), command)
            self.assertNotIn("pass_fds", run.call_args.kwargs)
        else:
            self.assertIn("-fd", command)
            self.assertEqual(command[command.index("-i") + 1], "fd:")
            self.assertNotIn(str(self.media), command)
            self.assertEqual(len(run.call_args.kwargs["pass_fds"]), 1)
        self.assertNotIn("-hwaccel", command)
        self.assertEqual(run.call_args.kwargs["timeout"], 20)
        self.assertFalse(run.call_args.kwargs["check"])
        self.assertIs(
            run.call_args.kwargs["stderr"],
            subprocess.DEVNULL,
        )
        self.assertIs(
            run.call_args.kwargs["stdout"],
            subprocess.PIPE,
        )

    @unittest.skipUnless(os.name == "nt", "Windows lease semantics test")
    def test_windows_lease_blocks_parent_rename_until_cleanup(self):
        source = self.source()
        moved = self.root.with_name(self.root.name + "-moved")
        try:
            media = source.resolve_media(self.context)
            self.assertIsNotNone(media)
            with self.assertRaises(OSError):
                os.replace(self.root, moved)
        finally:
            source.close()

        os.replace(self.root, moved)
        try:
            self.assertTrue((moved / "episode.mkv").is_file())
        finally:
            os.replace(moved, self.root)

    @unittest.skipIf(os.name == "nt", "POSIX descriptor binding test")
    def test_path_redirection_cannot_change_leased_ffmpeg_input(self):
        payload = jpeg_bytes()
        original = self.media.read_bytes()
        alternate = self.root / "alternate.mkv"
        alternate.write_bytes(b"alternate-media" * 64)
        alias = self.root / "episode-alias.mkv"
        alias.symlink_to(self.media)
        alias_context = replace(
            self.context,
            media=replace(self.context.media, path=str(alias)),
        )
        source = LocalFfmpegFrameSource(
            alias_context,
            1800.0,
            executable=str(self.ffmpeg),
        )

        def redirect_alias(command, **kwargs):
            descriptor = kwargs["pass_fds"][0]
            alias.unlink()
            alias.symlink_to(alternate)
            try:
                leased_bytes = os.pread(
                    descriptor,
                    len(original),
                    0,
                )
                self.assertEqual(leased_bytes, original)
                self.assertEqual(
                    command[command.index("-i") + 1],
                    "fd:",
                )
            finally:
                alias.unlink()
                alias.symlink_to(self.media)
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=payload,
                stderr=b"",
            )

        try:
            media = source.resolve_media(alias_context)
            frame = source.preview_frames(media)[0]
            with patch(
                "app.media_identity.local_frames.subprocess.run",
                side_effect=redirect_alias,
            ):
                self.assertEqual(source.read_preview(frame), payload)
        finally:
            source.close()

    @unittest.skipIf(os.name == "nt", "POSIX descriptor binding test")
    def test_parent_directory_redirection_cannot_change_leased_ffmpeg_input(self):
        payload = jpeg_bytes()
        original = self.media.read_bytes()
        held_root = self.root.with_name(self.root.name + "-held")
        replacement_root = self.root.with_name(self.root.name + "-replacement")
        replacement_root.mkdir()
        (replacement_root / self.media.name).write_bytes(
            b"replacement-parent-media" * 64
        )
        source = self.source()

        def redirect_parent(command, **kwargs):
            descriptor = kwargs["pass_fds"][0]
            os.replace(self.root, held_root)
            os.replace(replacement_root, self.root)
            try:
                self.assertEqual(
                    os.pread(descriptor, len(original), 0),
                    original,
                )
                self.assertEqual(
                    command[command.index("-i") + 1],
                    "fd:",
                )
            finally:
                os.replace(self.root, replacement_root)
                os.replace(held_root, self.root)
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=payload,
                stderr=b"",
            )

        try:
            media = source.resolve_media(self.context)
            self.assertIsNotNone(media)
            frame = source.preview_frames(media)[0]
            with patch(
                "app.media_identity.local_frames.subprocess.run",
                side_effect=redirect_parent,
            ):
                self.assertEqual(source.read_preview(frame), payload)
        finally:
            source.close()
            if replacement_root.exists():
                for item in replacement_root.iterdir():
                    item.unlink()
                replacement_root.rmdir()

    def test_timeout_is_optional_preview_failure(self):
        with (
            patch(
                "app.media_identity.local_frames.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "app.media_identity.local_frames.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["ffmpeg"], 20),
            ),
        ):
            source = self.source()
            try:
                media = source.resolve_media(self.context)
                frame = source.preview_frames(media)[0]
                with self.assertRaisesRegex(LocalFrameUnavailable, "timed out"):
                    source.read_preview(frame)
            finally:
                source.close()

    def test_ffmpeg_change_invalidates_prepared_source_and_cache_signature(self):
        source = self.source()
        media = source.resolve_media(self.context)
        self.assertIsNotNone(media)
        original_signature = media.source_signature

        self.ffmpeg.write_bytes(b"fixture-ffmpeg-v2-with-different-size")
        if os.name != "nt":
            self.ffmpeg.chmod(0o755)

        status = source.status()
        self.assertFalse(status.available)
        self.assertIn("FFmpeg", status.detail)

        refreshed = self.source()
        refreshed_media = refreshed.resolve_media(self.context)
        self.assertIsNotNone(refreshed_media)
        self.assertNotEqual(
            refreshed_media.source_signature,
            original_signature,
        )

    def test_media_change_during_extraction_fails_closed(self):
        payload = jpeg_bytes()

        def mutate_and_return(*_args, **_kwargs):
            self.media.write_bytes(self.media.read_bytes() + b"changed")
            return subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=payload,
                stderr=b"",
            )

        with (
            patch(
                "app.media_identity.local_frames.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "app.media_identity.local_frames.subprocess.run",
                side_effect=mutate_and_return,
            ),
        ):
            source = self.source()
            media = source.resolve_media(self.context)
            frame = source.preview_frames(media)[0]
            with self.assertRaises(LocalFrameSourceFailure):
                source.read_preview(frame)

    def test_same_size_same_mtime_a_b_a_during_extraction_fails_closed(self):
        payload = jpeg_bytes()
        original = self.media.read_bytes()
        original_stat = self.media.stat()
        replacement = bytearray(original)
        replacement[0] ^= 0x01
        replacement = bytes(replacement)

        def cycle_content(*_args, **_kwargs):
            self.media.write_bytes(replacement)
            os.utime(
                self.media,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )
            self.media.write_bytes(original)
            os.utime(
                self.media,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )
            return subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=payload,
                stderr=b"",
            )

        source = self.source()
        try:
            media = source.resolve_media(self.context)
            frame = source.preview_frames(media)[0]
            with patch(
                "app.media_identity.local_frames.subprocess.run",
                side_effect=cycle_content,
            ):
                with self.assertRaises(LocalFrameSourceFailure):
                    source.read_preview(frame)
        finally:
            source.close()

        self.assertEqual(self.media.read_bytes(), original)

    def test_same_size_same_mtime_file_replacement_is_rejected_when_inode_is_available(self):
        with patch(
            "app.media_identity.local_frames.shutil.which",
            return_value="/usr/bin/ffmpeg",
        ):
            source = self.source()
            if source._inode_id is None:
                self.skipTest("filesystem does not expose a stable inode identity")

            original_stat = self.media.stat()
            replacement = self.root / "replacement.mkv"
            replacement.write_bytes(b"x" * original_stat.st_size)
            os.utime(
                replacement,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )
            os.replace(replacement, self.media)

            status = source.status()

        self.assertFalse(status.available)
        self.assertIn("snapshot", status.detail)

    def test_tampered_generated_frame_reference_is_rejected_before_ffmpeg(self):
        with (
            patch(
                "app.media_identity.local_frames.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "app.media_identity.local_frames.subprocess.run",
            ) as run,
        ):
            source = self.source()
            media = source.resolve_media(self.context)
            frame = source.preview_frames(media)[0]
            with self.assertRaisesRegex(
                LocalFrameSourceFailure,
                "extraction policy",
            ):
                source.read_preview(
                    replace(frame, asset_ref="ffmpeg:tampered")
                )

        run.assert_not_called()

    def test_generated_image_dimensions_are_revalidated(self):
        oversized = jpeg_bytes(width=1400, height=720)
        result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=oversized,
            stderr=b"",
        )
        with (
            patch(
                "app.media_identity.local_frames.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "app.media_identity.local_frames.subprocess.run",
                return_value=result,
            ),
        ):
            source = self.source()
            media = source.resolve_media(self.context)
            frame = source.preview_frames(media)[0]
            with self.assertRaisesRegex(
                LocalFrameUnavailable,
                "dimensions",
            ):
                source.read_preview(frame)


if __name__ == "__main__":
    unittest.main()

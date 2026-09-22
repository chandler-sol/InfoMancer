from __future__ import annotations

from io import BytesIO
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

from app.media_identity.models import MediaIdentityFile
from app.media_identity.speech import (
    SpeechAudioIdentity,
    SpeechIdentityError,
    SpeechWindow,
)
from app.media_identity.speech_audio import (
    MAX_NORMAL_SPEECH_AUDIO_BYTES,
    MAX_SPEECH_AUDIO_BYTES,
    SPEECH_AUDIO_CHANNELS,
    SPEECH_AUDIO_FORMAT_KEY,
    SPEECH_AUDIO_SAMPLE_RATE_HZ,
    ExtractedSpeechAudio,
    LocalFfmpegSpeechAudioExtractor,
    SpeechAudioStaleError,
    SpeechAudioStream,
    SpeechAudioUnavailable,
    select_speech_audio_stream,
    validate_normal_speech_audio_budget,
    validate_normal_speech_window_plan,
)


def wav_bytes(
    *,
    duration_ms: int = 1000,
    sample_rate: int = SPEECH_AUDIO_SAMPLE_RATE_HZ,
    channels: int = SPEECH_AUDIO_CHANNELS,
    sample_width: int = 2,
) -> bytes:
    frame_count = max(1, duration_ms * sample_rate // 1000)
    output = BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(sample_width)
        writer.setframerate(sample_rate)
        writer.writeframes(
            b"\x00" * frame_count * channels * sample_width
        )
    return output.getvalue()


class SpeechAudioExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.media_path = self.root / "episode.mkv"
        self.media_path.write_bytes(b"fixture-media" * 128)
        stat = self.media_path.stat()
        self.media = MediaIdentityFile(
            file_id=7,
            title_id=3,
            path=str(self.media_path),
            size_bytes=stat.st_size,
            modified_at=stat.st_mtime,
            sha256="a" * 64,
        )
        self.ffmpeg = self.root / (
            "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        )
        self.ffmpeg.write_bytes(b"fixture-ffmpeg-v1")
        if os.name != "nt":
            self.ffmpeg.chmod(0o755)

        self.streams = [
            {
                "stream_index": 0,
                "stream_type": "video",
                "default_flag": 1,
            },
            {
                "stream_index": 1,
                "stream_type": "audio",
                "language": "eng",
                "default_flag": 1,
                "commentary": 1,
                "channels": 2,
                "sample_rate": 48_000,
                "title": "Director Commentary",
            },
            {
                "stream_index": 2,
                "stream_type": "audio",
                "language": "eng",
                "default_flag": 0,
                "commentary": 0,
                "channels": 6,
                "sample_rate": 48_000,
                "title": "English 5.1",
            },
            {
                "stream_index": 3,
                "stream_type": "audio",
                "language": "jpn",
                "default_flag": 1,
                "commentary": 0,
                "channels": 2,
                "sample_rate": 48_000,
                "title": "Japanese",
            },
        ]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def extractor(self, **kwargs) -> LocalFfmpegSpeechAudioExtractor:
        return LocalFfmpegSpeechAudioExtractor(
            self.media,
            kwargs.pop("streams", self.streams),
            preferred_language=kwargs.pop("preferred_language", "eng"),
            executable=kwargs.pop("executable", str(self.ffmpeg)),
            **kwargs,
        )

    @staticmethod
    def completed(payload: bytes):
        def run(command, **_kwargs):
            Path(command[-1]).write_bytes(payload)
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=None,
                stderr=b"",
            )

        return run

    def test_public_stream_contract_enforces_normalized_invariants(self) -> None:
        stream = SpeechAudioStream(
            index=2,
            language=" ENG ",
            title=" Main ",
            channels=2,
            sample_rate_hz=48_000,
            default=True,
        )
        self.assertEqual(stream.language, "eng")
        self.assertEqual(stream.title, "Main")
        with self.assertRaises(SpeechAudioUnavailable):
            SpeechAudioStream(index=-1)
        with self.assertRaises(SpeechAudioUnavailable):
            SpeechAudioStream(index=1, default=1)
        with self.assertRaises(SpeechAudioUnavailable):
            SpeechAudioStream(index=1, channels=0)

    def test_stream_selection_prefers_primary_requested_language_then_default(self) -> None:
        english = select_speech_audio_stream(
            self.streams,
            preferred_language="eng",
        )
        self.assertEqual(english.index, 2)
        self.assertFalse(english.commentary)

        default = select_speech_audio_stream(self.streams)
        self.assertEqual(default.index, 3)
        self.assertTrue(default.default)

    def test_stream_selection_normalizes_common_language_tags_only_for_matching(self) -> None:
        streams = [
            {
                "stream_index": 4,
                "stream_type": "audio",
                "language": "en",
                "default_flag": 0,
            },
            {
                "stream_index": 5,
                "stream_type": "audio",
                "language": "jpn",
                "default_flag": 1,
            },
        ]
        selected = select_speech_audio_stream(
            streams,
            preferred_language="eng",
        )
        self.assertEqual(selected.index, 4)
        self.assertEqual(selected.language, "en")

    def test_stream_selection_rejects_lossy_stream_index_coercion(self) -> None:
        with self.assertRaisesRegex(SpeechAudioUnavailable, "No usable"):
            select_speech_audio_stream(
                [
                    {
                        "stream_index": 1.5,
                        "stream_type": "audio",
                    }
                ]
            )

    def test_stream_selection_falls_back_deterministically(self) -> None:
        streams = [
            {
                "stream_index": 8,
                "stream_type": "audio",
                "language": "eng",
                "commentary": 1,
            },
            {
                "stream_index": 4,
                "stream_type": "audio",
                "language": "eng",
                "visual_impaired": 1,
                "default_flag": 1,
            },
        ]
        selected = select_speech_audio_stream(
            streams,
            preferred_language="eng",
        )
        self.assertEqual(selected.index, 4)

        with self.assertRaisesRegex(SpeechAudioUnavailable, "No usable"):
            select_speech_audio_stream(
                [{"stream_index": 0, "stream_type": "video"}]
            )

    def test_normal_window_plan_enforces_duplicates_and_240_second_ceiling(self) -> None:
        accepted = validate_normal_speech_window_plan(
            [
                SpeechWindow(0, 60_000),
                SpeechWindow(60_000, 120_000),
                SpeechWindow(120_000, 180_000),
                SpeechWindow(180_000, 240_000),
            ]
        )
        self.assertEqual(sum(item.duration_ms for item in accepted), 240_000)

        with self.assertRaisesRegex(SpeechIdentityError, "repeat"):
            validate_normal_speech_window_plan(
                [
                    SpeechWindow(0, 60_000, "one"),
                    SpeechWindow(0, 60_000, "two"),
                ]
            )

        with self.assertRaisesRegex(SpeechIdentityError, "240 seconds"):
            validate_normal_speech_window_plan(
                [
                    SpeechWindow(0, 90_000),
                    SpeechWindow(90_000, 180_000),
                    SpeechWindow(180_000, 270_000),
                ]
            )

        with self.assertRaisesRegex(SpeechIdentityError, "8 targeted windows"):
            validate_normal_speech_window_plan(
                [
                    SpeechWindow(index * 1000, (index + 1) * 1000)
                    for index in range(9)
                ]
            )

    def test_extract_uses_one_bounded_canonical_software_audio_command(self) -> None:
        payload = wav_bytes(duration_ms=1000)
        with patch(
            "app.media_identity.speech_audio.subprocess.run",
            side_effect=self.completed(payload),
        ) as run:
            artifact = self.extractor().extract(
                SpeechWindow(30_000, 75_000, "dialogue-gap")
            )
            try:
                self.assertEqual(Path(artifact.validated_path()), artifact.path)
                self.assertEqual(
                    artifact.identity.sha256,
                    hashlib.sha256(payload).hexdigest(),
                )
                self.assertEqual(artifact.identity.size_bytes, len(payload))
                self.assertEqual(
                    artifact.identity.format_key,
                    SPEECH_AUDIO_FORMAT_KEY,
                )
                self.assertEqual(
                    artifact.identity.sample_rate_hz,
                    SPEECH_AUDIO_SAMPLE_RATE_HZ,
                )
                self.assertEqual(
                    artifact.identity.channels,
                    SPEECH_AUDIO_CHANNELS,
                )
                self.assertTrue(artifact.identity.source_signature)
                self.assertEqual(artifact.stream.index, 2)

                command = run.call_args.args[0]
                self.assertEqual(command[0], str(self.ffmpeg.resolve()))
                self.assertIn("-nostdin", command)
                self.assertIn("-xerror", command)
                self.assertEqual(
                    command[command.index("-map") + 1],
                    "0:2",
                )
                self.assertEqual(
                    command[command.index("-ss") + 1],
                    "30.000",
                )
                self.assertEqual(
                    command[command.index("-t") + 1],
                    "45.000",
                )
                self.assertEqual(
                    command[command.index("-ac") + 1],
                    "1",
                )
                self.assertEqual(
                    command[command.index("-ar") + 1],
                    "16000",
                )
                self.assertEqual(
                    command[command.index("-c:a") + 1],
                    "pcm_s16le",
                )
                self.assertEqual(
                    int(command[command.index("-fs") + 1]),
                    MAX_SPEECH_AUDIO_BYTES,
                )
                self.assertIn("-map_metadata", command)
                self.assertIn("-map_chapters", command)
                self.assertNotIn("-hwaccel", command)
                self.assertEqual(
                    run.call_args.kwargs["timeout"],
                    60,
                )
                self.assertIs(
                    run.call_args.kwargs["stdout"],
                    subprocess.DEVNULL,
                )
            finally:
                parent = artifact.path.parent
                artifact.cleanup()
                self.assertFalse(parent.exists())

    def test_output_must_be_valid_canonical_wav(self) -> None:
        invalid_payloads = [
            b"not-a-wave",
            wav_bytes(sample_rate=8_000),
            wav_bytes(channels=2),
            wav_bytes(sample_width=1),
        ]
        for payload in invalid_payloads:
            with self.subTest(length=len(payload)):
                with patch(
                    "app.media_identity.speech_audio.subprocess.run",
                    side_effect=self.completed(payload),
                ):
                    with self.assertRaises(SpeechAudioUnavailable):
                        self.extractor().extract(SpeechWindow(0, 5_000))

    def test_truncated_pcm_frame_data_is_rejected(self) -> None:
        payload = wav_bytes(duration_ms=1000)[:-10]
        with patch(
            "app.media_identity.speech_audio.subprocess.run",
            side_effect=self.completed(payload),
        ):
            with self.assertRaisesRegex(
                SpeechAudioUnavailable,
                "truncated PCM",
            ):
                self.extractor().extract(SpeechWindow(0, 2000))

    def test_excessive_wav_container_padding_is_rejected(self) -> None:
        payload = wav_bytes(duration_ms=1000) + (b"x" * (70 * 1024))
        with patch(
            "app.media_identity.speech_audio.subprocess.run",
            side_effect=self.completed(payload),
        ):
            with self.assertRaisesRegex(
                SpeechAudioUnavailable,
                "container overhead",
            ):
                self.extractor().extract(SpeechWindow(0, 2000))

    def test_output_cannot_exceed_requested_duration(self) -> None:
        payload = wav_bytes(duration_ms=2000)
        with patch(
            "app.media_identity.speech_audio.subprocess.run",
            side_effect=self.completed(payload),
        ):
            with self.assertRaisesRegex(
                SpeechAudioUnavailable,
                "window duration",
            ):
                self.extractor().extract(SpeechWindow(0, 1000))

    def test_oversized_output_is_rejected_without_reading_unbounded_bytes(self) -> None:
        payload = b"R" * (MAX_SPEECH_AUDIO_BYTES + 1)
        with patch(
            "app.media_identity.speech_audio.subprocess.run",
            side_effect=self.completed(payload),
        ):
            with self.assertRaisesRegex(
                SpeechAudioUnavailable,
                "bounded regular",
            ):
                self.extractor().extract(SpeechWindow(0, 1000))

    def test_ffmpeg_missing_at_preparation_time_is_optional_unavailable(self) -> None:
        missing = self.root / "missing-ffmpeg"
        extractor = self.extractor(executable=str(missing))
        with self.assertRaisesRegex(
            SpeechAudioUnavailable,
            "FFmpeg is unavailable",
        ):
            extractor.source_signature(SpeechWindow(0, 1000))

    def test_timeout_and_ffmpeg_failure_are_optional_audio_failures(self) -> None:
        with patch(
            "app.media_identity.speech_audio.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["ffmpeg"], 60),
        ):
            with self.assertRaisesRegex(SpeechAudioUnavailable, "timed out"):
                self.extractor().extract(SpeechWindow(0, 1000))

        def failed(command, **_kwargs):
            return subprocess.CompletedProcess(
                args=command,
                returncode=1,
                stdout=None,
                stderr=b"fixture error",
            )

        with patch(
            "app.media_identity.speech_audio.subprocess.run",
            side_effect=failed,
        ):
            with self.assertRaisesRegex(
                SpeechAudioUnavailable,
                "could not extract",
            ):
                self.extractor().extract(SpeechWindow(0, 1000))

    def test_media_change_during_extraction_fails_closed(self) -> None:
        payload = wav_bytes()

        def mutate(command, **_kwargs):
            Path(command[-1]).write_bytes(payload)
            self.media_path.write_bytes(
                self.media_path.read_bytes() + b"changed"
            )
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=None,
                stderr=b"",
            )

        with patch(
            "app.media_identity.speech_audio.subprocess.run",
            side_effect=mutate,
        ):
            with self.assertRaisesRegex(
                SpeechAudioStaleError,
                "media file",
            ):
                self.extractor().extract(SpeechWindow(0, 1000))

    def test_ffmpeg_change_during_extraction_fails_closed(self) -> None:
        payload = wav_bytes()

        def mutate(command, **_kwargs):
            Path(command[-1]).write_bytes(payload)
            self.ffmpeg.write_bytes(b"fixture-ffmpeg-v2-different-size")
            if os.name != "nt":
                self.ffmpeg.chmod(0o755)
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=None,
                stderr=b"",
            )

        with patch(
            "app.media_identity.speech_audio.subprocess.run",
            side_effect=mutate,
        ):
            with self.assertRaisesRegex(
                SpeechAudioStaleError,
                "FFmpeg",
            ):
                self.extractor().extract(SpeechWindow(0, 1000))

    def test_same_size_same_mtime_media_replacement_is_rejected_when_possible(self) -> None:
        extractor = self.extractor()
        if extractor._inode_id is None:
            self.skipTest("filesystem does not expose a stable inode identity")

        original = self.media_path.stat()
        replacement = self.root / "replacement.mkv"
        replacement.write_bytes(b"x" * original.st_size)
        os.utime(
            replacement,
            ns=(original.st_atime_ns, original.st_mtime_ns),
        )
        os.replace(replacement, self.media_path)

        with self.assertRaisesRegex(
            SpeechAudioStaleError,
            "media file",
        ):
            extractor.source_signature(SpeechWindow(0, 1000))

    def test_transient_artifact_cleanup_cannot_claim_arbitrary_directories(self) -> None:
        identity = SpeechAudioIdentity(
            "f" * 64,
            100,
            SPEECH_AUDIO_FORMAT_KEY,
            SPEECH_AUDIO_SAMPLE_RATE_HZ,
            SPEECH_AUDIO_CHANNELS,
        )
        window = SpeechWindow(0, 1000)
        stream = SpeechAudioStream(index=0)

        with self.assertRaisesRegex(
            SpeechAudioUnavailable,
            "owned temporary-directory",
        ):
            ExtractedSpeechAudio(
                temporary_directory=str(self.root),
                path=self.root / "audio.wav",
                identity=identity,
                window=window,
                stream=stream,
            )
        self.assertTrue(self.root.exists())

        with tempfile.TemporaryDirectory() as owned:
            with self.assertRaisesRegex(
                SpeechAudioUnavailable,
                "leaves its owned",
            ):
                ExtractedSpeechAudio(
                    temporary_directory=object.__new__(tempfile.TemporaryDirectory),
                    path=self.root / "outside.wav",
                    identity=identity,
                    window=window,
                    stream=stream,
                )

    def test_final_audio_revalidation_can_bind_the_pending_request_identity(self) -> None:
        payload = wav_bytes(duration_ms=1000)
        with patch(
            "app.media_identity.speech_audio.subprocess.run",
            side_effect=self.completed(payload),
        ):
            artifact = self.extractor().extract(SpeechWindow(0, 1000))

        try:
            other = SpeechAudioIdentity(
                "0" * 64,
                artifact.identity.size_bytes,
                artifact.identity.format_key,
                artifact.identity.sample_rate_hz,
                artifact.identity.channels,
            )
            with self.assertRaisesRegex(
                SpeechAudioStaleError,
                "does not match the request",
            ):
                artifact.validated_path(other)
            self.assertEqual(
                Path(artifact.validated_path(artifact.identity)),
                artifact.path,
            )
        finally:
            artifact.cleanup()

    def test_final_audio_revalidation_detects_same_size_tampering(self) -> None:
        payload = wav_bytes(duration_ms=1000)
        with patch(
            "app.media_identity.speech_audio.subprocess.run",
            side_effect=self.completed(payload),
        ):
            artifact = self.extractor().extract(SpeechWindow(0, 1000))

        try:
            tampered = bytearray(artifact.path.read_bytes())
            tampered[-1] ^= 0x01
            artifact.path.write_bytes(tampered)
            with self.assertRaisesRegex(
                SpeechAudioStaleError,
                "bytes no longer match",
            ):
                artifact.validated_path()
        finally:
            artifact.cleanup()

    def test_source_signature_binds_stream_window_and_ffmpeg_identity(self) -> None:
        first = self.extractor(preferred_language="eng")
        first_signature = first.source_signature(
            SpeechWindow(0, 10_000)
        )
        changed_window = first.source_signature(
            SpeechWindow(10_000, 20_000)
        )
        japanese = self.extractor(preferred_language="jpn")
        changed_stream = japanese.source_signature(
            SpeechWindow(0, 10_000)
        )
        self.assertNotEqual(first_signature, changed_window)
        self.assertNotEqual(first_signature, changed_stream)

        self.ffmpeg.write_bytes(b"fixture-ffmpeg-v2-different-size")
        if os.name != "nt":
            self.ffmpeg.chmod(0o755)
        refreshed = self.extractor(preferred_language="eng")
        changed_ffmpeg = refreshed.source_signature(
            SpeechWindow(0, 10_000)
        )
        self.assertNotEqual(first_signature, changed_ffmpeg)

    def test_aggregate_audio_byte_budget_uses_cleanup_friendly_records(self) -> None:
        windows = [
            SpeechWindow(0, 60_000),
            SpeechWindow(60_000, 120_000),
            SpeechWindow(120_000, 180_000),
            SpeechWindow(180_000, 240_000),
        ]
        each = MAX_NORMAL_SPEECH_AUDIO_BYTES // 4 + 1
        self.assertLessEqual(each, MAX_SPEECH_AUDIO_BYTES)
        records = [
            (
                window,
                SpeechAudioIdentity(
                    f"{index:x}".rjust(64, "0"),
                    each,
                    SPEECH_AUDIO_FORMAT_KEY,
                    SPEECH_AUDIO_SAMPLE_RATE_HZ,
                    SPEECH_AUDIO_CHANNELS,
                ),
            )
            for index, window in enumerate(windows, start=1)
        ]
        with self.assertRaisesRegex(
            SpeechAudioUnavailable,
            "aggregate byte",
        ):
            validate_normal_speech_audio_budget(records)

    def test_aggregate_budget_rejects_noncanonical_audio_identity(self) -> None:
        records = [
            (
                SpeechWindow(0, 60_000),
                SpeechAudioIdentity(
                    "f" * 64,
                    100,
                    "different-format",
                    SPEECH_AUDIO_SAMPLE_RATE_HZ,
                    SPEECH_AUDIO_CHANNELS,
                ),
            )
        ]
        with self.assertRaisesRegex(
            SpeechAudioUnavailable,
            "canonical format",
        ):
            validate_normal_speech_audio_budget(records)

    def test_preferred_language_must_be_stable_text(self) -> None:
        with self.assertRaisesRegex(
            SpeechAudioUnavailable,
            "must be text",
        ):
            select_speech_audio_stream(
                self.streams,
                preferred_language=object(),
            )

    def test_extractor_requires_media_to_exist_at_preparation_time(self) -> None:
        missing = MediaIdentityFile(
            file_id=8,
            title_id=3,
            path=str(self.root / "missing.mkv"),
            size_bytes=100,
            modified_at=1.0,
        )
        with self.assertRaisesRegex(
            SpeechAudioUnavailable,
            "cataloged media file",
        ):
            LocalFfmpegSpeechAudioExtractor(
                missing,
                self.streams,
                executable=str(self.ffmpeg),
            )

    @unittest.skipUnless(
        shutil.which("ffmpeg"),
        "A system FFmpeg is required for the real extraction smoke test.",
    )
    def test_real_ffmpeg_extracts_and_revalidates_canonical_audio(self) -> None:
        real_media = self.root / "real-input.wav"
        real_media.write_bytes(wav_bytes(duration_ms=3000))
        stat = real_media.stat()
        media = MediaIdentityFile(
            file_id=9,
            title_id=3,
            path=str(real_media),
            size_bytes=stat.st_size,
            modified_at=stat.st_mtime,
        )
        extractor = LocalFfmpegSpeechAudioExtractor(
            media,
            [
                {
                    "stream_index": 0,
                    "stream_type": "audio",
                    "language": "eng",
                    "default_flag": 1,
                    "channels": 1,
                    "sample_rate": SPEECH_AUDIO_SAMPLE_RATE_HZ,
                }
            ],
            preferred_language="eng",
            executable=shutil.which("ffmpeg"),
            timeout_seconds=30,
        )
        with extractor.extract(SpeechWindow(500, 1500)) as artifact:
            self.assertTrue(Path(artifact.validated_path()).is_file())
            self.assertEqual(
                artifact.identity.format_key,
                SPEECH_AUDIO_FORMAT_KEY,
            )
            self.assertLessEqual(
                artifact.identity.size_bytes,
                MAX_SPEECH_AUDIO_BYTES,
            )

    def test_timeout_contract_rejects_invalid_values(self) -> None:
        for value in (0, 121, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    SpeechAudioUnavailable,
                    "timeout",
                ):
                    self.extractor(timeout_seconds=value)


if __name__ == "__main__":
    unittest.main()

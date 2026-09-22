from __future__ import annotations

import unittest

from app.media_identity import (
    MediaIdentityFile,
    SpeechEngine,
    SpeechIdentityError,
    SpeechModelIdentity,
    SpeechRequest,
    SpeechTranscript,
    SpeechWindow,
    speech_transcript_cache_key,
)


class FakeSpeechEngine:
    key = "fixture-whisper"
    version = "1"

    def available(self) -> bool:
        return True

    def cache_identity(self):
        return {
            "backend": "cpu",
            "runtime": "fixture-1",
            "threads": 4,
        }

    def transcribe(self, audio_path: str, request: SpeechRequest) -> SpeechTranscript:
        return SpeechTranscript(
            text=f"{audio_path}:{request.window.key}",
            language=request.language or "en",
            confidence=0.8,
        )


class SpeechContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.media = MediaIdentityFile(
            file_id=7,
            title_id=3,
            path="/media/show/episode.mkv",
            size_bytes=123456,
            modified_at=42.5,
            sha256="a" * 64,
        )
        self.model = SpeechModelIdentity(
            key="ggml-base-en",
            version="fixture-v1",
            sha256="b" * 64,
            source="fixture",
            license_id="fixture",
        )

    def request(self, **kwargs) -> SpeechRequest:
        values = {
            "media": self.media,
            "window": SpeechWindow(30_000, 75_000, "dialogue-gap"),
            "model": self.model,
            "language": "en",
            "translate": False,
            "parameters": {"temperature": 0.0},
        }
        values.update(kwargs)
        return SpeechRequest(**values)

    def test_speech_engine_is_a_structural_protocol(self) -> None:
        engine = FakeSpeechEngine()
        self.assertIsInstance(engine, SpeechEngine)
        result = engine.transcribe("/tmp/audio.wav", self.request())
        self.assertEqual(result.language, "en")
        self.assertEqual(result.confidence, 0.8)

    def test_windows_are_positive_bounded_and_stable(self) -> None:
        window = SpeechWindow(10_000, 70_000, "intro")
        self.assertEqual(window.duration_ms, 60_000)
        self.assertEqual(window.key, "10000:70000")
        with self.assertRaises(SpeechIdentityError):
            SpeechWindow(-1, 1000)
        with self.assertRaises(SpeechIdentityError):
            SpeechWindow(1000, 1000)
        with self.assertRaises(SpeechIdentityError):
            SpeechWindow(0, 90_001)

    def test_model_identity_requires_content_hash(self) -> None:
        self.assertEqual(
            self.model.cache_identity(),
            {
                "key": "ggml-base-en",
                "version": "fixture-v1",
                "sha256": "b" * 64,
            },
        )
        with self.assertRaises(SpeechIdentityError):
            SpeechModelIdentity("model", "v1", "not-a-hash")

    def test_transcript_confidence_is_optional_but_bounded(self) -> None:
        self.assertIsNone(SpeechTranscript("words").confidence)
        with self.assertRaises(SpeechIdentityError):
            SpeechTranscript("words", confidence=1.1)

    def test_cache_key_is_deterministic_and_parameter_order_independent(self) -> None:
        engine = FakeSpeechEngine()
        first = self.request(
            parameters={"temperature": 0.0, "beam_size": 5},
        )
        second = self.request(
            parameters={"beam_size": 5, "temperature": 0.0},
        )
        self.assertEqual(
            speech_transcript_cache_key(first, engine),
            speech_transcript_cache_key(second, engine),
        )

    def test_cache_key_changes_for_every_output_affecting_identity(self) -> None:
        baseline = speech_transcript_cache_key(
            self.request(),
            FakeSpeechEngine(),
        )

        changed_window = speech_transcript_cache_key(
            self.request(window=SpeechWindow(31_000, 75_000, "dialogue-gap")),
            FakeSpeechEngine(),
        )
        changed_model = speech_transcript_cache_key(
            self.request(
                model=SpeechModelIdentity(
                    "ggml-base-en",
                    "fixture-v2",
                    "c" * 64,
                )
            ),
            FakeSpeechEngine(),
        )
        changed_language = speech_transcript_cache_key(
            self.request(language="es"),
            FakeSpeechEngine(),
        )
        changed_media = speech_transcript_cache_key(
            self.request(
                media=MediaIdentityFile(
                    7,
                    3,
                    self.media.path,
                    self.media.size_bytes + 1,
                    self.media.modified_at,
                    self.media.sha256,
                )
            ),
            FakeSpeechEngine(),
        )

        class ChangedEngine(FakeSpeechEngine):
            def cache_identity(self):
                return {
                    "backend": "cpu",
                    "runtime": "fixture-2",
                    "threads": 4,
                }

        changed_engine = speech_transcript_cache_key(
            self.request(),
            ChangedEngine(),
        )

        for changed in (
            changed_window,
            changed_model,
            changed_language,
            changed_media,
            changed_engine,
        ):
            self.assertNotEqual(changed, baseline)

    def test_engine_without_deterministic_identity_is_rejected(self) -> None:
        class Broken:
            key = "broken"
            version = "1"

            def available(self):
                return True

            def transcribe(self, audio_path, request):
                return SpeechTranscript("")

        with self.assertRaisesRegex(
            SpeechIdentityError,
            "cache identity",
        ):
            speech_transcript_cache_key(self.request(), Broken())


if __name__ == "__main__":
    unittest.main()

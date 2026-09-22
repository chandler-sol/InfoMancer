from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.media_identity.models import MediaIdentityFile
from app.media_identity.speech import (
    SpeechAudioIdentity,
    SpeechBinaryIdentity,
    SpeechIdentityError,
    SpeechModelIdentity,
    SpeechRequest,
    SpeechWindow,
)
from app.whisper_cpp_speech import (
    WhisperCppSpeechEngine,
    WhisperCppSpeechError,
    _WhisperOutputLimitError,
    _run_bounded_process,
)


class FakeRuntime:
    def __init__(self, root: Path) -> None:
        self.path = root / ("whisper-cli.exe" if os.name == "nt" else "whisper-cli")
        self.path.write_bytes(b"fixture-runtime")
        if os.name != "nt":
            self.path.chmod(0o755)
        self.identity = SpeechBinaryIdentity(
            key="whisper.cpp",
            version="1.9.4",
            sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),
            size_bytes=self.path.stat().st_size,
            source="fixture",
            license_id="MIT",
            details={"runtime_tree_sha256": "a" * 64},
        )
        self.resolve_calls = 0

    def resolve(self):
        self.resolve_calls += 1
        return self.path, self.identity

    def binary_identity(self):
        return self.resolve()[1]

    def launch_environment(self, _path):
        return dict(os.environ)


class FakeModel:
    def __init__(self, root: Path, *, multilingual: bool = True) -> None:
        self.path = root / "model.bin"
        self.path.write_bytes(b"fixture-model")
        self.identity = SpeechModelIdentity(
            key="whisper-base-q5_1" if multilingual else "whisper-base.en-q5_1",
            version="fixture",
            sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),
            size_bytes=self.path.stat().st_size,
            source="fixture",
            license_id="MIT",
            details={
                "multilingual": multilingual,
                "language_scope": "multilingual" if multilingual else "english",
            },
        )
        self.resolve_calls = 0

    def resolve(self):
        self.resolve_calls += 1
        return self.path, self.identity


class WhisperCppSpeechEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.runtime = FakeRuntime(self.root)
        self.model = FakeModel(self.root)
        self.audio = self.root / "speech.wav"
        self.audio.write_bytes(b"R" * 1024)
        stat = self.audio.stat()
        self.audio_identity = SpeechAudioIdentity(
            hashlib.sha256(self.audio.read_bytes()).hexdigest(),
            stat.st_size,
            "wav-pcm-s16le",
            16_000,
            1,
            source_signature="fixture",
        )
        media_path = self.root / "episode.mkv"
        media_path.write_bytes(b"media")
        media_stat = media_path.stat()
        self.media = MediaIdentityFile(
            file_id=1,
            title_id=2,
            path=str(media_path),
            size_bytes=media_stat.st_size,
            modified_at=media_stat.st_mtime,
            sha256="b" * 64,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(self, **kwargs) -> SpeechRequest:
        return SpeechRequest(
            media=self.media,
            window=SpeechWindow(0, 1000, kwargs.pop("purpose", "target")),
            audio=self.audio_identity,
            model=kwargs.pop("model", self.model.identity),
            language=kwargs.pop("language", "eng"),
            translate=kwargs.pop("translate", False),
            parameters=kwargs.pop("parameters", {}),
            **kwargs,
        )

    def test_engine_forces_cpu_and_bounded_plain_output(self) -> None:
        engine = WhisperCppSpeechEngine(
            self.runtime,
            self.model,
            threads=3,
            timeout_seconds=42,
        )
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b"  hello there  \n",
            stderr=b"",
        )
        with patch(
            "app.whisper_cpp_speech._run_bounded_process",
            return_value=completed,
        ) as run:
            transcript = engine.transcribe(
                str(self.audio),
                self.request(language="eng"),
            )

        command = run.call_args.args[0]
        self.assertIn("--no-gpu", command)
        self.assertIn("--no-prints", command)
        self.assertIn("--no-timestamps", command)
        self.assertEqual(command[command.index("--threads") + 1], "3")
        self.assertEqual(command[command.index("--language") + 1], "en")
        self.assertEqual(
            run.call_args.kwargs["timeout_seconds"],
            42,
        )
        self.assertEqual(transcript.text, "hello there")
        self.assertEqual(transcript.language, "en")
        self.assertTrue(transcript.details["cpu_only"])
        self.assertEqual(transcript.details["engine_version"], "adapter-1")
        self.assertEqual(transcript.details["runtime_version"], "1.9.4")
        self.assertEqual(
            transcript.details["runtime_tree_sha256"],
            "a" * 64,
        )
        self.assertGreaterEqual(self.runtime.resolve_calls, 1)
        self.assertGreaterEqual(self.model.resolve_calls, 1)

    def test_cache_identity_keeps_runtime_snapshot_in_binary_identity(self) -> None:
        engine = WhisperCppSpeechEngine(self.runtime, self.model, threads=4)
        before = self.runtime.resolve_calls
        identity = engine.cache_identity()
        self.assertEqual(self.runtime.resolve_calls, before)
        self.assertEqual(identity["threads"], 4)
        self.assertTrue(identity["cpu_only"])
        self.assertIn("adapter_version", identity)
        self.assertNotIn("runtime_tree_sha256", identity)
        self.assertEqual(
            self.runtime.identity.cache_identity()["runtime_tree_sha256"],
            "a" * 64,
        )

    def test_exact_audio_is_rehashed_immediately_before_launch(self) -> None:
        engine = WhisperCppSpeechEngine(self.runtime, self.model)
        self.audio.write_bytes(b"X" * self.audio_identity.size_bytes)
        with patch("app.whisper_cpp_speech._run_bounded_process") as run:
            with self.assertRaisesRegex(
                WhisperCppSpeechError,
                "bytes no longer match",
            ):
                engine.transcribe(str(self.audio), self.request())
        run.assert_not_called()

    def test_model_identity_must_match_request(self) -> None:
        engine = WhisperCppSpeechEngine(self.runtime, self.model)
        wrong = SpeechModelIdentity(
            key="wrong",
            version="1",
            sha256="c" * 64,
            size_bytes=1,
        )
        with patch("app.whisper_cpp_speech._run_bounded_process") as run:
            with self.assertRaisesRegex(
                WhisperCppSpeechError,
                "does not match",
            ):
                engine.transcribe(
                    str(self.audio),
                    self.request(model=wrong),
                )
        run.assert_not_called()

    def test_english_only_model_rejects_other_language_and_translation(self) -> None:
        english = FakeModel(self.root, multilingual=False)
        engine = WhisperCppSpeechEngine(self.runtime, english)
        with self.assertRaisesRegex(
            WhisperCppSpeechError,
            "English-only",
        ):
            engine.transcribe(
                str(self.audio),
                self.request(model=english.identity, language="jpn"),
            )
        with self.assertRaisesRegex(
            WhisperCppSpeechError,
            "translation",
        ):
            engine.transcribe(
                str(self.audio),
                self.request(
                    model=english.identity,
                    language="eng",
                    translate=True,
                ),
            )

    def test_multilingual_empty_language_uses_auto_and_translate_flag(self) -> None:
        engine = WhisperCppSpeechEngine(self.runtime, self.model)
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b"translated",
            stderr=b"",
        )
        with patch(
            "app.whisper_cpp_speech._run_bounded_process",
            return_value=completed,
        ) as run:
            transcript = engine.transcribe(
                str(self.audio),
                self.request(language="", translate=True),
            )
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--language") + 1], "auto")
        self.assertIn("--translate", command)
        self.assertEqual(transcript.language, "")

    def test_unsupported_request_parameters_fail_closed(self) -> None:
        engine = WhisperCppSpeechEngine(self.runtime, self.model)
        with self.assertRaisesRegex(
            SpeechIdentityError,
            "unsupported",
        ):
            engine.transcribe(
                str(self.audio),
                self.request(parameters={"beam_size": 3}),
            )

    def test_timeout_and_nonzero_exit_are_optional_speech_failures(self) -> None:
        engine = WhisperCppSpeechEngine(self.runtime, self.model)
        with patch(
            "app.whisper_cpp_speech._run_bounded_process",
            side_effect=subprocess.TimeoutExpired(["whisper-cli"], 180),
        ):
            with self.assertRaisesRegex(WhisperCppSpeechError, "timed out"):
                engine.transcribe(str(self.audio), self.request())

        with patch(
            "app.whisper_cpp_speech._run_bounded_process",
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout=b"",
                stderr=b"failure",
            ),
        ):
            with self.assertRaisesRegex(
                WhisperCppSpeechError,
                "could not transcribe",
            ):
                engine.transcribe(str(self.audio), self.request())

    def test_process_runner_caps_output_before_returning_it(self) -> None:
        with self.assertRaises(_WhisperOutputLimitError) as caught:
            _run_bounded_process(
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.buffer.write(b'x' * 4096)",
                ],
                cwd=str(self.root),
                env=os.environ,
                timeout_seconds=10,
                stdout_limit=1024,
                stderr_limit=1024,
            )
        self.assertEqual(caught.exception.stream_name, "stdout")

    def test_thread_and_timeout_contracts_are_strict(self) -> None:
        for invalid in (0, -1, 1.5, True, 33):
            with self.subTest(threads=invalid):
                with self.assertRaises(SpeechIdentityError):
                    WhisperCppSpeechEngine(
                        self.runtime,
                        self.model,
                        threads=invalid,
                    )
        for invalid in (0, -1, 1.5, True, 301):
            with self.subTest(timeout=invalid):
                with self.assertRaises(SpeechIdentityError):
                    WhisperCppSpeechEngine(
                        self.runtime,
                        self.model,
                        timeout_seconds=invalid,
                    )


if __name__ == "__main__":
    unittest.main()

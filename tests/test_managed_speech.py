from __future__ import annotations

from io import BytesIO
import hashlib
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from app.managed_speech import (
    ManagedSpeechComponentError,
    ManagedSpeechLayout,
    ManagedWhisperCppRuntime,
    ManagedWhisperModel,
    WHISPERCPP_ASSETS,
    WHISPER_MODELS,
    _download_pinned,
    _extract_runtime_archive,
)
from app.media_identity.speech import (
    SpeechBinaryIdentity,
    SpeechModelIdentity,
)


class ManagedSpeechLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name)
        self.layout = ManagedSpeechLayout(self.data)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def binary_identity(payload: bytes) -> SpeechBinaryIdentity:
        return SpeechBinaryIdentity(
            key="whisper.cpp",
            version="fixture-v1",
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            source="fixture",
            license_id="MIT",
        )

    @staticmethod
    def model_identity(payload: bytes) -> SpeechModelIdentity:
        return SpeechModelIdentity(
            key="ggml-base.en",
            version="fixture-v1",
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            source="fixture",
            license_id="fixture",
        )

    def test_relative_data_root_is_normalized_before_path_checks(self) -> None:
        relative = (
            Path(self.temporary.name).relative_to(Path.cwd())
            if str(self.temporary.name).startswith(str(Path.cwd()))
            else None
        )
        if relative is None:
            self.skipTest(
                "Temporary directory is not beneath the current working directory."
            )
        layout = ManagedSpeechLayout(relative)
        self.assertTrue(layout.data_directory.is_absolute())

    def test_binary_and_model_live_in_separate_component_trees(self) -> None:
        binary = self.binary_identity(b"binary")
        model = self.model_identity(b"model")
        self.assertEqual(
            self.layout.binary_directory(binary),
            self.layout.data_directory
            / "components"
            / "whispercpp"
            / "whisper.cpp"
            / "fixture-v1"
            / binary.sha256,
        )
        self.assertEqual(
            self.layout.model_directory(model),
            self.layout.data_directory
            / "components"
            / "whisper-models"
            / "ggml-base.en"
            / model.sha256,
        )

    def test_same_version_binaries_with_different_hashes_do_not_collide(self) -> None:
        first = self.binary_identity(b"binary-a")
        second = self.binary_identity(b"binary-b")
        self.assertNotEqual(first.sha256, second.sha256)
        self.assertNotEqual(
            self.layout.binary_directory(first),
            self.layout.binary_directory(second),
        )

    def test_component_segments_reject_path_traversal(self) -> None:
        digest = "a" * 64
        with self.assertRaises(ManagedSpeechComponentError):
            self.layout.binary_directory(
                SpeechBinaryIdentity("../escape", "v1", digest, 100)
            )
        with self.assertRaises(ManagedSpeechComponentError):
            self.layout.binary_directory(
                SpeechBinaryIdentity("whisper.cpp", "../escape", digest, 100)
            )
        with self.assertRaises(ManagedSpeechComponentError):
            self.layout.model_path(
                SpeechModelIdentity("model", "v1", digest, 100),
                "../model.bin",
            )

    def test_component_filename_must_be_text(self) -> None:
        identity = self.model_identity(b"model")
        with self.assertRaisesRegex(
            ManagedSpeechComponentError,
            "must be text",
        ):
            self.layout.model_path(identity, object())

    def test_verified_binary_requires_exact_hash_size_and_executable_bit(
        self,
    ) -> None:
        payload = b"trusted-binary"
        identity = self.binary_identity(payload)
        path = self.layout.binary_path(identity, "whisper-cli")
        self.layout.ensure_directories([path.parent])
        path.write_bytes(payload)
        if os.name != "nt":
            path.chmod(0o755)
        self.assertEqual(
            self.layout.binary_candidate(identity, "whisper-cli"),
            path,
        )

        path.write_bytes(b"x" * len(payload))
        if os.name != "nt":
            path.chmod(0o755)
        self.assertIsNone(
            self.layout.binary_candidate(identity, "whisper-cli")
        )

    @unittest.skipIf(os.name == "nt", "POSIX executable bits do not apply on Windows.")
    def test_non_executable_binary_is_rejected(self) -> None:
        payload = b"trusted-binary"
        identity = self.binary_identity(payload)
        path = self.layout.binary_path(identity, "whisper-cli")
        self.layout.ensure_directories([path.parent])
        path.write_bytes(payload)
        path.chmod(0o644)
        self.assertIsNone(
            self.layout.binary_candidate(identity, "whisper-cli")
        )

    def test_verified_model_requires_exact_hash_and_size(self) -> None:
        payload = b"trusted-model"
        identity = self.model_identity(payload)
        path = self.layout.model_path(identity, "model.bin")
        self.layout.ensure_directories([path.parent])
        path.write_bytes(payload)
        self.assertEqual(
            self.layout.model_candidate(identity, "model.bin"),
            path,
        )

        path.write_bytes(b"x" * len(payload))
        self.assertIsNone(
            self.layout.model_candidate(identity, "model.bin")
        )

    def test_size_mismatch_is_rejected_before_hashing(self) -> None:
        payload = b"trusted-model"
        identity = self.model_identity(payload)
        path = self.layout.model_path(identity, "model.bin")
        self.layout.ensure_directories([path.parent])
        path.write_bytes(payload + b"-extra")

        with patch(
            "app.managed_speech._sha256_stream",
            side_effect=AssertionError("hashing should not run"),
        ):
            self.assertIsNone(
                self.layout.model_candidate(identity, "model.bin")
            )

    @unittest.skipIf(
        os.name == "nt",
        "Replacing an open executable is platform-dependent on Windows.",
    )
    def test_path_replacement_during_hash_is_rejected(self) -> None:
        payload = b"trusted-binary"
        identity = self.binary_identity(payload)
        path = self.layout.binary_path(identity, "whisper-cli")
        replacement = path.with_name("replacement")
        self.layout.ensure_directories([path.parent])
        path.write_bytes(payload)
        path.chmod(0o755)

        def replace_path_while_hashing(stream):
            original_bytes = stream.read()
            replacement.write_bytes(payload)
            replacement.chmod(0o755)
            os.replace(replacement, path)
            return hashlib.sha256(original_bytes).hexdigest()

        with patch(
            "app.managed_speech._sha256_stream",
            side_effect=replace_path_while_hashing,
        ):
            self.assertIsNone(
                self.layout.binary_candidate(identity, "whisper-cli")
            )

    def test_junction_or_reparse_component_is_rejected(self) -> None:
        payload = b"trusted-model"
        identity = self.model_identity(payload)
        with patch.object(
            Path,
            "is_junction",
            return_value=True,
            create=True,
        ):
            self.assertIsNone(
                self.layout.model_candidate(identity, "model.bin")
            )
            with self.assertRaises(ManagedSpeechComponentError):
                self.layout.ensure_directories(
                    [self.layout.model_directory(identity)]
                )

    @unittest.skipIf(
        os.name == "nt",
        "Symlink creation is not reliably permitted on Windows CI.",
    )
    def test_symlinked_component_tree_is_rejected(self) -> None:
        outside = self.data / "outside"
        outside.mkdir()
        components = self.data / "components"
        components.symlink_to(outside, target_is_directory=True)

        payload = b"trusted-model"
        identity = self.model_identity(payload)
        self.assertIsNone(
            self.layout.model_candidate(identity, "model.bin")
        )
        with self.assertRaisesRegex(
            ManagedSpeechComponentError,
            "leaves the InfoMancer data directory",
        ):
            self.layout.ensure_directories(
                [self.layout.model_directory(identity)]
            )
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipIf(
        os.name == "nt",
        "Symlink creation is not reliably permitted on Windows CI.",
    )
    def test_broken_symlink_component_is_rejected_cleanly(self) -> None:
        components = self.data / "components"
        components.symlink_to(
            self.data / "missing-target",
            target_is_directory=True,
        )

        identity = self.model_identity(b"trusted-model")
        with self.assertRaises(ManagedSpeechComponentError):
            self.layout.ensure_directories(
                [self.layout.model_directory(identity)]
            )


class ManagedSpeechComponentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def runtime_archive(
        *,
        cli: bytes = b"fixture-whisper-cli",
        companion: bytes = b"fixture-lib",
    ) -> bytes:
        output = BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            for name, payload in (
                ("bin/whisper-cli", cli),
                ("bin/libwhisper.so", companion),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                info.mode = 0o755 if name.endswith("whisper-cli") else 0o644
                archive.addfile(info, BytesIO(payload))
        return output.getvalue()

    def test_runtime_install_is_content_addressed_and_companion_tamper_fails_closed(
        self,
    ) -> None:
        archive = self.runtime_archive()
        asset = {
            "filename": "fixture.tar.gz",
            "format": "tar.gz",
            "size_bytes": len(archive),
            "sha256": hashlib.sha256(archive).hexdigest(),
        }
        runtime = ManagedWhisperCppRuntime(self.data)

        with (
            patch(
                "app.managed_speech.whispercpp_platform_key",
                return_value=("linux", "x86_64"),
            ),
            patch.dict(
                WHISPERCPP_ASSETS,
                {("linux", "x86_64"): asset},
                clear=False,
            ),
            patch(
                "app.managed_speech._download_pinned",
                return_value=archive,
            ),
            patch(
                "app.managed_speech._verify_whisper_cli",
                return_value="whisper.cpp 1.9.4",
            ),
        ):
            path, identity = runtime.install()
            self.assertTrue(path.is_file())
            self.assertEqual(path.name, "whisper-cli")
            self.assertEqual(path.parents[2].name, identity.sha256)
            self.assertEqual(identity.version, "1.9.4")
            self.assertEqual(
                runtime.resolve()[1].sha256,
                identity.sha256,
            )

            companion = path.parent / "libwhisper.so"
            self.assertTrue(companion.is_file())
            companion.write_bytes(b"tampered-lib")
            self.assertIsNone(runtime._managed_candidate())

    def test_runtime_archive_rejects_path_traversal_before_writing(self) -> None:
        output = BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            payload = b"escape"
            info = tarfile.TarInfo("../escape")
            info.size = len(payload)
            archive.addfile(info, BytesIO(payload))

        destination = self.data / "runtime"
        destination.mkdir()
        with self.assertRaisesRegex(
            ManagedSpeechComponentError,
            "unsupported path",
        ):
            _extract_runtime_archive(
                output.getvalue(),
                "tar.gz",
                destination,
            )
        self.assertFalse((self.data / "escape").exists())

    def test_runtime_status_does_not_offer_managed_install_without_pinned_asset(
        self,
    ) -> None:
        runtime = ManagedWhisperCppRuntime(self.data)
        with (
            patch.object(runtime, "_override", return_value=""),
            patch.object(runtime, "_managed_candidate", return_value=None),
            patch.object(runtime, "_system_candidate", return_value=""),
            patch(
                "app.managed_speech.whispercpp_platform_key",
                return_value=("darwin", "arm64"),
            ),
        ):
            status = runtime.status()
        self.assertEqual(status.state, "unsupported")
        self.assertFalse(status.can_install)
        self.assertIn("custom or system", status.detail)

    def test_model_install_is_separate_exact_and_tamper_detected(self) -> None:
        payload = b"fixture-model-payload"
        entry = {
            "filename": "fixture.bin",
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "multilingual": True,
            "language_scope": "multilingual",
            "original_model": "fixture/original",
            "quantization": "q5_1",
        }
        with (
            patch.dict(
                WHISPER_MODELS,
                {"fixture": entry},
                clear=False,
            ),
            patch(
                "app.managed_speech._download_pinned",
                return_value=payload,
            ),
        ):
            model = ManagedWhisperModel(self.data, "fixture")
            path, identity = model.install()
            self.assertEqual(path.read_bytes(), payload)
            self.assertIn("whisper-models", path.parts)
            self.assertNotIn("whispercpp", path.parts)
            self.assertEqual(model.resolve()[1], identity)

            path.write_bytes(b"x" * len(payload))
            self.assertFalse(model.status().available)
            with self.assertRaisesRegex(
                ManagedSpeechComponentError,
                "integrity",
            ):
                model.resolve()

    def test_model_and_runtime_downloads_are_separately_bounded(self) -> None:
        class Response:
            def __init__(self, payload: bytes, final_url: str) -> None:
                self.payload = payload
                self.final_url = final_url
                self.headers = {"Content-Length": str(len(payload))}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return self.final_url

            def read(self, _limit):
                return self.payload

        payload = b"model"
        digest = hashlib.sha256(payload).hexdigest()
        with patch(
            "app.managed_speech.urllib.request.urlopen",
            return_value=Response(
                payload,
                "https://cas-bridge.xethub.hf.co/model",
            ),
        ):
            self.assertEqual(
                _download_pinned(
                    "https://huggingface.co/example/model",
                    expected_size=len(payload),
                    expected_sha256=digest,
                    maximum_bytes=100,
                    kind="model",
                ),
                payload,
            )

        with patch(
            "app.managed_speech.urllib.request.urlopen",
            return_value=Response(
                payload,
                "https://evil.example/model",
            ),
        ):
            with self.assertRaisesRegex(
                ManagedSpeechComponentError,
                "redirected",
            ):
                _download_pinned(
                    "https://huggingface.co/example/model",
                    expected_size=len(payload),
                    expected_sha256=digest,
                    maximum_bytes=100,
                    kind="model",
                )

    def test_remove_never_claims_unmanaged_sibling_data(self) -> None:
        payload = b"fixture-model"
        entry = {
            "filename": "fixture.bin",
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "multilingual": True,
            "language_scope": "multilingual",
            "original_model": "fixture/original",
            "quantization": "q5_1",
        }
        sibling = self.data / "keep-me"
        sibling.write_text("safe", encoding="utf-8")
        with (
            patch.dict(WHISPER_MODELS, {"fixture": entry}, clear=False),
            patch(
                "app.managed_speech._download_pinned",
                return_value=payload,
            ),
        ):
            model = ManagedWhisperModel(self.data, "fixture")
            model.install()
            model.remove()
        self.assertEqual(sibling.read_text(encoding="utf-8"), "safe")


if __name__ == "__main__":
    unittest.main()

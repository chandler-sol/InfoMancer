from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.managed_speech import (
    ManagedSpeechComponentError,
    ManagedSpeechLayout,
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
            / "fixture-v1",
        )
        self.assertEqual(
            self.layout.model_directory(model),
            self.layout.data_directory
            / "components"
            / "whisper-models"
            / "ggml-base.en"
            / model.sha256,
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


if __name__ == "__main__":
    unittest.main()

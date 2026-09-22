from __future__ import annotations

import gzip
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.managed_ffmpeg import (
    FFMPEG_ASSETS,
    FFMPEG_VERSION,
    ManagedFfmpegComponent,
    ManagedFfmpegError,
    managed_ffmpeg_binary,
)
from app.media_info import ffmpeg_executable


class ManagedFfmpegTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_status_prefers_override_then_bundle_then_managed_then_system(self):
        component = ManagedFfmpegComponent(self.data)

        with (
            patch("app.managed_ffmpeg._override_candidate", return_value="custom-ffmpeg"),
            patch("app.managed_ffmpeg.shutil.which", return_value="/tools/custom-ffmpeg"),
        ):
            status = component.status()
        self.assertEqual(status.state, "override")
        self.assertTrue(status.available)

        with (
            patch("app.managed_ffmpeg._override_candidate", return_value=""),
            patch(
                "app.managed_ffmpeg._bundled_candidate",
                return_value=self.data / "bundle" / "ffmpeg",
            ),
        ):
            status = component.status()
        self.assertEqual(status.state, "bundled")
        self.assertTrue(status.available)

        managed = self.data / "components" / "ffmpeg" / FFMPEG_VERSION / (
            "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        )
        managed.parent.mkdir(parents=True)
        managed.write_bytes(b"managed")
        if os.name != "nt":
            managed.chmod(0o755)
        with (
            patch("app.managed_ffmpeg._override_candidate", return_value=""),
            patch("app.managed_ffmpeg._bundled_candidate", return_value=None),
            patch("app.managed_ffmpeg.shutil.which", return_value=None),
        ):
            status = component.status()
        self.assertEqual(status.state, "managed")
        self.assertTrue(status.can_remove)

        managed.unlink()
        with (
            patch("app.managed_ffmpeg._override_candidate", return_value=""),
            patch("app.managed_ffmpeg._bundled_candidate", return_value=None),
            patch("app.managed_ffmpeg.shutil.which", return_value="/usr/bin/ffmpeg"),
        ):
            status = component.status()
        self.assertEqual(status.state, "system")
        self.assertFalse(status.can_install)

    def test_unavailable_supported_platform_offers_managed_install(self):
        component = ManagedFfmpegComponent(self.data)
        with (
            patch("app.managed_ffmpeg._override_candidate", return_value=""),
            patch("app.managed_ffmpeg._bundled_candidate", return_value=None),
            patch("app.managed_ffmpeg.shutil.which", return_value=None),
            patch(
                "app.managed_ffmpeg.ffmpeg_platform_key",
                return_value=("linux", "x86_64"),
            ),
        ):
            status = component.status()
        self.assertEqual(status.state, "unavailable")
        self.assertTrue(status.can_install)
        self.assertFalse(status.can_remove)

    def test_install_verifies_all_payloads_and_activates_private_copy(self):
        binary = b"fixture-ffmpeg-binary"
        archive = gzip.compress(binary)
        license_bytes = b"fixture-license"
        asset = {
            "slug": "fixture",
            "archive_sha256": hashlib.sha256(archive).hexdigest(),
            "binary_sha256": hashlib.sha256(binary).hexdigest(),
            "license_sha256": hashlib.sha256(license_bytes).hexdigest(),
        }
        component = ManagedFfmpegComponent(self.data)

        with (
            patch("app.managed_ffmpeg._override_candidate", return_value=""),
            patch("app.managed_ffmpeg._bundled_candidate", return_value=None),
            patch("app.managed_ffmpeg.shutil.which", return_value=None),
            patch(
                "app.managed_ffmpeg.ffmpeg_platform_key",
                return_value=("linux", "x86_64"),
            ),
            patch.dict(
                FFMPEG_ASSETS,
                {("linux", "x86_64"): asset},
                clear=False,
            ),
            patch(
                "app.managed_ffmpeg._download",
                side_effect=[archive, license_bytes],
            ) as download,
            patch("app.managed_ffmpeg._verify_executable") as verify,
        ):
            installed = component.install()

        self.assertEqual(installed.read_bytes(), binary)
        self.assertEqual(download.call_count, 2)
        verify.assert_called_once()
        self.assertTrue((installed.parent / "FFMPEG_LICENSE.txt").is_file())
        self.assertTrue((installed.parent / "FFMPEG_NOTICE.txt").is_file())
        self.assertTrue((installed.parent / "component.json").is_file())

    def test_install_rejects_hash_mismatch_before_activation(self):
        binary = b"fixture-ffmpeg-binary"
        archive = gzip.compress(binary)
        license_bytes = b"fixture-license"
        asset = {
            "slug": "fixture",
            "archive_sha256": "0" * 64,
            "binary_sha256": hashlib.sha256(binary).hexdigest(),
            "license_sha256": hashlib.sha256(license_bytes).hexdigest(),
        }
        component = ManagedFfmpegComponent(self.data)

        with (
            patch("app.managed_ffmpeg._override_candidate", return_value=""),
            patch("app.managed_ffmpeg._bundled_candidate", return_value=None),
            patch("app.managed_ffmpeg.shutil.which", return_value=None),
            patch(
                "app.managed_ffmpeg.ffmpeg_platform_key",
                return_value=("linux", "x86_64"),
            ),
            patch.dict(
                FFMPEG_ASSETS,
                {("linux", "x86_64"): asset},
                clear=False,
            ),
            patch(
                "app.managed_ffmpeg._download",
                side_effect=[archive, license_bytes],
            ),
        ):
            with self.assertRaisesRegex(ManagedFfmpegError, "integrity"):
                component.install()

        self.assertFalse(
            (self.data / "components" / "ffmpeg" / FFMPEG_VERSION).exists()
        )

    def test_remove_only_deletes_infomancer_managed_version_directory(self):
        component = ManagedFfmpegComponent(self.data)
        directory = self.data / "components" / "ffmpeg" / FFMPEG_VERSION
        directory.mkdir(parents=True)
        (directory / "fixture").write_text("managed", encoding="utf-8")
        sibling = self.data / "keep-me"
        sibling.write_text("safe", encoding="utf-8")

        component.remove()

        self.assertFalse(directory.exists())
        self.assertEqual(sibling.read_text(encoding="utf-8"), "safe")

    def test_runtime_resolver_uses_managed_copy_before_system_path(self):
        managed = self.data / "managed" / (
            "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        )
        managed.parent.mkdir()
        managed.write_bytes(b"fixture")

        with (
            patch.dict(os.environ, {"INFOMANCER_FFMPEG": ""}, clear=False),
            patch("app.media_info.managed_ffmpeg_candidate", return_value=managed),
            patch("app.media_info.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch.object(
                __import__("sys"),
                "_MEIPASS",
                "",
                create=True,
            ),
        ):
            self.assertEqual(ffmpeg_executable(), str(managed))


if __name__ == "__main__":
    unittest.main()

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.media_info import ffprobe_executable
from scripts.stage_ffprobe import (
    ASSETS,
    BTBN_COMMIT,
    BTBN_RELEASE,
    FFMPEG_COMMIT,
    LICENSE_GIT_BLOB_SHA1,
    LICENSE_URL,
)


ROOT = Path(__file__).resolve().parents[1]


class FFprobePackagingTests(unittest.TestCase):
    def test_resolver_prefers_explicit_override(self):
        with patch.dict(
            os.environ, {"INFOMANCER_FFPROBE": "custom-ffprobe"}, clear=False
        ):
            self.assertEqual(ffprobe_executable(), "custom-ffprobe")

    def test_resolver_finds_pyinstaller_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
            candidate.write_bytes(b"stub")
            with patch.dict(
                os.environ, {"INFOMANCER_FFPROBE": ""}, clear=False
            ), patch.object(sys, "_MEIPASS", tmp, create=True), patch(
                "app.media_info.shutil.which", return_value=None
            ):
                self.assertEqual(ffprobe_executable(), str(candidate))

    def test_pinned_assets_are_reviewed_lgpl_builds(self):
        expected = {
            ("windows", "x86_64"),
            ("windows", "arm64"),
            ("linux", "x86_64"),
            ("linux", "arm64"),
        }
        self.assertEqual(set(ASSETS), expected)
        sha256 = re.compile(r"^[0-9a-f]{64}$")
        for target, asset in ASSETS.items():
            with self.subTest(target=target):
                self.assertIn("-lgpl-", asset["filename"])
                self.assertNotIn("-gpl-", asset["filename"])
                self.assertRegex(asset["archive_sha256"], sha256)
                self.assertIn(asset["format"], {"zip", "tar.xz"})

    def test_unreviewed_macos_binary_is_not_silently_bundled(self):
        self.assertNotIn(("darwin", "x86_64"), ASSETS)
        self.assertNotIn(("darwin", "arm64"), ASSETS)

    def test_ffprobe_provenance_is_immutable(self):
        self.assertEqual(
            FFMPEG_COMMIT, "ad500d59cb6e0126add4fcb95afb4e2557c4292c"
        )
        self.assertEqual(
            BTBN_COMMIT, "cc8f0958be119db774cdaf6c50065651a4901e72"
        )
        self.assertEqual(BTBN_RELEASE, "autobuild-2026-09-11-13-20")
        self.assertTrue(LICENSE_URL.endswith("/COPYING.LGPLv3"))
        self.assertRegex(LICENSE_GIT_BLOB_SHA1, r"^[0-9a-f]{40}$")

    def test_native_packaging_enforces_lgpl_configuration(self):
        stage = (ROOT / "scripts" / "stage_ffprobe.py").read_text(encoding="utf-8")
        for required in (
            "FFPROBE_LICENSE.txt",
            "FFPROBE_NOTICE.txt",
            "FFPROBE_BUILDINFO.txt",
            "--enable-gpl",
            "--enable-nonfree",
            "--enable-version3",
            "COPYING.LGPLv3",
            '_require_hash("FFprobe archive"',
            "LICENSE_GIT_BLOB_SHA1",
        ):
            self.assertIn(required, stage)

        sidecar = (ROOT / "desktop" / "sidecar.py").read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--check-ffprobe"', sidecar)
        self.assertIn('[ffprobe_executable(), "-version"]', sidecar)

        for relative in (
            ".github/workflows/windows-desktop.yml",
            ".github/workflows/windows-desktop-release.yml",
        ):
            workflow = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(workflow=relative):
                self.assertIn("scripts/stage_ffprobe.py", workflow)
                self.assertIn("FFPROBE_BUILDINFO.txt", workflow)
                self.assertIn("--add-binary", workflow)
                self.assertIn("--check-ffprobe", workflow)

    def test_release_publishes_corresponding_ffmpeg_source_and_build_provenance(self):
        workflow = (
            ROOT / ".github/workflows/windows-desktop-release.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(FFMPEG_COMMIT, workflow)
        self.assertIn(BTBN_COMMIT, workflow)
        self.assertIn("ffmpeg-source-", workflow)
        self.assertIn("ffmpeg-build-scripts-", workflow)
        self.assertIn("gh release upload", workflow)


if __name__ == "__main__":
    unittest.main()

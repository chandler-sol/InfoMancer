import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.media_info import ffmpeg_executable, ffprobe_executable
from scripts.stage_ffmpeg import ASSETS as FFMPEG_ASSETS
from scripts.stage_ffprobe import ASSETS


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

    def test_ffmpeg_resolver_prefers_explicit_override(self):
        with patch.dict(
            os.environ, {"INFOMANCER_FFMPEG": "custom-ffmpeg"}, clear=False
        ):
            self.assertEqual(ffmpeg_executable(), "custom-ffmpeg")

    def test_ffmpeg_resolver_finds_pyinstaller_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            candidate.write_bytes(b"stub")
            with patch.dict(
                os.environ, {"INFOMANCER_FFMPEG": ""}, clear=False
            ), patch.object(sys, "_MEIPASS", tmp, create=True), patch(
                "app.media_info.shutil.which", return_value=None
            ):
                self.assertEqual(ffmpeg_executable(), str(candidate))

    def test_pinned_assets_cover_native_desktop_targets(self):
        expected = {
            ("windows", "x86_64"),
            ("linux", "x86_64"),
            ("linux", "arm64"),
            ("darwin", "x86_64"),
            ("darwin", "arm64"),
        }
        self.assertEqual(set(ASSETS), expected)
        self.assertEqual(set(FFMPEG_ASSETS), expected)
        sha256 = re.compile(r"^[0-9a-f]{64}$")
        for tool_name, assets in (
            ("ffprobe", ASSETS),
            ("ffmpeg", FFMPEG_ASSETS),
        ):
            for target, asset in assets.items():
                with self.subTest(tool=tool_name, target=target):
                    self.assertTrue(asset["slug"])
                    self.assertRegex(asset["archive_sha256"], sha256)
                    self.assertRegex(asset["binary_sha256"], sha256)
                    self.assertRegex(asset["license_sha256"], sha256)

    def test_native_packaging_runs_packaged_ffmpeg_self_check(self):
        stage = (ROOT / "scripts" / "stage_ffmpeg.py").read_text(encoding="utf-8")
        self.assertIn("FFMPEG_LICENSE.txt", stage)
        self.assertIn("FFMPEG_NOTICE.txt", stage)
        self.assertIn('_require_hash("FFmpeg archive"', stage)
        self.assertIn('_require_hash("FFmpeg binary"', stage)
        self.assertIn('_require_hash("FFmpeg license"', stage)

        sidecar = (ROOT / "desktop" / "sidecar.py").read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--check-ffmpeg"', sidecar)
        self.assertIn('[ffmpeg_executable(), "-version"]', sidecar)

        for relative in (
            ".github/workflows/draft-08-release.yml",
            ".github/workflows/windows-desktop.yml",
            ".github/workflows/windows-desktop-release.yml",
            ".github/workflows/promote-update-channel.yml",
        ):
            workflow = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(workflow=relative):
                self.assertIn("scripts/stage_ffmpeg.py", workflow)
                self.assertIn("FFMPEG_LICENSE.txt", workflow)
                self.assertIn("--check-ffmpeg", workflow)

    def test_native_packaging_runs_packaged_ffprobe_self_check(self):
        stage = (ROOT / "scripts" / "stage_ffprobe.py").read_text(encoding="utf-8")
        self.assertIn("FFPROBE_LICENSE.txt", stage)
        self.assertIn("FFPROBE_NOTICE.txt", stage)
        self.assertIn('_require_hash("FFprobe archive"', stage)
        self.assertIn('_require_hash("FFprobe binary"', stage)
        self.assertIn('_require_hash("FFprobe license"', stage)

        sidecar = (ROOT / "desktop" / "sidecar.py").read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--check-ffprobe"', sidecar)
        self.assertIn('[ffprobe_executable(), "-version"]', sidecar)

        for relative in (
            ".github/workflows/draft-08-release.yml",
            ".github/workflows/windows-desktop.yml",
            ".github/workflows/windows-desktop-release.yml",
        ):
            workflow = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(workflow=relative):
                self.assertIn("scripts/stage_ffprobe.py", workflow)
                self.assertIn("--add-binary", workflow)
                self.assertIn("--check-ffprobe", workflow)


if __name__ == "__main__":
    unittest.main()

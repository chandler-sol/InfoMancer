import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.media_info import ffprobe_executable
from scripts.stage_ffprobe import (
    FFMPEG_COMMIT,
    FORBIDDEN_CONFIGURE_FLAGS,
    LICENSE_GIT_BLOB_SHA1,
    REQUIRED_CONFIGURE_FLAGS,
    SOURCE_ARCHIVE,
    FFprobeComplianceError,
    _verify_configure_args,
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

    def test_ffprobe_source_identity_is_pinned(self):
        self.assertEqual(
            FFMPEG_COMMIT, "ad500d59cb6e0126add4fcb95afb4e2557c4292c"
        )
        self.assertEqual(SOURCE_ARCHIVE, f"ffmpeg-source-{FFMPEG_COMMIT}.tar.gz")
        self.assertEqual(
            LICENSE_GIT_BLOB_SHA1, "40924c2a6da76a2b0c639f6fe7ef0b2d095a6adb"
        )

    def test_minimal_build_has_required_license_safety_flags(self):
        required = {
            "--disable-autodetect",
            "--disable-network",
            "--disable-ffmpeg",
            "--disable-ffplay",
            "--disable-encoders",
            "--disable-muxers",
            "--disable-filters",
            "--disable-devices",
            "--disable-hwaccels",
            "--disable-pthreads",
            "--enable-w32threads",
            "--enable-static",
            "--disable-shared",
        }
        self.assertTrue(required.issubset(REQUIRED_CONFIGURE_FLAGS))
        self.assertEqual(
            FORBIDDEN_CONFIGURE_FLAGS,
            {"--enable-gpl", "--enable-nonfree", "--enable-version3"},
        )

        build_script = (ROOT / "scripts" / "build_minimal_ffprobe.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(FFMPEG_COMMIT, build_script)
        self.assertIn("git -C \"$SOURCE_DIR\" archive", build_script)
        self.assertIn("COPYING.LGPLv2.1", build_script)
        self.assertNotIn("BtbN", build_script)
        for flag in required:
            self.assertIn(flag, build_script)

    def test_configure_verifier_rejects_optional_external_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "args.txt"
            path.write_text(
                "\n".join(sorted(REQUIRED_CONFIGURE_FLAGS | {"--enable-libopus"}))
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(FFprobeComplianceError):
                _verify_configure_args(path)

    def test_configure_verifier_rejects_gpl_nonfree_and_version3(self):
        for flag in FORBIDDEN_CONFIGURE_FLAGS:
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "args.txt"
                path.write_text(
                    "\n".join(sorted(REQUIRED_CONFIGURE_FLAGS | {flag})) + "\n",
                    encoding="utf-8",
                )
                with self.assertRaises(FFprobeComplianceError):
                    _verify_configure_args(path)

    def test_native_stager_is_offline_and_records_compliance_evidence(self):
        stage = (ROOT / "scripts" / "stage_ffprobe.py").read_text(encoding="utf-8")
        for required in (
            "FFPROBE_LICENSE.txt",
            "FFPROBE_NOTICE.txt",
            "FFPROBE_BUILDINFO.txt",
            "FFPROBE_BUILD_SCRIPT.sh",
            "FFPROBE_DLL_DEPENDENCIES.txt",
            "--enable-gpl",
            "--enable-nonfree",
            "--enable-version3",
            "--enable-lib",
            "--disable-autodetect",
            "LICENSE_GIT_BLOB_SHA1",
        ):
            self.assertIn(required, stage)
        self.assertNotIn("urllib.request", stage)
        self.assertNotIn("BtbN", stage)

        sidecar = (ROOT / "desktop" / "sidecar.py").read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--check-ffprobe"', sidecar)
        self.assertIn('[ffprobe_executable(), "-version"]', sidecar)

    def test_compliance_workflow_builds_source_then_executes_binary_on_windows(self):
        workflow = (ROOT / ".github/workflows/ffprobe-compliance.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("scripts/build_minimal_ffprobe.sh", workflow)
        self.assertIn("gcc-mingw-w64-x86-64", workflow)
        self.assertIn("minimal-ffprobe-win64", workflow)
        self.assertIn("scripts/stage_ffprobe.py", workflow)
        self.assertIn("windows-latest", workflow)
        self.assertIn("ubuntu-latest", workflow)

    def test_windows_release_uses_minimal_build_and_publishes_source(self):
        workflow = (
            ROOT / ".github/workflows/windows-desktop-release.yml"
        ).read_text(encoding="utf-8")
        for required in (
            "scripts/build_minimal_ffprobe.sh",
            "minimal-ffprobe-win64",
            "scripts/stage_ffprobe.py",
            "FFPROBE_BUILDINFO.txt",
            "FFPROBE_BUILD_SCRIPT.sh",
            "ffmpeg-source-",
            "gh release upload",
            "--check-ffprobe",
        ):
            self.assertIn(required, workflow)
        self.assertNotIn("BtbN/FFmpeg-Builds", workflow)


if __name__ == "__main__":
    unittest.main()

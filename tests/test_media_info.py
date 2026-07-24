import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.media_info import MediaInspectionError, inspect_media


class MediaInfoTests(unittest.TestCase):
    def test_ffprobe_metadata_is_normalized(self):
        with tempfile.NamedTemporaryFile(suffix=".mkv") as media:
            response = type("Result", (), {
                "returncode": 0,
                "stderr": "",
                "stdout": json.dumps({
                    "format": {
                        "duration": "3661.5", "bit_rate": "12000000",
                        "format_name": "matroska,webm",
                    },
                    "streams": [
                        {
                            "codec_type": "video", "codec_name": "hevc",
                            "width": 3840, "height": 2160,
                            "color_transfer": "smpte2084",
                            "color_primaries": "bt2020",
                        },
                        {
                            "codec_type": "audio", "codec_name": "eac3",
                            "channels": 6,
                        },
                    ],
                }),
            })()
            with patch("app.media_info.subprocess.run", return_value=response):
                result = inspect_media(Path(media.name))
        self.assertEqual(result["runtime_seconds"], 3661.5)
        self.assertEqual((result["width"], result["height"]), (3840, 2160))
        self.assertEqual(result["video_codec"], "HEVC")
        self.assertEqual(result["audio_codec"], "EAC3")
        self.assertEqual(result["dynamic_range"], "HDR10")
        self.assertEqual(result["container"], "MATROSKA")

    def test_missing_file_has_plain_language_error(self):
        with self.assertRaisesRegex(MediaInspectionError, "no longer available"):
            inspect_media(Path("definitely-not-a-real-media-file.mkv"))

    def test_broken_mkv_header_explains_likely_cause_and_next_steps(self):
        with tempfile.NamedTemporaryFile(suffix=".mkv") as media:
            response = type("Result", (), {
                "returncode": 1,
                "stderr": (
                    "[matroska,webm] EBML header parsing failed\n"
                    "Invalid data found when processing input"
                ),
                "stdout": "",
            })()
            with patch("app.media_info.subprocess.run", return_value=response):
                with self.assertRaises(MediaInspectionError) as raised:
                    inspect_media(Path(media.name))
        error = raised.exception
        self.assertEqual(error.headline, "This MKV appears incomplete or damaged")
        self.assertIn("incomplete or damaged copy", error.user_message)
        self.assertIn("replace or recopy it", error.user_message)
        self.assertIn("FFprobe output", error.log_detail)

    def test_permission_failure_explains_which_permissions_to_check(self):
        with tempfile.NamedTemporaryFile(suffix=".mkv") as media:
            response = type("Result", (), {
                "returncode": 1,
                "stderr": "Permission denied",
                "stdout": "",
            })()
            with patch("app.media_info.subprocess.run", return_value=response):
                with self.assertRaises(MediaInspectionError) as raised:
                    inspect_media(Path(media.name))
        self.assertEqual(raised.exception.headline, "InfoMancer cannot read this file")
        self.assertIn("account or container", raised.exception.user_message)


if __name__ == "__main__":
    unittest.main()

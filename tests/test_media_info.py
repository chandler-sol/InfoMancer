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
                            "index": 0,
                            "codec_type": "video", "codec_name": "hevc",
                            "width": 3840, "height": 2160,
                            "color_transfer": "smpte2084",
                            "color_primaries": "bt2020",
                            "disposition": {"default": 1},
                        },
                        {
                            "index": 1,
                            "codec_type": "audio", "codec_name": "eac3",
                            "channels": 6, "channel_layout": "5.1(side)",
                            "sample_rate": "48000",
                            "tags": {"language": "eng", "title": "Main Audio"},
                            "disposition": {"default": 1, "comment": 0},
                        },
                        {
                            "index": 2,
                            "codec_type": "audio", "codec_name": "aac",
                            "channels": 2,
                            "tags": {"language": "eng", "title": "Director Commentary"},
                            "disposition": {"default": 0, "comment": 0},
                        },
                        {
                            "index": 3,
                            "codec_type": "subtitle", "codec_name": "subrip",
                            "tags": {"language": "spa", "title": "Forced Spanish"},
                            "disposition": {"forced": 1, "hearing_impaired": 1},
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
        self.assertEqual(result["audio_channels"], 6)
        self.assertEqual(result["dynamic_range"], "HDR10")
        self.assertEqual(result["container"], "MATROSKA")
        self.assertEqual(len(result["streams"]), 4)
        self.assertEqual(result["streams"][1]["language"], "eng")
        self.assertEqual(result["streams"][1]["channel_layout"], "5.1(side)")
        self.assertEqual(result["streams"][1]["sample_rate"], 48000)
        self.assertTrue(result["streams"][2]["commentary"])
        self.assertEqual(result["streams"][3]["type"], "subtitle")
        self.assertEqual(result["streams"][3]["language"], "spa")
        self.assertTrue(result["streams"][3]["forced"])
        self.assertTrue(result["streams"][3]["hearing_impaired"])

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

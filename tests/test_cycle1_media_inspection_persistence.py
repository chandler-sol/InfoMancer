from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from app.db import Database
from app.routes.context import RouteContext
from app.routes.cycle1_intelligence_foundation import build_router


class Cycle1MediaInspectionPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO roots(id,path,kind,label) VALUES (1,'/media','movie','Movies')"
            )
            conn.execute(
                """INSERT INTO titles(id,root_id,kind,title,folder_path)
                   VALUES (1,1,'movie','Example','/media/Example')"""
            )
            conn.execute(
                """INSERT INTO files(
                     id,title_id,path,filename,extension,size_bytes,modified_at,seen_scan,
                     video_codec,audio_codec,audio_channels
                   ) VALUES (
                     1,1,'/media/Example/movie.mkv','movie.mkv','.mkv',1000,123.5,
                     'scan-1','OLDVIDEO','OLDAUDIO',2
                   )"""
            )
        self.events: list[dict] = []
        self.job: dict = {}
        self.namespace = {
            "db": self.database,
            "inspect_media": self.good_inspection,
            "media_info_job": self.job,
            "media_info_lock": threading.Lock(),
            "record_event": self.record_event,
        }
        _router, handlers = build_router(RouteContext(self.namespace))
        self.run_media_inspection = handlers["run_media_inspection"]
        self.media_streams = handlers["media_streams"]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def record_event(
        self, category: str, message: str, *, level: str = "info",
        detail: str = "", context: dict | None = None, user_id=None,
    ) -> None:
        self.events.append({
            "category": category,
            "message": message,
            "level": level,
            "detail": detail,
            "context": context or {},
        })

    @staticmethod
    def good_inspection(_path: Path) -> dict:
        return {
            "runtime_seconds": 7200.0,
            "width": 3840,
            "height": 2160,
            "video_codec": "HEVC",
            "audio_codec": "EAC3",
            "audio_channels": 6,
            "bitrate": 18_000_000,
            "container": "MATROSKA",
            "dynamic_range": "HDR10",
            "streams": [
                {
                    "index": 0, "type": "video", "codec": "HEVC",
                    "language": "und", "default": True,
                },
                {
                    "index": 1, "type": "audio", "codec": "EAC3",
                    "language": "eng", "channels": 6,
                    "channel_layout": "5.1", "sample_rate": 48000,
                    "title": "Main", "default": True,
                },
                {
                    "index": 2, "type": "subtitle", "codec": "SUBRIP",
                    "language": "eng", "forced": True,
                },
            ],
        }

    def test_worker_commits_legacy_summary_and_streams_together(self) -> None:
        self.run_media_inspection([1])

        with self.database.connect() as conn:
            file_row = conn.execute(
                """SELECT video_codec,audio_codec,audio_channels,width,height,
                          media_info_at,media_info_error
                   FROM files WHERE id=1"""
            ).fetchone()
        streams = self.media_streams.file_streams(1)

        self.assertEqual(file_row["video_codec"], "HEVC")
        self.assertEqual(file_row["audio_codec"], "EAC3")
        self.assertEqual(file_row["audio_channels"], 6)
        self.assertEqual((file_row["width"], file_row["height"]), (3840, 2160))
        self.assertTrue(file_row["media_info_at"])
        self.assertIsNone(file_row["media_info_error"])
        self.assertEqual(
            [(row["stream_index"], row["stream_type"], row["language"]) for row in streams],
            [(0, "video", "und"), (1, "audio", "eng"), (2, "subtitle", "eng")],
        )
        self.assertEqual(self.job["status"], "complete")
        self.assertEqual(self.job["updated"], 1)
        self.assertEqual(self.job["errors"], 0)

        collected = next(
            event for event in self.events
            if event["message"] == "Media details collected for movie.mkv."
        )
        self.assertEqual(collected["context"]["stream_count"], 3)
        self.assertNotIn("streams", collected["context"])

    def test_stream_failure_rolls_back_summary_and_previous_inventory(self) -> None:
        self.run_media_inspection([1])
        original_streams = self.media_streams.file_streams(1)

        def invalid_inspection(_path: Path) -> dict:
            values = dict(self.good_inspection(_path))
            values["video_codec"] = "SHOULD_NOT_COMMIT"
            values["streams"] = [
                {"index": 9, "type": "audio", "codec": "AAC"},
                {"index": 9, "type": "subtitle", "codec": "SRT"},
            ]
            return values

        # The route bundle intentionally uses a LiveRef, so runtime/test replacement
        # remains visible after composition just like the existing route architecture.
        self.namespace["inspect_media"] = invalid_inspection
        self.events.clear()
        self.run_media_inspection([1])

        with self.database.connect() as conn:
            file_row = conn.execute(
                "SELECT video_codec,audio_codec,audio_channels FROM files WHERE id=1"
            ).fetchone()
        after_streams = self.media_streams.file_streams(1)

        self.assertEqual(file_row["video_codec"], "HEVC")
        self.assertEqual(file_row["audio_codec"], "EAC3")
        self.assertEqual(file_row["audio_channels"], 6)
        self.assertEqual(
            [(row["stream_index"], row["stream_type"], row["codec"]) for row in after_streams],
            [(row["stream_index"], row["stream_type"], row["codec"]) for row in original_streams],
        )
        self.assertEqual(self.job["status"], "complete")
        self.assertEqual(self.job["updated"], 0)
        self.assertEqual(self.job["errors"], 1)
        persistence_error = next(
            event for event in self.events
            if event["context"].get("operation") == "media-stream-persistence"
        )
        self.assertEqual(persistence_error["level"], "error")


if __name__ == "__main__":
    unittest.main()

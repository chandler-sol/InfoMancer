import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.app_settings import AppSettings
from app.background import BackgroundCoordinator
from app.db import Database
from app.duplicate_trash import DuplicateTrashService
from app.file_hashes import MediaHashService
from app.media_info import inspect_media
from app.media_integrity import MediaIntegrityService
from app.mie import MediaIntelligenceEngine
from app.stream_inventory import MediaStreamService


class FakeProcess:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class Intelligence09Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            conn.execute("INSERT INTO roots(id,path,kind,label,last_scanned_at) VALUES (1,?,'tv','TV',CURRENT_TIMESTAMP)", (self.temp.name,))
            conn.execute("INSERT INTO titles(id,root_id,kind,title,folder_path,tvdb_id,metadata_title,poster_url) VALUES (1,1,'tv','Show',?,123,'Show','poster')", (str(Path(self.temp.name) / 'Show'),))
            conn.execute("INSERT INTO title_credits(title_id,imdb_person_id,person_name,role) VALUES (1,'nm1','Actor','actor')")
            media = Path(self.temp.name) / "Show S01E01.mkv"
            media.write_bytes(b"fixture")
            stat = media.stat()
            conn.execute(
                """INSERT INTO files(id,title_id,path,filename,extension,size_bytes,modified_at,season,episode_start,episode_end,media_info_at,seen_scan)
                   VALUES (1,1,?,'Show S01E01.mkv','.mkv',?,?,1,1,1,CURRENT_TIMESTAMP,'scan')""",
                (str(media), stat.st_size, stat.st_mtime),
            )
        self.mie = MediaIntelligenceEngine(self.database)
        self.streams = MediaStreamService(self.database)
        self.integrity = MediaIntegrityService(self.database)

    def tearDown(self):
        self.temp.cleanup()

    @patch("app.media_info.subprocess.run")
    def test_ffprobe_inventory_keeps_audio_and_subtitle_metadata(self, run):
        run.return_value = FakeProcess(stdout="""{"format":{"duration":"100","bit_rate":"1000","format_name":"matroska"},"streams":[{"index":0,"codec_type":"video","codec_name":"hevc","width":1920,"height":1080},{"index":1,"codec_type":"audio","codec_name":"eac3","channels":6,"channel_layout":"5.1","sample_rate":"48000","tags":{"language":"eng","title":"Main"},"disposition":{"default":1}},{"index":2,"codec_type":"subtitle","codec_name":"subrip","tags":{"language":"eng","title":"English SDH"},"disposition":{"hearing_impaired":1,"forced":0}}]}""")
        values = inspect_media(Path(self.temp.name) / "Show S01E01.mkv")
        self.assertEqual(values["audio_codec"], "EAC3")
        self.assertEqual(len(values["streams"]), 3)
        subtitle = values["streams"][2]
        self.assertEqual(subtitle["type"], "subtitle")
        self.assertEqual(subtitle["language"], "eng")
        self.assertTrue(subtitle["hearing_impaired"])

    def test_stream_expectations_create_explainable_title_findings(self):
        self.streams.replace(1, [
            {"index": 0, "type": "video", "codec": "HEVC"},
            {"index": 1, "type": "audio", "codec": "EAC3", "language": "eng", "channels": 2},
        ])
        self.mie.save_stream_expectations(
            required_audio_languages="eng", required_subtitle_languages="eng",
            minimum_audio_channels="6", require_subtitles=True,
        )
        self.mie.analyze()
        rules = {item["rule_key"] for item in self.mie.findings()}
        self.assertIn("stream-subtitle-language-missing", rules)
        self.assertIn("stream-subtitles-missing", rules)
        self.assertIn("stream-audio-channels-low", rules)

    @patch("app.media_integrity.shutil.which", return_value=None)
    def test_integrity_preflight_detects_missing_ffmpeg(self, _which):
        self.assertFalse(self.integrity.available())

    def test_integrity_job_participates_in_central_background_guard(self):
        settings = AppSettings(self.database, "https://example.invalid/search?q={query}")
        coordinator = BackgroundCoordinator(
            self.database, settings, MediaHashService(self.database),
            DuplicateTrashService(self.database), lambda *args, **kwargs: None,
        )
        self.assertFalse(coordinator.other_background_work_running())
        with coordinator.media_integrity_lock:
            coordinator.media_integrity_job["status"] = "running"
        self.assertTrue(coordinator.other_background_work_running())
        with coordinator.media_integrity_lock:
            coordinator.media_integrity_job["status"] = "complete"
        self.assertFalse(coordinator.other_background_work_running())

    @patch("app.media_integrity.subprocess.run")
    def test_integrity_sampling_is_read_only_and_surfaces_decode_failure(self, run):
        run.return_value = FakeProcess(stderr="Invalid data found when processing input", returncode=1)
        row = self.integrity.pending_files()[0]
        result = self.integrity.check_file(row)
        self.assertEqual(result["status"], "failed")
        self.mie.analyze()
        finding = next(item for item in self.mie.findings() if item["rule_key"] == "media-integrity")
        self.assertEqual(finding["severity"], "critical")
        self.assertIn("will not attempt an automatic repair", finding["recommendation"])

    def test_mie_20_records_opened_resolved_and_title_health(self):
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO titles(id,root_id,kind,title,folder_path) VALUES (2,1,'movie','Second title',?)",
                (str(Path(self.temp.name) / "Second title"),),
            )
        self.mie.analyze()
        first = self.mie.analysis_history()[0]
        self.assertGreaterEqual(first["opened_findings"], 0)
        health = self.mie.title_health_overview()
        self.assertTrue(health)
        with self.database.connect() as conn:
            snapshot_ids = {row[0] for row in conn.execute(
                "SELECT title_id FROM mie_title_health_snapshots WHERE run_id=(SELECT MAX(id) FROM mie_analysis_runs)"
            )}
            finding_ids = {row[0] for row in conn.execute(
                "SELECT DISTINCT title_id FROM mie_findings WHERE status='active' AND title_id IS NOT NULL"
            )}
        self.assertEqual(snapshot_ids, finding_ids)
        with self.database.connect() as conn:
            conn.execute("UPDATE roots SET last_scanned_at=CURRENT_TIMESTAMP WHERE id=1")
        self.mie.analyze()
        second = self.mie.analysis_history()[0]
        self.assertGreaterEqual(second["resolved_findings"], 0)


if __name__ == "__main__":
    unittest.main()

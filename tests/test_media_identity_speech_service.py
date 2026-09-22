from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.media_identity.models import MediaIdentityFile
from app.media_identity.speech import (
    SpeechAudioIdentity,
    SpeechBinaryIdentity,
    SpeechModelIdentity,
    SpeechTranscript,
)
from app.media_identity.speech_service import (
    NormalSpeechService,
    NormalSpeechStaleError,
    plan_normal_speech_windows,
)
from app.media_identity.speech_audio import SpeechAudioStream


class FakePreparedAudio:
    def __init__(self, identity: SpeechAudioIdentity):
        self.identity = identity
        self.cleanup_calls = 0

    def validated_path(self, expected_identity=None):
        if expected_identity is not None and expected_identity != self.identity:
            raise RuntimeError("identity mismatch")
        return "/tmp/infomancer-fake-speech.wav"

    def cleanup(self):
        self.cleanup_calls += 1


class FakeExtractor:
    instances = []

    def __init__(self, media, streams, *, preferred_language=""):
        self.media = media
        self.streams = tuple(streams)
        self.preferred_language = preferred_language
        self.stream = SpeechAudioStream(
            index=1,
            language="eng",
            channels=2,
            sample_rate_hz=48_000,
            default=True,
        )
        self.extract_calls = 0
        self.prepared = []
        type(self).instances.append(self)

    def source_signature(self, window):
        return hashlib.sha256(
            f"{self.media.file_id}:{window.start_ms}:{window.end_ms}:stream-1".encode()
        ).hexdigest()

    def extract(self, window):
        self.extract_calls += 1
        source_signature = self.source_signature(window)
        payload = f"wav:{window.start_ms}:{window.end_ms}".encode()
        identity = SpeechAudioIdentity(
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            format_key="wav-pcm-s16le",
            sample_rate_hz=16_000,
            channels=1,
            source_signature=source_signature,
            details={
                "window": {
                    "start_ms": window.start_ms,
                    "end_ms": window.end_ms,
                }
            },
        )
        prepared = FakePreparedAudio(identity)
        self.prepared.append(prepared)
        return prepared


class FakeSpeechEngine:
    key = "fake-speech"
    version = "1"

    def __init__(
        self,
        *,
        available=True,
        interrupt_on_call=None,
        mutate=None,
        change_identity_after_transcribe=False,
    ):
        self.is_available = available
        self.interrupt_on_call = interrupt_on_call
        self.mutate = mutate
        self.change_identity_after_transcribe = change_identity_after_transcribe
        self.identity_revision = 0
        self.calls = 0

    def available(self):
        return self.is_available

    def binary_identity(self):
        return SpeechBinaryIdentity(
            key="fake-speech",
            version="1",
            sha256=("a" if self.identity_revision == 0 else "b") * 64,
            size_bytes=1234,
            source="fixture",
            details={
                "runtime_tree_sha256": (
                    "c" if self.identity_revision == 0 else "d"
                ) * 64
            },
        )

    def cache_identity(self):
        return {"fixture": "speech-v1", "cpu_only": True}

    def transcribe(self, _audio_path, request):
        self.calls += 1
        if self.interrupt_on_call == self.calls:
            raise KeyboardInterrupt()
        if self.mutate is not None:
            self.mutate()
        transcript = SpeechTranscript(
            text=f"dialogue {request.window.start_ms}-{request.window.end_ms}",
            language="en",
            details={"fixture": True},
        )
        if self.change_identity_after_transcribe:
            self.identity_revision += 1
        return transcript


class NormalSpeechServiceTests(unittest.TestCase):
    def setUp(self):
        FakeExtractor.instances.clear()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.media_root = self.root / "media"
        self.media_root.mkdir()
        self.media_path = self.media_root / "Example - S01E01.mkv"
        self.media_path.write_bytes(b"fixture-media" * 64)
        stat = self.media_path.stat()

        self.database = Database(self.root / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO roots(id,path,kind,label) VALUES (1,?,'tv','TV')",
                (str(self.media_root),),
            )
            conn.execute(
                """INSERT INTO titles(
                     id,root_id,kind,title,metadata_title,folder_path,tvdb_id
                   ) VALUES (1,1,'tv','Example','Example',?,4242)""",
                (str(self.media_root / "Example"),),
            )
            conn.execute(
                """INSERT INTO files(
                     id,title_id,path,filename,extension,size_bytes,modified_at,
                     season,episode_start,episode_end,runtime_seconds,seen_scan
                   ) VALUES (1,1,?,?,?,?,?,1,1,1,1800,'fixture')""",
                (
                    str(self.media_path),
                    self.media_path.name,
                    "mkv",
                    stat.st_size,
                    stat.st_mtime,
                ),
            )
            conn.execute(
                """INSERT INTO media_identity_scans(
                     id,file_id,identity_kind,requested_profile,completed_profile,
                     status,stage,claimed_identity_json,file_size_bytes,
                     file_modified_at,metadata_signature
                   ) VALUES (
                     1,1,'episode','normal','fast','complete','resolved',
                     '{}',?,?, 'metadata-v1'
                   )""",
                (stat.st_size, stat.st_mtime),
            )

        self.media = MediaIdentityFile(
            file_id=1,
            title_id=1,
            path=str(self.media_path),
            size_bytes=stat.st_size,
            modified_at=stat.st_mtime,
            sha256=None,
        )
        self.scan = self._scan()
        self.model = SpeechModelIdentity(
            key="base-q5_1",
            version="fixture",
            sha256="c" * 64,
            size_bytes=4096,
            source="fixture",
            details={"multilingual": True},
        )
        self.streams = [
            {
                "stream_index": 1,
                "stream_type": "audio",
                "language": "eng",
                "channels": 2,
                "sample_rate": 48_000,
                "default_flag": 1,
                "commentary": 0,
                "visual_impaired": 0,
            }
        ]

    def tearDown(self):
        self.temporary.cleanup()

    def _scan(self):
        with self.database.connect() as conn:
            return dict(
                conn.execute(
                    "SELECT * FROM media_identity_scans WHERE id=1"
                ).fetchone()
            )

    def _service(self, engine):
        return NormalSpeechService(
            self.database,
            engine,
            self.model,
            extractor_factory=FakeExtractor,
            language="eng",
        )

    def test_window_plan_is_bounded_and_deterministic(self):
        windows = plan_normal_speech_windows(1800)
        self.assertEqual(len(windows), 8)
        self.assertEqual(
            sum(item.duration_ms for item in windows),
            240_000,
        )
        self.assertEqual(
            windows,
            plan_normal_speech_windows(1800),
        )
        self.assertTrue(all(item.duration_ms == 30_000 for item in windows))

    def test_long_runtime_windows_do_not_overlap_near_budget_boundary(self):
        windows = plan_normal_speech_windows(241)
        ordered = sorted(windows, key=lambda item: item.start_ms)

        self.assertEqual(len(ordered), 8)
        self.assertEqual(
            sum(item.duration_ms for item in ordered),
            240_000,
        )
        self.assertTrue(
            all(
                first.end_ms <= second.start_ms
                for first, second in zip(ordered, ordered[1:])
            )
        )
        self.assertGreaterEqual(ordered[0].start_ms, 0)
        self.assertLessEqual(ordered[-1].end_ms, 241_000)

    def test_short_runtime_is_covered_without_exceeding_eight_windows(self):
        windows = plan_normal_speech_windows(95)
        self.assertEqual(len(windows), 4)
        self.assertEqual(windows[0].start_ms, 0)
        self.assertEqual(windows[-1].end_ms, 95_000)
        self.assertEqual(sum(item.duration_ms for item in windows), 95_000)

    def test_transcript_is_persisted_and_second_run_reuses_it(self):
        engine = FakeSpeechEngine()
        service = self._service(engine)

        first = service.run(
            1,
            self.scan,
            self.media,
            10,
            self.streams,
        )
        self.assertEqual(first.transcript_count, 1)
        self.assertEqual(first.reused_artifact_count, 0)
        self.assertEqual(engine.calls, 1)
        self.assertEqual(FakeExtractor.instances[-1].extract_calls, 1)
        self.assertEqual(
            FakeExtractor.instances[-1].prepared[0].cleanup_calls,
            1,
        )

        second = service.run(
            1,
            self.scan,
            self.media,
            10,
            self.streams,
        )
        self.assertEqual(second.transcript_count, 1)
        self.assertEqual(second.reused_artifact_count, 1)
        self.assertEqual(engine.calls, 1)
        self.assertEqual(FakeExtractor.instances[-1].extract_calls, 1)
        self.assertEqual(
            FakeExtractor.instances[-1].prepared[0].cleanup_calls,
            1,
        )

        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT artifact_type,analyzer_key,start_ms,end_ms,text_value,
                          source_signature,payload_json
                   FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='speech_transcript'"""
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["analyzer_key"], "local-speech-transcript")
        self.assertEqual(rows[0]["start_ms"], 0)
        self.assertEqual(rows[0]["end_ms"], 10_000)
        self.assertIn("dialogue", rows[0]["text_value"])
        self.assertTrue(rows[0]["source_signature"])

    def test_same_stat_media_change_cannot_reuse_old_transcript(self):
        class ContentAwareExtractor(FakeExtractor):
            instances = []

            def extract(self, window):
                self.extract_calls += 1
                media_bytes = Path(self.media.path).read_bytes()
                payload = (
                    b"wav:"
                    + hashlib.sha256(media_bytes).digest()
                    + f":{window.start_ms}:{window.end_ms}".encode()
                )
                identity = SpeechAudioIdentity(
                    sha256=hashlib.sha256(payload).hexdigest(),
                    size_bytes=len(payload),
                    format_key="wav-pcm-s16le",
                    sample_rate_hz=16_000,
                    channels=1,
                    source_signature=self.source_signature(window),
                )
                prepared = FakePreparedAudio(identity)
                self.prepared.append(prepared)
                return prepared

        def service(engine):
            return NormalSpeechService(
                self.database,
                engine,
                self.model,
                extractor_factory=ContentAwareExtractor,
                language="eng",
            )

        first_engine = FakeSpeechEngine()
        first = service(first_engine).run(
            1,
            self.scan,
            self.media,
            10,
            self.streams,
        )
        self.assertEqual(first.transcript_count, 1)
        self.assertEqual(first_engine.calls, 1)

        original_stat = self.media_path.stat()
        original = self.media_path.read_bytes()
        changed = (b"X" if original[:1] != b"X" else b"Y") + original[1:]
        self.assertEqual(len(changed), len(original))
        self.media_path.write_bytes(changed)
        os.utime(
            self.media_path,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        self.assertEqual(self.media_path.stat().st_size, original_stat.st_size)
        self.assertEqual(self.media_path.stat().st_mtime, original_stat.st_mtime)

        second_engine = FakeSpeechEngine()
        second = service(second_engine).run(
            1,
            self.scan,
            self.media,
            10,
            self.streams,
        )

        self.assertEqual(second.transcript_count, 1)
        self.assertEqual(second.reused_artifact_count, 0)
        self.assertEqual(second_engine.calls, 1)
        self.assertEqual(ContentAwareExtractor.instances[-1].extract_calls, 1)
        with self.database.connect() as conn:
            count = conn.execute(
                """SELECT COUNT(*) AS count
                   FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='speech_transcript'"""
            ).fetchone()["count"]
        self.assertEqual(int(count), 2)

    def test_interrupted_run_resumes_from_each_persisted_fragment(self):
        interrupted_engine = FakeSpeechEngine(interrupt_on_call=2)
        interrupted = self._service(interrupted_engine)

        with self.assertRaises(KeyboardInterrupt):
            interrupted.run(
                1,
                self.scan,
                self.media,
                70,
                self.streams,
            )

        with self.database.connect() as conn:
            before = conn.execute(
                """SELECT COUNT(*) AS count
                   FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='speech_transcript'"""
            ).fetchone()["count"]
        self.assertEqual(int(before), 1)

        resumed_engine = FakeSpeechEngine()
        resumed = self._service(resumed_engine)
        result = resumed.run(
            1,
            self.scan,
            self.media,
            70,
            self.streams,
        )

        self.assertEqual(result.transcript_count, 3)
        self.assertEqual(result.reused_artifact_count, 1)
        self.assertEqual(resumed_engine.calls, 2)
        with self.database.connect() as conn:
            after = conn.execute(
                """SELECT COUNT(*) AS count
                   FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='speech_transcript'"""
            ).fetchone()["count"]
        self.assertEqual(int(after), 3)

    def test_catalog_path_rebinding_is_rejected_even_with_same_stat_snapshot(self):
        original_stat = self.media_path.stat()
        replacement = self.media_root / "Replacement - S01E01.mkv"
        replacement.write_bytes(self.media_path.read_bytes())
        os.utime(
            replacement,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        self.assertEqual(replacement.stat().st_size, original_stat.st_size)
        self.assertEqual(replacement.stat().st_mtime, original_stat.st_mtime)

        with self.database.connect() as conn:
            conn.execute(
                "UPDATE files SET path=?,filename=? WHERE id=1",
                (str(replacement), replacement.name),
            )

        with self.assertRaisesRegex(
            NormalSpeechStaleError,
            "cataloged media binding changed",
        ):
            self._service(FakeSpeechEngine()).run(
                1,
                self.scan,
                self.media,
                10,
                self.streams,
            )

        with self.database.connect() as conn:
            count = conn.execute(
                """SELECT COUNT(*) AS count
                   FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='speech_transcript'"""
            ).fetchone()["count"]
        self.assertEqual(int(count), 0)

    def test_file_change_after_transcription_blocks_persistence(self):
        def mutate():
            self.media_path.write_bytes(self.media_path.read_bytes() + b"changed")

        engine = FakeSpeechEngine(mutate=mutate)
        service = self._service(engine)

        with self.assertRaises(NormalSpeechStaleError):
            service.run(
                1,
                self.scan,
                self.media,
                10,
                self.streams,
            )

        with self.database.connect() as conn:
            count = conn.execute(
                """SELECT COUNT(*) AS count
                   FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='speech_transcript'"""
            ).fetchone()["count"]
        self.assertEqual(int(count), 0)
        self.assertEqual(
            FakeExtractor.instances[-1].prepared[0].cleanup_calls,
            1,
        )

    def test_runtime_identity_change_discards_transcript_instead_of_caching_it(self):
        engine = FakeSpeechEngine(change_identity_after_transcribe=True)
        result = self._service(engine).run(
            1,
            self.scan,
            self.media,
            10,
            self.streams,
        )

        self.assertEqual(result.transcript_count, 0)
        self.assertTrue(
            any(
                "Speech engine identity changed during transcription" in item
                for item in result.failures
            )
        )
        self.assertEqual(engine.calls, 1)
        self.assertEqual(
            FakeExtractor.instances[-1].prepared[0].cleanup_calls,
            1,
        )
        with self.database.connect() as conn:
            count = conn.execute(
                """SELECT COUNT(*) AS count
                   FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='speech_transcript'"""
            ).fetchone()["count"]
        self.assertEqual(int(count), 0)

    def test_unavailable_engine_is_optional_and_does_not_extract_audio(self):
        engine = FakeSpeechEngine(available=False)
        result = self._service(engine).run(
            1,
            self.scan,
            self.media,
            60,
            self.streams,
        )
        self.assertEqual(result.transcript_count, 0)
        self.assertIn("speech-engine-unavailable", result.failures)
        self.assertEqual(FakeExtractor.instances, [])


if __name__ == "__main__":
    unittest.main()

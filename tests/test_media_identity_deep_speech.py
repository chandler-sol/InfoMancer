from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.media_identity.deep_speech import (
    MAX_DEEP_SPEECH_AUDIO_BYTES,
    MAX_DEEP_SPEECH_TOTAL_MS,
    MAX_DEEP_SPEECH_WINDOWS,
    DeepSpeechError,
    DeepSpeechPolicy,
    build_deep_speech_plan,
)
from app.media_identity.deep_speech_service import (
    DeepSpeechSamplingError,
    DeepSpeechSamplingService,
    DeepSpeechService,
)
from app.media_identity.fast import FastIdentityService
from app.media_identity.normal_service import NormalIdentityService
from app.media_identity.speech import (
    SpeechAudioIdentity,
    SpeechBinaryIdentity,
    SpeechModelIdentity,
    SpeechTranscript,
)
from app.media_identity.speech_audio import SpeechAudioStream
from app.media_identity.speech_service import (
    NormalSpeechService,
    plan_normal_speech_windows,
)


class FakePreparedAudio:
    def __init__(self, identity: SpeechAudioIdentity) -> None:
        self.identity = identity
        self.cleanup_calls = 0

    def validated_path(self, expected_identity=None) -> str:
        if (
            expected_identity is not None
            and expected_identity != self.identity
        ):
            raise RuntimeError("unexpected speech identity")
        return "fixture.wav"

    def cleanup(self) -> None:
        self.cleanup_calls += 1


class FakeExtractor:
    instances: list["FakeExtractor"] = []

    def __init__(
        self,
        media,
        streams,
        *,
        preferred_language="",
    ) -> None:
        self.media = media
        self.streams = list(streams)
        self.preferred_language = preferred_language
        self.stream = SpeechAudioStream(
            index=1,
            language="eng",
            channels=2,
            sample_rate_hz=48_000,
            default=True,
        )
        self.extract_calls = 0
        self.closed = False
        self.prepared: list[FakePreparedAudio] = []
        self.__class__.instances.append(self)

    def extract(self, window):
        self.extract_calls += 1
        raw = (
            f"{self.media.file_id}:{window.start_ms}:{window.end_ms}:stream-1"
        ).encode()
        identity = SpeechAudioIdentity(
            sha256=hashlib.sha256(raw).hexdigest(),
            size_bytes=max(1, len(raw)),
            format_key="wav-pcm-s16le",
            sample_rate_hz=16_000,
            channels=1,
            source_signature=hashlib.sha256(
                b"source:" + raw
            ).hexdigest(),
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

    def close(self):
        self.closed = True


class FakeSpeechEngine:
    key = "fake-speech"
    version = "1"

    def __init__(self) -> None:
        self.calls = 0
        self.successful_calls = 0
        self.interrupt_after: int | None = None
        self.mutate = None

    def available(self):
        return True

    def binary_identity(self):
        return SpeechBinaryIdentity(
            key=self.key,
            version=self.version,
            sha256="a" * 64,
            size_bytes=1024,
            source="fixture",
            details={"runtime_tree_sha256": "b" * 64},
        )

    def cache_identity(self):
        return {"fixture": "deep-speech-v1", "cpu_only": True}

    def transcribe(self, _audio_path, request):
        self.calls += 1
        if (
            self.interrupt_after is not None
            and self.successful_calls >= self.interrupt_after
        ):
            raise KeyboardInterrupt()
        if self.mutate is not None:
            self.mutate(self.calls)
        self.successful_calls += 1
        return SpeechTranscript(
            text=(
                f"dialogue {request.window.start_ms}-"
                f"{request.window.end_ms}"
            ),
            language="en",
            confidence=0.91,
            details={"fixture": True},
        )


class DeepSpeechPlanTests(unittest.TestCase):
    def test_long_runtime_preserves_normal_then_doubles_bounded_coverage(self):
        normal = plan_normal_speech_windows(1800)
        deep = build_deep_speech_plan(1800)

        self.assertEqual(len(normal), 8)
        self.assertEqual(len(deep.samples), 16)
        self.assertEqual(
            deep.windows[: len(normal)],
            normal,
        )
        self.assertTrue(
            all(
                item.inherited_normal
                for item in deep.samples[: len(normal)]
            )
        )
        self.assertTrue(
            all(
                not item.inherited_normal
                for item in deep.samples[len(normal):]
            )
        )
        self.assertEqual(
            sum(item.window.duration_ms for item in deep.samples),
            480_000,
        )
        ordered = sorted(deep.windows, key=lambda item: item.start_ms)
        self.assertTrue(
            all(
                left.end_ms <= right.start_ms
                for left, right in zip(ordered, ordered[1:])
            )
        )
        self.assertEqual(len(deep.plan_signature), 64)
        self.assertEqual(
            len({item.work_key for item in deep.samples}),
            len(deep.samples),
        )

    def test_short_runtime_does_not_duplicate_full_normal_coverage(self):
        normal = plan_normal_speech_windows(95)
        deep = build_deep_speech_plan(95)

        self.assertEqual(deep.windows, normal)
        self.assertEqual(
            sum(item.duration_ms for item in deep.samples),
            95_000,
        )
        self.assertTrue(
            all(item.inherited_normal for item in deep.samples)
        )

    def test_plan_is_deterministic(self):
        first = build_deep_speech_plan(2700)
        second = build_deep_speech_plan(2700)

        self.assertEqual(first.plan_signature, second.plan_signature)
        self.assertEqual(first.windows, second.windows)
        self.assertEqual(
            [item.work_key for item in first.samples],
            [item.work_key for item in second.samples],
        )

    def test_policy_rejects_unbounded_work(self):
        bad = (
            {"max_windows": MAX_DEEP_SPEECH_WINDOWS + 1},
            {"max_windows": True},
            {"max_total_ms": MAX_DEEP_SPEECH_TOTAL_MS + 1},
            {"max_total_ms": 239_999},
            {"max_audio_bytes": MAX_DEEP_SPEECH_AUDIO_BYTES + 1},
        )
        for kwargs in bad:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(DeepSpeechError):
                    DeepSpeechPolicy(**kwargs)


class DeepSpeechServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeExtractor.instances.clear()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.media_root = self.root / "media"
        self.show_root = self.media_root / "Example Show"
        self.show_root.mkdir(parents=True)
        self.media = self.show_root / "Example Show - S01E01.mkv"
        self.media.write_bytes(b"fixture-media" * 128)
        stat = self.media.stat()

        self.database = Database(self.root / "deep-speech.db")
        self.database.initialize()
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO roots(id,path,kind,label) VALUES (1,?,'tv','TV')",
                (str(self.media_root),),
            )
            conn.execute(
                """INSERT INTO titles(
                     id,root_id,kind,title,metadata_title,folder_path
                   ) VALUES (
                     1,1,'tv','Example Show','Example Show',?
                   )""",
                (str(self.show_root),),
            )
            conn.execute(
                """INSERT INTO files(
                     id,title_id,path,filename,extension,size_bytes,modified_at,
                     season,episode_start,episode_end,parsed_title,runtime_seconds,
                     width,height,video_codec,audio_codec,audio_channels,bitrate,
                     container,dynamic_range,media_info_at,media_info_error,
                     seen_scan
                   ) VALUES (
                     1,1,?,?,?,?,?,1,1,1,'Example Show',1800,1920,1080,
                     'H264','AAC',2,5000000,'MKV','SDR',
                     '2026-09-21T12:00:00','','fixture-scan'
                   )""",
                (
                    str(self.media),
                    self.media.name,
                    "mkv",
                    stat.st_size,
                    stat.st_mtime,
                ),
            )
            conn.execute(
                """INSERT INTO media_streams(
                     file_id,stream_index,stream_type,codec,language,title,
                     channels,channel_layout,sample_rate,default_flag,
                     forced_flag,hearing_impaired,visual_impaired,commentary,
                     disposition_json
                   ) VALUES (
                     1,1,'audio','aac','eng','Main',2,'stereo',48000,1,
                     0,0,0,0,'{}'
                   )"""
            )
            conn.executemany(
                """INSERT INTO expected_episodes(
                     id,title_id,tvdb_episode_id,season,episode,name
                   ) VALUES (?,?,?,?,?,?)""",
                [
                    (1, 1, 1001, 1, 1, "Episode One"),
                    (2, 1, 1002, 1, 2, "Episode Two"),
                ],
            )

        self.fast = FastIdentityService(self.database)
        self.scan = self.fast.scan_file(1)
        self.engine = FakeSpeechEngine()
        self.model = SpeechModelIdentity(
            key="base-q5_1",
            version="fixture",
            sha256="c" * 64,
            size_bytes=4096,
            source="fixture",
            details={"multilingual": True},
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _scan_row(self):
        with self.database.connect() as conn:
            return dict(conn.execute(
                "SELECT * FROM media_identity_scans WHERE id=?",
                (self.scan.scan_id,),
            ).fetchone())

    def _file_and_streams(self):
        with self.database.connect() as conn:
            file_row = dict(conn.execute(
                "SELECT * FROM files WHERE id=1"
            ).fetchone())
            streams = [
                dict(row)
                for row in conn.execute(
                    """SELECT stream_index,stream_type,codec,language,title,
                              channels,channel_layout,sample_rate,default_flag,
                              forced_flag,hearing_impaired,visual_impaired,
                              commentary,disposition_json
                       FROM media_streams
                       WHERE file_id=1 ORDER BY stream_index"""
                ).fetchall()
            ]
        from app.media_identity.models import MediaIdentityFile
        scan = self._scan_row()
        media = MediaIdentityFile(
            file_id=1,
            title_id=1,
            path=str(self.media),
            size_bytes=int(scan["file_size_bytes"]),
            modified_at=scan["file_modified_at"],
            sha256=str(scan["file_sha256"]),
        )
        return file_row, media, streams

    def _deep_service(self):
        return DeepSpeechService(
            self.database,
            self.engine,
            self.model,
            extractor_factory=FakeExtractor,
            language="eng",
        )

    def _sampling_service(self):
        return DeepSpeechSamplingService(
            self.database,
            self.engine,
            self.model,
            extractor_factory=FakeExtractor,
            language="eng",
        )

    def test_deep_reuses_sealed_normal_windows_and_transcribes_only_extras(self):
        file_row, media, streams = self._file_and_streams()
        scan = self._scan_row()
        normal = NormalSpeechService(
            self.database,
            self.engine,
            self.model,
            extractor_factory=FakeExtractor,
            language="eng",
        )
        normal_run = normal.run(
            self.scan.scan_id,
            scan,
            media,
            file_row["runtime_seconds"],
            streams,
        )
        self.assertTrue(normal_run.coverage_complete)
        self.assertEqual(normal_run.transcript_count, 8)
        self.assertEqual(self.engine.successful_calls, 8)

        deep_run = self._deep_service().run(
            self.scan.scan_id,
            self._scan_row(),
            media,
            file_row["runtime_seconds"],
            streams,
        )
        self.assertTrue(deep_run.coverage_complete)
        self.assertEqual(deep_run.transcript_count, 16)
        self.assertEqual(deep_run.reused_artifact_count, 8)
        self.assertEqual(self.engine.successful_calls, 16)

    def test_unsealed_legacy_normal_transcript_is_reverified_for_deep(self):
        file_row, media, streams = self._file_and_streams()
        normal = NormalSpeechService(
            self.database,
            self.engine,
            self.model,
            extractor_factory=FakeExtractor,
            language="eng",
        )
        normal.run(
            self.scan.scan_id,
            self._scan_row(),
            media,
            file_row["runtime_seconds"],
            streams,
        )
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT id,payload_json FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='speech_transcript'
                   ORDER BY id LIMIT 1"""
            ).fetchone()
            payload = json.loads(row["payload_json"])
            payload.pop("transcript_output_sha256", None)
            conn.execute(
                """UPDATE media_identity_artifacts
                   SET payload_json=? WHERE id=?""",
                (json.dumps(payload, sort_keys=True), int(row["id"])),
            )

        calls_before = self.engine.successful_calls
        deep_run = self._deep_service().run(
            self.scan.scan_id,
            self._scan_row(),
            media,
            file_row["runtime_seconds"],
            streams,
        )
        self.assertTrue(deep_run.coverage_complete)
        self.assertEqual(deep_run.reused_artifact_count, 7)
        self.assertEqual(
            self.engine.successful_calls - calls_before,
            9,
        )
        with self.database.connect() as conn:
            repaired = conn.execute(
                """SELECT profile,payload_json FROM media_identity_artifacts
                   WHERE id=?""",
                (int(row["id"]),),
            ).fetchone()
        repaired_payload = json.loads(repaired["payload_json"])
        self.assertEqual(repaired["profile"], "deep")
        self.assertEqual(
            len(repaired_payload["transcript_output_sha256"]),
            64,
        )

    def test_sampling_manifest_fast_path_avoids_reextracting_audio(self):
        before_scan = self._scan_row()["claimed_identity_json"]
        first = self._sampling_service().run(self.scan.scan_id)

        self.assertTrue(first.coverage_complete)
        self.assertEqual(first.transcript_count, 16)
        self.assertIsNotNone(first.manifest_artifact_id)
        self.assertEqual(self.engine.successful_calls, 16)
        first_instance_count = len(FakeExtractor.instances)

        second = self._sampling_service().run(self.scan.scan_id)
        self.assertTrue(second.coverage_complete)
        self.assertEqual(second.reused_artifact_count, 16)
        self.assertEqual(
            second.manifest_artifact_id,
            first.manifest_artifact_id,
        )
        self.assertEqual(len(FakeExtractor.instances), first_instance_count)
        self.assertEqual(self.engine.successful_calls, 16)
        self.assertEqual(
            self._scan_row()["claimed_identity_json"],
            before_scan,
        )

    def test_interrupted_sampling_resumes_fragments_without_manifest(self):
        self.engine.interrupt_after = 5
        with self.assertRaises(KeyboardInterrupt):
            self._sampling_service().run(self.scan.scan_id)

        with self.database.connect() as conn:
            transcript_count = conn.execute(
                """SELECT COUNT(*) FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='speech_transcript'"""
            ).fetchone()[0]
            manifest_count = conn.execute(
                """SELECT COUNT(*) FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='deep_speech_manifest'"""
            ).fetchone()[0]
        self.assertEqual(transcript_count, 5)
        self.assertEqual(manifest_count, 0)

        self.engine.interrupt_after = None
        resumed = self._sampling_service().run(self.scan.scan_id)
        self.assertTrue(resumed.coverage_complete)
        self.assertEqual(resumed.reused_artifact_count, 5)
        self.assertEqual(self.engine.successful_calls, 16)
        self.assertIsNotNone(resumed.manifest_artifact_id)

    def test_manifest_rejects_tampered_transcript_output(self):
        result = self._sampling_service().run(self.scan.scan_id)
        self.assertTrue(result.coverage_complete)
        assert result.manifest_artifact_id is not None

        with self.database.connect() as conn:
            manifest = conn.execute(
                """SELECT payload_json FROM media_identity_artifacts
                   WHERE id=?""",
                (result.manifest_artifact_id,),
            ).fetchone()
            payload = json.loads(manifest["payload_json"])
            child_id = int(payload["observations"][0]["artifact_id"])
            conn.execute(
                """UPDATE media_identity_artifacts
                   SET text_value=text_value || '-tampered'
                   WHERE id=?""",
                (child_id,),
            )

        with self.assertRaisesRegex(
            DeepSpeechSamplingError,
            "artifact integrity",
        ):
            self._sampling_service().run(self.scan.scan_id)

    def test_revision_change_during_transcription_blocks_deep_publication(self):
        mutated = {"done": False}

        def mutate(call_count: int) -> None:
            if call_count != 2 or mutated["done"]:
                return
            mutated["done"] = True
            with self.database.connect() as conn:
                row = conn.execute(
                    """SELECT claimed_identity_json
                       FROM media_identity_scans WHERE id=?""",
                    (self.scan.scan_id,),
                ).fetchone()
                claimed = json.loads(row["claimed_identity_json"])
                claimed["result_revision"] = int(
                    claimed.get("result_revision") or 1
                ) + 1
                conn.execute(
                    """UPDATE media_identity_scans
                       SET claimed_identity_json=? WHERE id=?""",
                    (
                        json.dumps(claimed, sort_keys=True),
                        self.scan.scan_id,
                    ),
                )

        self.engine.mutate = mutate
        with self.assertRaisesRegex(
            NormalSpeechService.__mro__[0].__name__ and Exception,
            "publication changed",
        ):
            self._sampling_service().run(self.scan.scan_id)

        with self.database.connect() as conn:
            manifest_count = conn.execute(
                """SELECT COUNT(*) FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='deep_speech_manifest'"""
            ).fetchone()[0]
        self.assertEqual(manifest_count, 0)


if __name__ == "__main__":
    unittest.main()

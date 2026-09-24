from __future__ import annotations

import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.media_identity.fast import FastIdentityService
from app.media_identity.fingerprint import (
    AUDIO_ENVELOPE_DHASH64_V1,
    VIDEO_DHASH64_V1,
    ContentFingerprint,
    FingerprintError,
    FingerprintSample,
)
from app.media_identity.fingerprint_audio import (
    LocalFingerprintError,
    audio_envelope_dhash64_from_pcm_s16le,
    plan_audio_fingerprint_timestamps,
)
from app.media_identity.fingerprint_audio_service import (
    DeepAudioFingerprintArtifactService,
    DeepAudioFingerprintCorrelationService,
)
from app.media_identity.fingerprint_correlation import (
    DeepFingerprintCorrelationService,
)
from app.media_identity.fingerprint_local import (
    plan_video_fingerprint_timestamps,
)
from app.media_identity.fingerprint_service import (
    DeepFingerprintArtifactService,
)


def _pcm(values: list[int]) -> bytes:
    return struct.pack(f"<{len(values)}h", *values)


class AudioFingerprintPrimitiveTests(unittest.TestCase):
    def test_increasing_energy_shape_has_zero_energy_slope_bits(self) -> None:
        values = [
            amplitude
            for amplitude in range(33)
            for _ in range(4)
        ]
        result = audio_envelope_dhash64_from_pcm_s16le(_pcm(values))

        self.assertEqual(result[:8], "00000000")

    def test_decreasing_energy_shape_sets_energy_slope_bits(self) -> None:
        values = [
            amplitude
            for amplitude in range(32, -1, -1)
            for _ in range(4)
        ]
        result = audio_envelope_dhash64_from_pcm_s16le(_pcm(values))

        self.assertEqual(result[:8], "ffffffff")

    def test_global_volume_scale_preserves_shape_hash(self) -> None:
        base = [
            (index % 11 + 1) * (1 if index % 3 else -1)
            for index in range(330)
        ]
        scaled = [value * 20 for value in base]

        self.assertEqual(
            audio_envelope_dhash64_from_pcm_s16le(_pcm(base)),
            audio_envelope_dhash64_from_pcm_s16le(_pcm(scaled)),
        )

    def test_pcm_contract_rejects_odd_and_too_short_payloads(self) -> None:
        with self.assertRaisesRegex(FingerprintError, "complete 16-bit"):
            audio_envelope_dhash64_from_pcm_s16le(b"\x00")
        with self.assertRaisesRegex(FingerprintError, "too short"):
            audio_envelope_dhash64_from_pcm_s16le(_pcm([0] * 32))

    def test_audio_timestamp_plan_is_deterministic_and_interior(self) -> None:
        first = plan_audio_fingerprint_timestamps(
            1_800_000,
            sample_count=8,
        )
        second = plan_audio_fingerprint_timestamps(
            1_800_000,
            sample_count=8,
        )

        self.assertEqual(first, second)
        self.assertEqual(len(first), 8)
        self.assertEqual(tuple(sorted(first)), first)
        self.assertEqual(len(set(first)), 8)
        self.assertGreater(first[0], 90_000)
        self.assertLess(first[-1], 1_710_000)


class FakeAudioExtractor:
    values_by_file: dict[int, tuple[str, ...]] = {}
    calls: dict[int, int] = {}

    def __init__(self, media, runtime_ms, *, stream) -> None:
        self.media = media
        self.stream = stream
        self.runtime_ms = int(runtime_ms)
        self.timestamps = plan_audio_fingerprint_timestamps(
            self.runtime_ms,
            sample_count=6,
        )
        stat = Path(media.path).stat()
        self._generation = (
            int(stat.st_size),
            int(getattr(stat, "st_mtime_ns", 0)),
        )
        self.source_signature = hashlib.sha256(
            (
                f"audio:{media.file_id}:{media.sha256}:"
                f"{self.runtime_ms}:{self._generation}:"
                f"{dict(stream.cache_identity())}"
            ).encode()
        ).hexdigest()

    def available(self) -> bool:
        return True

    def extract(self) -> ContentFingerprint:
        current = Path(self.media.path).stat()
        if (
            int(current.st_size),
            int(getattr(current, "st_mtime_ns", 0)),
        ) != self._generation:
            raise LocalFingerprintError(
                "fixture audio media generation changed"
            )
        file_id = int(self.media.file_id)
        self.__class__.calls[file_id] = (
            self.__class__.calls.get(file_id, 0) + 1
        )
        values = self.__class__.values_by_file.get(
            file_id,
            tuple(f"{file_id:016x}" for _ in self.timestamps),
        )
        return ContentFingerprint(
            file_id=file_id,
            file_sha256=str(self.media.sha256),
            runtime_ms=self.runtime_ms,
            algorithm=AUDIO_ENVELOPE_DHASH64_V1,
            samples=tuple(
                FingerprintSample(timestamp_ms=timestamp, value=value)
                for timestamp, value in zip(self.timestamps, values)
            ),
            source_kind="local_ffmpeg_audio",
            source_signature=self.source_signature,
            parameters={
                "extractor_version": 1,
                "sample_count": len(self.timestamps),
                "window_ms": 4_000,
                "sample_rate_hz": 8_000,
                "channels": 1,
                "stream": dict(self.stream.cache_identity()),
                "feature_bins": 33,
            },
        )


class FakeVideoExtractor:
    values_by_file: dict[int, tuple[str, ...]] = {}
    calls: dict[int, int] = {}

    def __init__(self, media, runtime_ms) -> None:
        self.media = media
        self.runtime_ms = int(runtime_ms)
        self.timestamps = plan_video_fingerprint_timestamps(
            self.runtime_ms,
            sample_count=6,
        )
        stat = Path(media.path).stat()
        self._generation = (
            int(stat.st_size),
            int(getattr(stat, "st_mtime_ns", 0)),
        )
        self.source_signature = hashlib.sha256(
            (
                f"video:{media.file_id}:{media.sha256}:"
                f"{self.runtime_ms}:{self._generation}"
            ).encode()
        ).hexdigest()

    def available(self) -> bool:
        return True

    def extract(self) -> ContentFingerprint:
        file_id = int(self.media.file_id)
        self.__class__.calls[file_id] = (
            self.__class__.calls.get(file_id, 0) + 1
        )
        values = self.__class__.values_by_file.get(
            file_id,
            tuple(f"{file_id:016x}" for _ in self.timestamps),
        )
        return ContentFingerprint(
            file_id=file_id,
            file_sha256=str(self.media.sha256),
            runtime_ms=self.runtime_ms,
            algorithm=VIDEO_DHASH64_V1,
            samples=tuple(
                FingerprintSample(timestamp_ms=timestamp, value=value)
                for timestamp, value in zip(self.timestamps, values)
            ),
            source_kind="local_ffmpeg",
            source_signature=self.source_signature,
            parameters={
                "extractor_version": 1,
                "sample_count": len(self.timestamps),
                "filter": "fixture",
            },
        )


class AudioFingerprintServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeAudioExtractor.values_by_file = {}
        FakeAudioExtractor.calls = {}
        FakeVideoExtractor.values_by_file = {}
        FakeVideoExtractor.calls = {}

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.media_root = self.root / "media"
        self.show_root = self.media_root / "Example"
        self.show_root.mkdir(parents=True)
        self.paths: dict[int, Path] = {}

        self.database = Database(self.root / "audio-fingerprint.db")
        self.database.initialize()
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO roots(id,path,kind,label) VALUES (1,?,'tv','TV')",
                (str(self.media_root),),
            )
            conn.execute(
                """INSERT INTO titles(
                     id,root_id,kind,title,metadata_title,folder_path
                   ) VALUES (1,1,'tv','Example','Example',?)""",
                (str(self.show_root),),
            )
            for file_id in (1, 2):
                path = self.show_root / f"Example - S01E{file_id:02d}.mkv"
                path.write_bytes(
                    (f"audio-fixture-{file_id}".encode()) * 128
                )
                self.paths[file_id] = path
                stat = path.stat()
                conn.execute(
                    """INSERT INTO files(
                         id,title_id,path,filename,extension,size_bytes,modified_at,
                         season,episode_start,episode_end,parsed_title,runtime_seconds,
                         width,height,video_codec,audio_codec,audio_channels,bitrate,
                         container,dynamic_range,media_info_at,media_info_error,
                         seen_scan
                       ) VALUES (
                         ?,1,?,?,?,?,?,1,?,?, 'Example',600,1920,1080,
                         'H264','AAC',2,5000000,'MKV','SDR',
                         '2026-09-24T12:00:00','','fixture'
                       )""",
                    (
                        file_id,
                        str(path),
                        path.name,
                        "mkv",
                        stat.st_size,
                        stat.st_mtime,
                        file_id,
                        file_id,
                    ),
                )
                conn.executemany(
                    """INSERT INTO media_streams(
                         file_id,stream_index,stream_type,codec,language,title,
                         channels,channel_layout,sample_rate,default_flag,
                         forced_flag,hearing_impaired,visual_impaired,commentary,
                         disposition_json
                       ) VALUES (
                         ?,?,'audio','aac',?,?,2,'stereo',48000,?,
                         0,0,0,?,'{}'
                       )""",
                    [
                        (
                            file_id,
                            1,
                            "eng",
                            "Main English",
                            1,
                            0,
                        ),
                        (
                            file_id,
                            2,
                            "eng",
                            "Commentary",
                            0,
                            1,
                        ),
                    ],
                )
                conn.execute(
                    """INSERT INTO expected_episodes(
                         id,title_id,tvdb_episode_id,season,episode,name
                       ) VALUES (?,?,?,?,?,?)""",
                    (
                        file_id,
                        1,
                        2000 + file_id,
                        1,
                        file_id,
                        f"Episode {file_id}",
                    ),
                )

        self.fast = FastIdentityService(self.database)
        self.scan1 = self.fast.scan_file(1)
        self.scan2 = self.fast.scan_file(2)
        self.audio = DeepAudioFingerprintArtifactService(
            self.database,
            extractor_factory=FakeAudioExtractor,
        )
        self.video = DeepFingerprintArtifactService(
            self.database,
            extractor_factory=FakeVideoExtractor,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_audio_fingerprint_uses_primary_non_commentary_stream(self) -> None:
        result = self.audio.ensure_scan(self.scan1.scan_id)

        self.assertIsNotNone(result.fingerprint)
        stream = result.fingerprint.parameters["stream"]
        self.assertEqual(stream["index"], 1)
        self.assertEqual(stream["language"], "eng")
        self.assertFalse(stream["commentary"])

    def test_audio_artifact_reuses_sealed_cache(self) -> None:
        first = self.audio.ensure_scan(self.scan1.scan_id)
        second = self.audio.ensure_scan(self.scan1.scan_id)

        self.assertIsNotNone(first.artifact_id)
        self.assertTrue(second.reused)
        self.assertEqual(second.artifact_id, first.artifact_id)
        self.assertEqual(FakeAudioExtractor.calls[1], 1)
        self.assertEqual(
            second.fingerprint.algorithm,
            AUDIO_ENVELOPE_DHASH64_V1,
        )

    def test_audio_tamper_is_regenerated_and_repaired(self) -> None:
        first = self.audio.ensure_scan(self.scan1.scan_id)
        assert first.artifact_id is not None

        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM media_identity_artifacts WHERE id=?",
                (first.artifact_id,),
            ).fetchone()
            payload = json.loads(row["payload_json"])
            payload["samples"][0]["value"] = "f" * 16
            conn.execute(
                "UPDATE media_identity_artifacts SET payload_json=? WHERE id=?",
                (
                    json.dumps(payload, sort_keys=True),
                    first.artifact_id,
                ),
            )

        second = self.audio.ensure_scan(self.scan1.scan_id)

        self.assertFalse(second.reused)
        self.assertEqual(second.artifact_id, first.artifact_id)
        self.assertEqual(FakeAudioExtractor.calls[1], 2)

    def test_video_and_audio_artifacts_do_not_collide(self) -> None:
        video = self.video.ensure_scan(self.scan1.scan_id)
        audio = self.audio.ensure_scan(self.scan1.scan_id)

        self.assertIsNotNone(video.artifact_id)
        self.assertIsNotNone(audio.artifact_id)
        self.assertNotEqual(video.artifact_id, audio.artifact_id)
        self.assertNotEqual(
            video.fingerprint.cache_key(),
            audio.fingerprint.cache_key(),
        )
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT analyzer_key,source_kind
                   FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='content_fingerprint'
                   ORDER BY analyzer_key"""
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {row["analyzer_key"] for row in rows},
            {
                VIDEO_DHASH64_V1.key,
                AUDIO_ENVELOPE_DHASH64_V1.key,
            },
        )

    def test_video_and_audio_correlation_manifests_do_not_collide(self) -> None:
        common = (
            "0000000000000000",
            "1111111111111111",
            "2222222222222222",
            "3333333333333333",
            "4444444444444444",
            "5555555555555555",
        )
        FakeVideoExtractor.values_by_file = {1: common, 2: common}
        FakeAudioExtractor.values_by_file = {1: common, 2: common}

        video_run = DeepFingerprintCorrelationService(
            self.database,
            artifact_service=self.video,
        ).run(self.scan1.scan_id)
        audio_run = DeepAudioFingerprintCorrelationService(
            self.database,
            artifact_service=self.audio,
        ).run(self.scan1.scan_id)

        self.assertTrue(video_run.coverage_complete)
        self.assertTrue(audio_run.coverage_complete)
        self.assertEqual(video_run.algorithm_key, VIDEO_DHASH64_V1.key)
        self.assertEqual(
            audio_run.algorithm_key,
            AUDIO_ENVELOPE_DHASH64_V1.key,
        )
        self.assertNotEqual(
            video_run.manifest_artifact_id,
            audio_run.manifest_artifact_id,
        )
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT analyzer_key,source_kind
                   FROM media_identity_artifacts
                   WHERE file_id=1
                     AND artifact_type='deep_fingerprint_manifest'
                   ORDER BY analyzer_key"""
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {row["analyzer_key"] for row in rows},
            {
                f"deep-fingerprint-correlation:{VIDEO_DHASH64_V1.key}",
                (
                    "deep-fingerprint-correlation:"
                    f"{AUDIO_ENVELOPE_DHASH64_V1.key}"
                ),
            },
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.media_identity.fast import FastIdentityService
from app.media_identity.fingerprint import (
    MAX_FINGERPRINT_CANDIDATES,
    ContentFingerprint,
    FingerprintAlgorithm,
    FingerprintError,
    FingerprintFamily,
    FingerprintMatchPolicy,
    FingerprintSample,
    VIDEO_DHASH64_V1,
    bounded_fingerprint_matches,
    compare_content_fingerprints,
    fingerprint_from_payload,
)
from app.media_identity.fingerprint_local import (
    dhash64_from_gray9x8,
    plan_video_fingerprint_timestamps,
)
from app.media_identity.fingerprint_service import (
    DeepFingerprintArtifactService,
    DeepFingerprintError,
)


def _fingerprint(
    file_id: int,
    values: list[str],
    *,
    sha_char: str | None = None,
    algorithm=VIDEO_DHASH64_V1,
) -> ContentFingerprint:
    return ContentFingerprint(
        file_id=file_id,
        file_sha256=(sha_char or hex(file_id % 16)[2:] or "a") * 64,
        runtime_ms=600_000,
        algorithm=algorithm,
        samples=tuple(
            FingerprintSample(
                timestamp_ms=10_000 + index * 30_000,
                value=value,
            )
            for index, value in enumerate(values)
        ),
        source_kind="local_ffmpeg",
        source_signature=f"source-{file_id}",
        parameters={"scale": "9x8-gray"},
    )


class FingerprintContractTests(unittest.TestCase):
    def test_round_trip_requires_valid_output_seal(self) -> None:
        original = _fingerprint(
            1,
            [
                "0000000000000000",
                "1111111111111111",
                "2222222222222222",
                "3333333333333333",
                "4444444444444444",
                "5555555555555555",
            ],
            sha_char="a",
        )
        payload = original.persisted_payload()
        restored = fingerprint_from_payload(payload)

        self.assertEqual(restored, original)
        self.assertEqual(restored.cache_key(), original.cache_key())
        self.assertEqual(len(payload["fingerprint_output_sha256"]), 64)

    def test_tampered_sample_fails_output_seal(self) -> None:
        original = _fingerprint(
            1,
            ["0" * 16] * 6,
            sha_char="a",
        )
        payload = original.persisted_payload()
        payload["samples"][0]["value"] = "f" * 16

        with self.assertRaisesRegex(
            FingerprintError,
            "integrity seal",
        ):
            fingerprint_from_payload(payload)

    def test_sample_width_and_timestamp_contracts_fail_closed(self) -> None:
        with self.assertRaisesRegex(FingerprintError, "width"):
            _fingerprint(
                1,
                ["0" * 15] * 6,
                sha_char="a",
            )

        with self.assertRaisesRegex(FingerprintError, "increasing"):
            ContentFingerprint(
                file_id=1,
                file_sha256="a" * 64,
                runtime_ms=100_000,
                algorithm=VIDEO_DHASH64_V1,
                samples=(
                    FingerprintSample(10_000, "0" * 16),
                    FingerprintSample(10_000, "1" * 16),
                ),
                source_kind="local",
                source_signature="fixture",
            )

    def test_exact_sequence_scores_one(self) -> None:
        values = [
            "0000000000000000",
            "0123456789abcdef",
            "1111111111111111",
            "2222222222222222",
            "3333333333333333",
            "ffffffffffffffff",
        ]
        left = _fingerprint(1, values, sha_char="a")
        right = _fingerprint(2, values, sha_char="b")

        comparison = compare_content_fingerprints(left, right)

        self.assertIsNotNone(comparison)
        self.assertEqual(comparison.alignment_shift, 0)
        self.assertEqual(comparison.compared_samples, 6)
        self.assertEqual(comparison.mean_similarity, 1.0)
        self.assertEqual(comparison.median_similarity, 1.0)
        self.assertEqual(comparison.coverage, 1.0)

    def test_small_sequence_shift_is_measured_without_becoming_a_verdict(self) -> None:
        core = [
            "0000000000000000",
            "1111111111111111",
            "2222222222222222",
            "3333333333333333",
            "4444444444444444",
            "5555555555555555",
            "6666666666666666",
            "7777777777777777",
        ]
        left = _fingerprint(1, core, sha_char="a")
        right = _fingerprint(
            2,
            ["fedcba9876543210", *core],
            sha_char="b",
        )

        comparison = compare_content_fingerprints(left, right)

        self.assertIsNotNone(comparison)
        self.assertEqual(comparison.alignment_shift, 1)
        self.assertEqual(comparison.compared_samples, 8)
        self.assertEqual(comparison.mean_similarity, 1.0)
        self.assertLess(comparison.coverage, 1.0)

    def test_one_lucky_sample_cannot_create_a_comparison(self) -> None:
        policy = FingerprintMatchPolicy(min_compared_samples=6)
        left = _fingerprint(
            1,
            ["0" * 16],
            sha_char="a",
        )
        right = _fingerprint(
            2,
            ["0" * 16],
            sha_char="b",
        )

        self.assertIsNone(
            compare_content_fingerprints(
                left,
                right,
                policy=policy,
            )
        )

    def test_algorithm_mismatch_is_incomparable(self) -> None:
        alternate = FingerprintAlgorithm(
            key="video-dhash64-sequence",
            version="2",
            family=FingerprintFamily.PERCEPTUAL_VIDEO,
            bits_per_sample=64,
            max_samples=24,
        )
        left = _fingerprint(
            1,
            ["0" * 16] * 6,
            sha_char="a",
        )
        right = _fingerprint(
            2,
            ["0" * 16] * 6,
            sha_char="b",
            algorithm=alternate,
        )

        self.assertIsNone(compare_content_fingerprints(left, right))

    def test_bounded_matcher_rejects_candidate_explosion(self) -> None:
        query = _fingerprint(
            1,
            ["0" * 16] * 6,
            sha_char="a",
        )
        candidates = [
            _fingerprint(
                index + 2,
                ["0" * 16] * 6,
                sha_char=hex((index + 2) % 16)[2:],
            )
            for index in range(MAX_FINGERPRINT_CANDIDATES + 1)
        ]

        with self.assertRaisesRegex(FingerprintError, "candidate count"):
            bounded_fingerprint_matches(query, candidates)

    def test_bounded_matcher_rejects_duplicate_candidate_files(self) -> None:
        query = _fingerprint(
            1,
            ["0" * 16] * 6,
            sha_char="a",
        )
        duplicate = _fingerprint(
            2,
            ["0" * 16] * 6,
            sha_char="b",
        )

        with self.assertRaisesRegex(FingerprintError, "unique"):
            bounded_fingerprint_matches(
                query,
                [duplicate, duplicate],
            )

    def test_rank_is_deterministic_and_skips_self(self) -> None:
        query = _fingerprint(
            1,
            [
                "0000000000000000",
                "1111111111111111",
                "2222222222222222",
                "3333333333333333",
                "4444444444444444",
                "5555555555555555",
            ],
            sha_char="a",
        )
        exact = _fingerprint(
            2,
            [item.value for item in query.samples],
            sha_char="b",
        )
        weak = _fingerprint(
            3,
            ["f" * 16] * 6,
            sha_char="c",
        )

        ranked = bounded_fingerprint_matches(
            query,
            [weak, query, exact],
        )

        self.assertEqual(
            [item.right_file_id for item in ranked],
            [2, 3],
        )
        self.assertGreater(
            ranked[0].mean_similarity,
            ranked[1].mean_similarity,
        )




class LocalFingerprintPrimitiveTests(unittest.TestCase):
    def test_dhash_known_gradients(self) -> None:
        ascending = bytes(
            value
            for _row in range(8)
            for value in range(9)
        )
        descending = bytes(
            value
            for _row in range(8)
            for value in range(8, -1, -1)
        )

        self.assertEqual(
            dhash64_from_gray9x8(ascending),
            "0000000000000000",
        )
        self.assertEqual(
            dhash64_from_gray9x8(descending),
            "ffffffffffffffff",
        )

    def test_dhash_rejects_wrong_frame_size(self) -> None:
        with self.assertRaisesRegex(FingerprintError, "exactly one 9x8"):
            dhash64_from_gray9x8(b"short")

    def test_timestamp_plan_is_deterministic_bounded_and_interior(self) -> None:
        first = plan_video_fingerprint_timestamps(
            1_800_000,
            sample_count=16,
        )
        second = plan_video_fingerprint_timestamps(
            1_800_000,
            sample_count=16,
        )

        self.assertEqual(first, second)
        self.assertEqual(len(first), 16)
        self.assertEqual(len(set(first)), 16)
        self.assertEqual(tuple(sorted(first)), first)
        self.assertGreater(first[0], 90_000)
        self.assertLess(first[-1], 1_710_000)


class FakeVideoFingerprintExtractor:
    values_by_file: dict[int, tuple[str, ...]] = {}
    extract_calls: dict[int, int] = {}
    mutate = None

    def __init__(self, media, runtime_ms) -> None:
        self.media = media
        self.runtime_ms = int(runtime_ms)
        self.timestamps = tuple(
            30_000 + index * 60_000
            for index in range(6)
        )
        self.source_signature = hashlib.sha256(
            (
                f"fake-fingerprint:{media.file_id}:"
                f"{media.sha256}:{self.runtime_ms}"
            ).encode()
        ).hexdigest()

    def available(self) -> bool:
        return True

    def extract(self) -> ContentFingerprint:
        file_id = int(self.media.file_id)
        self.__class__.extract_calls[file_id] = (
            self.__class__.extract_calls.get(file_id, 0) + 1
        )
        mutate = self.__class__.mutate
        if mutate is not None:
            mutate(file_id)
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


class DeepFingerprintArtifactServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeVideoFingerprintExtractor.values_by_file = {}
        FakeVideoFingerprintExtractor.extract_calls = {}
        FakeVideoFingerprintExtractor.mutate = None

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.media_root = self.root / "media"
        self.show_root = self.media_root / "Example Show"
        self.show_root.mkdir(parents=True)

        self.paths = {}
        for file_id in (1, 2, 3):
            path = self.show_root / f"Example Show - S01E{file_id:02d}.mkv"
            path.write_bytes(
                (f"fixture-media-{file_id}".encode()) * 128
            )
            self.paths[file_id] = path

        self.database = Database(self.root / "fingerprint.db")
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
            for file_id in (1, 2, 3):
                path = self.paths[file_id]
                stat = path.stat()
                conn.execute(
                    """INSERT INTO files(
                         id,title_id,path,filename,extension,size_bytes,modified_at,
                         season,episode_start,episode_end,parsed_title,runtime_seconds,
                         width,height,video_codec,audio_codec,audio_channels,bitrate,
                         container,dynamic_range,media_info_at,media_info_error,
                         seen_scan
                       ) VALUES (
                         ?,1,?,?,?,?,?,1,?,?, 'Example Show',600,1920,1080,
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
                conn.execute(
                    """INSERT INTO expected_episodes(
                         id,title_id,tvdb_episode_id,season,episode,name
                       ) VALUES (?,?,?,?,?,?)""",
                    (
                        file_id,
                        1,
                        1000 + file_id,
                        1,
                        file_id,
                        f"Episode {file_id}",
                    ),
                )

        self.fast = FastIdentityService(self.database)
        self.scan1 = self.fast.scan_file(1)
        self.scan2 = self.fast.scan_file(2)
        self.service = DeepFingerprintArtifactService(
            self.database,
            extractor_factory=FakeVideoFingerprintExtractor,
        )

    def tearDown(self) -> None:
        FakeVideoFingerprintExtractor.mutate = None
        self.temporary.cleanup()

    def test_fast_scan_hash_is_enough_without_background_hash_row(self) -> None:
        with self.database.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM media_file_hashes"
            ).fetchone()[0]
        self.assertEqual(count, 0)

        result = self.service.ensure_scan(self.scan1.scan_id)

        self.assertEqual(result.file_id, 1)
        self.assertIsNotNone(result.artifact_id)
        self.assertIsNotNone(result.fingerprint)
        self.assertFalse(result.reused)
        self.assertEqual(
            FakeVideoFingerprintExtractor.extract_calls[1],
            1,
        )

    def test_second_run_reuses_sealed_artifact_without_extraction(self) -> None:
        first = self.service.ensure_scan(self.scan1.scan_id)
        second = self.service.ensure_scan(self.scan1.scan_id)

        self.assertTrue(second.reused)
        self.assertEqual(second.artifact_id, first.artifact_id)
        self.assertEqual(
            FakeVideoFingerprintExtractor.extract_calls[1],
            1,
        )

    def test_tampered_artifact_is_regenerated_and_repaired(self) -> None:
        first = self.service.ensure_scan(self.scan1.scan_id)
        assert first.artifact_id is not None

        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT payload_json FROM media_identity_artifacts
                   WHERE id=?""",
                (first.artifact_id,),
            ).fetchone()
            payload = json.loads(row["payload_json"])
            payload["samples"][0]["value"] = "f" * 16
            conn.execute(
                """UPDATE media_identity_artifacts
                   SET payload_json=? WHERE id=?""",
                (
                    json.dumps(payload, sort_keys=True),
                    first.artifact_id,
                ),
            )

        second = self.service.ensure_scan(self.scan1.scan_id)

        self.assertFalse(second.reused)
        self.assertEqual(second.artifact_id, first.artifact_id)
        self.assertEqual(
            FakeVideoFingerprintExtractor.extract_calls[1],
            2,
        )
        with self.database.connect() as conn:
            repaired = conn.execute(
                """SELECT payload_json FROM media_identity_artifacts
                   WHERE id=?""",
                (first.artifact_id,),
            ).fetchone()
        fingerprint_from_payload(json.loads(repaired["payload_json"]))

    def test_publication_revision_change_mid_extract_blocks_persistence(self) -> None:
        def mutate(file_id: int) -> None:
            if file_id != 1:
                return
            FakeVideoFingerprintExtractor.mutate = None
            with self.database.connect() as conn:
                row = conn.execute(
                    """SELECT claimed_identity_json
                       FROM media_identity_scans WHERE id=?""",
                    (self.scan1.scan_id,),
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
                        self.scan1.scan_id,
                    ),
                )

        FakeVideoFingerprintExtractor.mutate = mutate

        with self.assertRaises(DeepFingerprintError):
            self.service.ensure_scan(self.scan1.scan_id)

        with self.database.connect() as conn:
            count = conn.execute(
                """SELECT COUNT(*) FROM media_identity_artifacts
                   WHERE file_id=1
                     AND artifact_type='content_fingerprint'"""
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_changed_media_bytes_cannot_reuse_old_scan_hash(self) -> None:
        first = self.service.ensure_scan(self.scan1.scan_id)
        self.assertIsNotNone(first.artifact_id)

        path = self.paths[1]
        path.write_bytes(path.read_bytes() + b"changed")

        result = self.service.ensure_scan(self.scan1.scan_id)

        self.assertIsNone(result.artifact_id)
        self.assertIsNone(result.fingerprint)
        self.assertIn("fingerprint-extraction", result.failure)

    def test_compare_current_files_uses_only_available_trusted_artifacts(self) -> None:
        values = (
            "0000000000000000",
            "1111111111111111",
            "2222222222222222",
            "3333333333333333",
            "4444444444444444",
            "5555555555555555",
        )
        FakeVideoFingerprintExtractor.values_by_file = {
            1: values,
            2: values,
        }
        self.service.ensure_scan(self.scan1.scan_id)
        self.service.ensure_scan(self.scan2.scan_id)

        matched = self.service.compare_current_files(
            1,
            [2, 3],
        )

        self.assertEqual(matched.requested_candidate_count, 2)
        self.assertEqual(matched.available_candidate_count, 1)
        self.assertEqual(matched.missing_file_ids, (3,))
        self.assertEqual(len(matched.comparisons), 1)
        self.assertEqual(matched.comparisons[0].right_file_id, 2)
        self.assertEqual(matched.comparisons[0].mean_similarity, 1.0)

    def test_file_only_peer_can_reuse_scan_bound_exact_hash(self) -> None:
        first = self.service.ensure_scan(self.scan2.scan_id)
        self.assertIsNotNone(first.artifact_id)

        current = self.service.load_current_file(2)

        self.assertIsNotNone(current)
        self.assertEqual(current.file_id, 2)


if __name__ == "__main__":
    unittest.main()

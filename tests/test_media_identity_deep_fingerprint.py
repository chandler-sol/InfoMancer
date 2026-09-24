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
    LocalFingerprintError,
    dhash64_from_gray9x8,
    plan_video_fingerprint_timestamps,
)
from app.media_identity.fingerprint_service import (
    DeepFingerprintArtifactService,
    DeepFingerprintError,
)
from app.media_identity.fingerprint_correlation import (
    DeepFingerprintCorrelationError,
    DeepFingerprintCorrelationService,
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

    def test_wrong_contract_version_is_rejected_even_with_valid_shape(self) -> None:
        original = _fingerprint(
            1,
            ["0" * 16] * 6,
            sha_char="a",
        )
        payload = original.persisted_payload()
        payload["identity"]["contract_version"] = 999
        payload["fingerprint_output_sha256"] = hashlib.sha256(
            json.dumps(
                {
                    "identity": payload["identity"],
                    "samples": payload["samples"],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()

        with self.assertRaisesRegex(
            FingerprintError,
            "contract version",
        ):
            fingerprint_from_payload(payload)

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

    def test_sampling_parameter_mismatch_is_incomparable(self) -> None:
        left = _fingerprint(
            1,
            ["0" * 16] * 6,
            sha_char="a",
        )
        right = ContentFingerprint(
            file_id=2,
            file_sha256="b" * 64,
            runtime_ms=left.runtime_ms,
            algorithm=left.algorithm,
            samples=left.samples,
            source_kind="local_ffmpeg",
            source_signature="source-2",
            parameters={"scale": "different"},
        )

        self.assertIsNone(compare_content_fingerprints(left, right))

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
    fail_files: set[int] = set()

    def __init__(self, media, runtime_ms) -> None:
        self.media = media
        self.runtime_ms = int(runtime_ms)
        self.timestamps = tuple(
            30_000 + index * 60_000
            for index in range(6)
        )
        stat = Path(media.path).stat()
        self._generation = (
            int(stat.st_size),
            int(getattr(stat, "st_mtime_ns", 0)),
        )
        self.source_signature = hashlib.sha256(
            (
                f"fake-fingerprint:{media.file_id}:"
                f"{media.sha256}:{self.runtime_ms}:"
                f"{self._generation[0]}:{self._generation[1]}"
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
                "fixture media generation changed"
            )
        if (
            int(current.st_size) != int(self.media.size_bytes)
            or (
                self.media.modified_at is not None
                and float(current.st_mtime) != float(self.media.modified_at)
            )
        ):
            raise LocalFingerprintError(
                "fixture media no longer matches catalog snapshot"
            )
        file_id = int(self.media.file_id)
        if file_id in self.__class__.fail_files:
            raise LocalFingerprintError(
                f"fixture fingerprint failure for file {file_id}"
            )
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
        FakeVideoFingerprintExtractor.fail_files = set()

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

    def test_sealed_artifact_reuse_does_not_require_extractor_availability(self) -> None:
        first = self.service.ensure_scan(self.scan1.scan_id)
        self.assertIsNotNone(first.artifact_id)

        def unavailable_factory(*_args, **_kwargs):
            raise AssertionError(
                "extractor factory should not be consulted for sealed cache reuse"
            )

        cache_only = DeepFingerprintArtifactService(
            self.database,
            extractor_factory=unavailable_factory,
        )
        second = cache_only.ensure_scan(self.scan1.scan_id)

        self.assertTrue(second.reused)
        self.assertEqual(second.artifact_id, first.artifact_id)
        self.assertIsNotNone(second.fingerprint)

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



class DeepFingerprintCorrelationServiceTests(
    DeepFingerprintArtifactServiceTests
):
    def setUp(self) -> None:
        super().setUp()
        self.correlation = DeepFingerprintCorrelationService(
            self.database,
            artifact_service=self.service,
        )

    def test_complete_cohort_persists_all_pairs_without_scan_mutation(self) -> None:
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
            3: (
                "ffffffffffffffff",
                "eeeeeeeeeeeeeeee",
                "dddddddddddddddd",
                "cccccccccccccccc",
                "bbbbbbbbbbbbbbbb",
                "aaaaaaaaaaaaaaaa",
            ),
        }
        with self.database.connect() as conn:
            before = dict(conn.execute(
                """SELECT requested_profile,completed_profile,stage,
                          claimed_identity_json,result_state,best_candidate_key
                   FROM media_identity_scans WHERE id=?""",
                (self.scan1.scan_id,),
            ).fetchone())

        result = self.correlation.run(self.scan1.scan_id)

        self.assertTrue(result.coverage_complete)
        self.assertEqual(result.planned_file_count, 3)
        self.assertEqual(result.completed_file_count, 3)
        self.assertEqual(result.planned_pair_count, 3)
        self.assertEqual(result.completed_pair_count, 3)
        self.assertIsNotNone(result.manifest_artifact_id)
        self.assertEqual(result.missing_file_ids, ())
        self.assertEqual(result.failures, ())
        self.assertEqual(
            {(item.left_file_id, item.right_file_id)
             for item in result.comparisons},
            {(1, 2), (1, 3), (2, 3)},
        )
        with self.database.connect() as conn:
            after = dict(conn.execute(
                """SELECT requested_profile,completed_profile,stage,
                          claimed_identity_json,result_state,best_candidate_key
                   FROM media_identity_scans WHERE id=?""",
                (self.scan1.scan_id,),
            ).fetchone())
            peer_hash = conn.execute(
                """SELECT status,sha256 FROM media_file_hashes
                   WHERE file_id=3"""
            ).fetchone()
        self.assertEqual(before, after)
        self.assertEqual(peer_hash["status"], "complete")
        self.assertEqual(len(peer_hash["sha256"]), 64)

    def test_second_cohort_run_reuses_all_fingerprints_and_manifest(self) -> None:
        first = self.correlation.run(self.scan1.scan_id)
        second = self.correlation.run(self.scan1.scan_id)

        self.assertTrue(first.coverage_complete)
        self.assertTrue(second.coverage_complete)
        self.assertEqual(
            second.manifest_artifact_id,
            first.manifest_artifact_id,
        )
        self.assertEqual(second.reused_fingerprint_count, 3)
        self.assertEqual(second.generated_fingerprint_count, 0)

    def test_one_failed_peer_keeps_matrix_non_authoritative(self) -> None:
        FakeVideoFingerprintExtractor.fail_files = {3}

        result = self.correlation.run(self.scan1.scan_id)

        self.assertFalse(result.coverage_complete)
        self.assertEqual(result.missing_file_ids, (3,))
        self.assertIsNone(result.manifest_artifact_id)
        self.assertEqual(result.completed_pair_count, 0)
        with self.database.connect() as conn:
            count = conn.execute(
                """SELECT COUNT(*) FROM media_identity_artifacts
                   WHERE file_id=1
                     AND artifact_type='deep_fingerprint_manifest'"""
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_target_revision_change_mid_cohort_blocks_manifest(self) -> None:
        changed = {"done": False}

        def mutate(file_id: int) -> None:
            if file_id != 2 or changed["done"]:
                return
            changed["done"] = True
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

        with self.assertRaises(DeepFingerprintCorrelationError):
            self.correlation.run(self.scan1.scan_id)

        with self.database.connect() as conn:
            count = conn.execute(
                """SELECT COUNT(*) FROM media_identity_artifacts
                   WHERE file_id=1
                     AND artifact_type='deep_fingerprint_manifest'"""
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_tampered_manifest_is_repaired_from_current_children(self) -> None:
        first = self.correlation.run(self.scan1.scan_id)
        self.assertTrue(first.coverage_complete)
        assert first.manifest_artifact_id is not None

        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT payload_json FROM media_identity_artifacts
                   WHERE id=?""",
                (first.manifest_artifact_id,),
            ).fetchone()
            payload = json.loads(row["payload_json"])
            payload["coverage_complete"] = False
            conn.execute(
                """UPDATE media_identity_artifacts
                   SET payload_json=? WHERE id=?""",
                (
                    json.dumps(payload, sort_keys=True),
                    first.manifest_artifact_id,
                ),
            )

        second = self.correlation.run(self.scan1.scan_id)

        self.assertTrue(second.coverage_complete)
        self.assertEqual(
            second.manifest_artifact_id,
            first.manifest_artifact_id,
        )
        with self.database.connect() as conn:
            repaired = json.loads(conn.execute(
                """SELECT payload_json FROM media_identity_artifacts
                   WHERE id=?""",
                (first.manifest_artifact_id,),
            ).fetchone()["payload_json"])
        self.assertTrue(repaired["coverage_complete"])

    def test_tampered_child_is_repaired_before_matrix_publication(self) -> None:
        first = self.correlation.run(self.scan1.scan_id)
        self.assertTrue(first.coverage_complete)

        with self.database.connect() as conn:
            child = conn.execute(
                """SELECT id,payload_json FROM media_identity_artifacts
                   WHERE file_id=2
                     AND artifact_type='content_fingerprint'
                   ORDER BY id DESC LIMIT 1"""
            ).fetchone()
            payload = json.loads(child["payload_json"])
            payload["samples"][0]["value"] = "f" * 16
            conn.execute(
                """UPDATE media_identity_artifacts
                   SET payload_json=? WHERE id=?""",
                (
                    json.dumps(payload, sort_keys=True),
                    int(child["id"]),
                ),
            )

        calls_before = FakeVideoFingerprintExtractor.extract_calls.get(2, 0)
        second = self.correlation.run(self.scan1.scan_id)

        self.assertTrue(second.coverage_complete)
        self.assertEqual(
            FakeVideoFingerprintExtractor.extract_calls.get(2, 0),
            calls_before + 1,
        )


if __name__ == "__main__":
    unittest.main()

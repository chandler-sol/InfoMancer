from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()

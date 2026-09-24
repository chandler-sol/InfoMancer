from __future__ import annotations

import unittest

from app.media_identity.correlation_interpretation import (
    CorrelationInterpretationError,
    CorrelationInterpretationPolicy,
    ModalityThresholds,
    MultimodalAgreement,
    SimilarityBand,
    interpret_matrix,
    interpret_modality,
    interpret_pair,
)
from app.media_identity.fingerprint import FingerprintComparison


def _comparison(
    left: int = 1,
    right: int = 2,
    *,
    algorithm_key: str = "fixture",
    algorithm_version: str = "1",
    compared_samples: int = 8,
    coverage: float = 1.0,
    mean: float = 0.95,
    median: float = 0.95,
    minimum: float = 0.70,
    shift: int = 0,
) -> FingerprintComparison:
    return FingerprintComparison(
        left_file_id=left,
        right_file_id=right,
        algorithm_key=algorithm_key,
        algorithm_version=algorithm_version,
        compared_samples=compared_samples,
        alignment_shift=shift,
        coverage=coverage,
        mean_similarity=mean,
        median_similarity=median,
        minimum_similarity=minimum,
    )


class CorrelationInterpretationPolicyTests(unittest.TestCase):
    def test_default_policy_is_versioned_and_separate_by_modality(self) -> None:
        policy = CorrelationInterpretationPolicy()
        payload = policy.identity_payload()

        self.assertEqual(payload["version"], 1)
        self.assertNotEqual(payload["video"], payload["audio"])
        self.assertEqual(payload["video"]["minimum_samples"], 6)
        self.assertEqual(payload["audio"]["minimum_samples"], 6)
        self.assertGreater(
            payload["audio"]["minimum_coverage"],
            payload["video"]["minimum_coverage"],
        )

    def test_thresholds_must_be_strictly_ordered(self) -> None:
        with self.assertRaisesRegex(
            CorrelationInterpretationError,
            "strictly ordered",
        ):
            ModalityThresholds(
                high_median=0.80,
                high_mean=0.80,
                moderate_median=0.80,
                moderate_mean=0.79,
                low_median=0.60,
                low_mean=0.60,
                minimum_coverage=0.50,
                minimum_samples=6,
            )

    def test_boolean_sample_limit_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            CorrelationInterpretationError,
            "sample count",
        ):
            ModalityThresholds(
                high_median=0.90,
                high_mean=0.88,
                moderate_median=0.82,
                moderate_mean=0.80,
                low_median=0.66,
                low_mean=0.68,
                minimum_coverage=0.50,
                minimum_samples=True,
            )


class ModalityInterpretationTests(unittest.TestCase):
    def test_video_exact_high_boundary_is_high(self) -> None:
        result = interpret_modality(
            _comparison(
                mean=0.88,
                median=0.90,
                coverage=0.50,
                compared_samples=6,
            ),
            modality="video",
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.band, SimilarityBand.HIGH)
        self.assertTrue(result.sufficient)
        self.assertTrue(result.strongly_supports_similarity)

    def test_audio_exact_high_boundary_is_high(self) -> None:
        result = interpret_modality(
            _comparison(
                mean=0.89,
                median=0.91,
                coverage=0.75,
                compared_samples=6,
            ),
            modality="audio",
        )

        self.assertEqual(result.band, SimilarityBand.HIGH)

    def test_moderate_requires_both_mean_and_median(self) -> None:
        result = interpret_modality(
            _comparison(
                mean=0.81,
                median=0.81,
                coverage=1.0,
            ),
            modality="video",
        )

        self.assertEqual(result.band, SimilarityBand.AMBIGUOUS)

    def test_exact_moderate_boundary_is_moderate(self) -> None:
        result = interpret_modality(
            _comparison(
                mean=0.80,
                median=0.82,
                coverage=0.50,
                compared_samples=6,
            ),
            modality="video",
        )

        self.assertEqual(result.band, SimilarityBand.MODERATE)
        self.assertTrue(result.supports_similarity)
        self.assertFalse(result.strongly_supports_similarity)

    def test_low_requires_both_mean_and_median(self) -> None:
        result = interpret_modality(
            _comparison(
                mean=0.67,
                median=0.70,
                coverage=1.0,
            ),
            modality="video",
        )

        self.assertEqual(result.band, SimilarityBand.AMBIGUOUS)

    def test_exact_low_boundary_is_low(self) -> None:
        result = interpret_modality(
            _comparison(
                mean=0.68,
                median=0.66,
                coverage=0.50,
                compared_samples=6,
            ),
            modality="video",
        )

        self.assertEqual(result.band, SimilarityBand.LOW)
        self.assertTrue(result.supports_difference)

    def test_insufficient_coverage_overrides_high_similarity(self) -> None:
        result = interpret_modality(
            _comparison(
                mean=1.0,
                median=1.0,
                coverage=0.49,
                compared_samples=8,
            ),
            modality="video",
        )

        self.assertEqual(result.band, SimilarityBand.INSUFFICIENT)
        self.assertFalse(result.sufficient)
        self.assertFalse(result.supports_similarity)

    def test_insufficient_sample_count_overrides_high_similarity(self) -> None:
        result = interpret_modality(
            _comparison(
                mean=1.0,
                median=1.0,
                coverage=1.0,
                compared_samples=5,
            ),
            modality="video",
        )

        self.assertEqual(result.band, SimilarityBand.INSUFFICIENT)

    def test_missing_measurement_remains_missing(self) -> None:
        self.assertIsNone(
            interpret_modality(
                None,
                modality="video",
            )
        )

    def test_unknown_modality_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            CorrelationInterpretationError,
            "video or audio",
        ):
            interpret_modality(
                _comparison(),
                modality="subtitle",
            )


class PairInterpretationTests(unittest.TestCase):
    def test_both_high_requires_two_high_modalities(self) -> None:
        result = interpret_pair(
            left_file_id=1,
            right_file_id=2,
            video=_comparison(
                algorithm_key="video",
                mean=0.95,
                median=0.95,
            ),
            audio=_comparison(
                algorithm_key="audio",
                mean=0.95,
                median=0.95,
            ),
        )

        self.assertEqual(
            result.agreement,
            MultimodalAgreement.BOTH_HIGH,
        )
        self.assertTrue(result.has_high_multimodal_support)
        self.assertFalse(result.contradictory)

    def test_high_plus_moderate_is_both_support_not_both_high(self) -> None:
        result = interpret_pair(
            left_file_id=1,
            right_file_id=2,
            video=_comparison(
                algorithm_key="video",
                mean=0.95,
                median=0.95,
            ),
            audio=_comparison(
                algorithm_key="audio",
                mean=0.83,
                median=0.85,
            ),
        )

        self.assertEqual(
            result.agreement,
            MultimodalAgreement.BOTH_SUPPORT,
        )
        self.assertFalse(result.has_high_multimodal_support)
        self.assertTrue(result.has_any_similarity_support)

    def test_both_low_is_not_a_same_content_verdict(self) -> None:
        result = interpret_pair(
            left_file_id=1,
            right_file_id=2,
            video=_comparison(
                algorithm_key="video",
                mean=0.50,
                median=0.50,
            ),
            audio=_comparison(
                algorithm_key="audio",
                mean=0.50,
                median=0.50,
            ),
        )

        self.assertEqual(
            result.agreement,
            MultimodalAgreement.BOTH_LOW,
        )
        self.assertFalse(result.has_any_similarity_support)

    def test_high_vs_low_is_explicitly_contradictory(self) -> None:
        result = interpret_pair(
            left_file_id=1,
            right_file_id=2,
            video=_comparison(
                algorithm_key="video",
                mean=0.95,
                median=0.95,
            ),
            audio=_comparison(
                algorithm_key="audio",
                mean=0.50,
                median=0.50,
            ),
        )

        self.assertEqual(
            result.agreement,
            MultimodalAgreement.CONTRADICTORY,
        )
        self.assertTrue(result.contradictory)
        self.assertFalse(result.has_high_multimodal_support)

    def test_high_vs_ambiguous_is_inconclusive_not_contradictory(self) -> None:
        result = interpret_pair(
            left_file_id=1,
            right_file_id=2,
            video=_comparison(
                algorithm_key="video",
                mean=0.95,
                median=0.95,
            ),
            audio=_comparison(
                algorithm_key="audio",
                mean=0.75,
                median=0.75,
            ),
        )

        self.assertEqual(
            result.agreement,
            MultimodalAgreement.INCONCLUSIVE,
        )
        self.assertFalse(result.contradictory)
        self.assertTrue(result.has_any_similarity_support)

    def test_one_sufficient_modality_is_single_modality(self) -> None:
        result = interpret_pair(
            left_file_id=1,
            right_file_id=2,
            video=_comparison(
                algorithm_key="video",
                mean=0.95,
                median=0.95,
            ),
            audio=None,
        )

        self.assertEqual(
            result.agreement,
            MultimodalAgreement.SINGLE_MODALITY,
        )

    def test_one_sufficient_and_one_insufficient_is_single_modality(self) -> None:
        result = interpret_pair(
            left_file_id=1,
            right_file_id=2,
            video=_comparison(
                algorithm_key="video",
                mean=0.95,
                median=0.95,
            ),
            audio=_comparison(
                algorithm_key="audio",
                mean=0.95,
                median=0.95,
                coverage=0.50,
            ),
        )

        self.assertEqual(
            result.agreement,
            MultimodalAgreement.SINGLE_MODALITY,
        )

    def test_two_insufficient_modalities_are_inconclusive(self) -> None:
        result = interpret_pair(
            left_file_id=1,
            right_file_id=2,
            video=_comparison(
                algorithm_key="video",
                mean=1.0,
                median=1.0,
                coverage=0.25,
            ),
            audio=_comparison(
                algorithm_key="audio",
                mean=1.0,
                median=1.0,
                coverage=0.50,
            ),
        )

        self.assertEqual(
            result.agreement,
            MultimodalAgreement.INCONCLUSIVE,
        )

    def test_reversed_measurement_orientation_matches_normalized_pair(self) -> None:
        result = interpret_pair(
            left_file_id=1,
            right_file_id=2,
            video=_comparison(
                left=2,
                right=1,
                algorithm_key="video",
            ),
            audio=None,
        )

        self.assertEqual(
            result.agreement,
            MultimodalAgreement.SINGLE_MODALITY,
        )
        self.assertEqual(
            frozenset(
                (
                    result.video.left_file_id,
                    result.video.right_file_id,
                )
            ),
            frozenset((1, 2)),
        )

    def test_boolean_pair_id_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            CorrelationInterpretationError,
            "distinct positive file IDs",
        ):
            interpret_pair(
                left_file_id=True,
                right_file_id=2,
                video=None,
                audio=None,
            )


class MatrixInterpretationTests(unittest.TestCase):
    def test_matrix_unions_modalities_by_unordered_pair(self) -> None:
        results = interpret_matrix(
            video=[
                _comparison(
                    left=2,
                    right=1,
                    algorithm_key="video",
                ),
                _comparison(
                    left=2,
                    right=3,
                    algorithm_key="video",
                    mean=0.50,
                    median=0.50,
                ),
            ],
            audio=[
                _comparison(
                    left=1,
                    right=2,
                    algorithm_key="audio",
                ),
            ],
        )

        self.assertEqual(
            [(item.left_file_id, item.right_file_id) for item in results],
            [(1, 2), (2, 3)],
        )
        self.assertEqual(
            results[0].agreement,
            MultimodalAgreement.BOTH_HIGH,
        )
        self.assertEqual(
            results[1].agreement,
            MultimodalAgreement.SINGLE_MODALITY,
        )

    def test_duplicate_pair_in_one_modality_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            CorrelationInterpretationError,
            "duplicate pair",
        ):
            interpret_matrix(
                video=[
                    _comparison(left=1, right=2),
                    _comparison(left=2, right=1),
                ],
                audio=[],
            )

    def test_invalid_matrix_record_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            CorrelationInterpretationError,
            "invalid record",
        ):
            interpret_matrix(
                video=[object()],
                audio=[],
            )


if __name__ == "__main__":
    unittest.main()

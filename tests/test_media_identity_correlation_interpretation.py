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
from app.media_identity.fingerprint import (
    AUDIO_ENVELOPE_DHASH64_V1,
    VIDEO_DHASH64_V1,
    FingerprintComparison,
)
from app.media_identity.fingerprint_bundle import DeepFingerprintBundleRun
from app.media_identity.fingerprint_correlation import (
    DeepFingerprintCorrelationRun,
)
from app.media_identity.correlation_interpretation_service import (
    interpret_fingerprint_bundle,
)


def _comparison(
    left: int = 1,
    right: int = 2,
    *,
    algorithm_key: str = VIDEO_DHASH64_V1.key,
    algorithm_version: str = VIDEO_DHASH64_V1.version,
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

    def test_modality_rejects_wrong_fingerprint_algorithm(self) -> None:
        with self.assertRaisesRegex(
            CorrelationInterpretationError,
            "wrong fingerprint algorithm",
        ):
            interpret_modality(
                _comparison(
                    algorithm_key=AUDIO_ENVELOPE_DHASH64_V1.key,
                    algorithm_version=AUDIO_ENVELOPE_DHASH64_V1.version,
                ),
                modality="video",
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
            (result.left_file_id, result.right_file_id),
            (1, 2),
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

    def test_reversed_requested_pair_normalizes_output_identity(self) -> None:
        result = interpret_pair(
            left_file_id=2,
            right_file_id=1,
            video=_comparison(
                left=1,
                right=2,
                algorithm_key=VIDEO_DHASH64_V1.key,
                algorithm_version=VIDEO_DHASH64_V1.version,
            ),
            audio=None,
        )

        self.assertEqual(
            (result.left_file_id, result.right_file_id),
            (1, 2),
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




def _correlation_run(
    *,
    algorithm_key: str,
    comparisons: tuple[FingerprintComparison, ...],
    signature: str = "a" * 64,
    coverage_complete: bool = True,
    planned_file_count: int = 2,
    planned_pair_count: int = 1,
) -> DeepFingerprintCorrelationRun:
    return DeepFingerprintCorrelationRun(
        scan_id=11,
        algorithm_key=algorithm_key,
        correlation_plan_signature=signature,
        planned_file_count=planned_file_count,
        completed_file_count=(
            planned_file_count if coverage_complete else 1
        ),
        planned_pair_count=planned_pair_count,
        completed_pair_count=len(comparisons),
        reused_fingerprint_count=0,
        generated_fingerprint_count=planned_file_count,
        manifest_artifact_id=(101 if coverage_complete else None),
        coverage_complete=coverage_complete,
        missing_file_ids=(() if coverage_complete else (2,)),
        comparisons=comparisons,
        failures=(() if coverage_complete else ("missing-peer",)),
    )


def _bundle(
    *,
    video: DeepFingerprintCorrelationRun | None = None,
    audio: DeepFingerprintCorrelationRun | None = None,
    signature: str = "a" * 64,
) -> DeepFingerprintBundleRun:
    video = video or _correlation_run(
        algorithm_key=VIDEO_DHASH64_V1.key,
        comparisons=(
            _comparison(
                algorithm_key=VIDEO_DHASH64_V1.key,
                algorithm_version=VIDEO_DHASH64_V1.version,
            ),
        ),
        signature=signature,
    )
    audio = audio or _correlation_run(
        algorithm_key=AUDIO_ENVELOPE_DHASH64_V1.key,
        comparisons=(
            _comparison(
                algorithm_key=AUDIO_ENVELOPE_DHASH64_V1.key,
                algorithm_version=AUDIO_ENVELOPE_DHASH64_V1.version,
            ),
        ),
        signature=signature,
    )
    return DeepFingerprintBundleRun(
        scan_id=11,
        result_revision=7,
        correlation_plan_signature=signature,
        video=video,
        audio=audio,
    )


class CorrelationInterpretationServiceTests(unittest.TestCase):
    def test_bundle_interpretation_is_bound_to_policy_revision_and_plan(self) -> None:
        result = interpret_fingerprint_bundle(_bundle())

        self.assertEqual(result.scan_id, 11)
        self.assertEqual(result.result_revision, 7)
        self.assertEqual(result.correlation_plan_signature, "a" * 64)
        self.assertEqual(result.interpretation_version, 1)
        self.assertEqual(len(result.policy_signature), 64)
        self.assertEqual(result.complete_modalities, ("video", "audio"))
        self.assertTrue(result.fully_multimodal)
        self.assertEqual(result.planned_pair_count, 1)
        self.assertEqual(result.pair_count, 1)
        self.assertEqual(result.both_high_count, 1)
        self.assertEqual(result.contradiction_count, 0)

    def test_policy_identity_is_deeply_immutable(self) -> None:
        result = interpret_fingerprint_bundle(_bundle())

        with self.assertRaises(TypeError):
            result.policy_identity["video"]["high_mean"] = 0.1

    def test_wrong_video_algorithm_is_rejected(self) -> None:
        bundle = _bundle(
            video=_correlation_run(
                algorithm_key=AUDIO_ENVELOPE_DHASH64_V1.key,
                comparisons=(),
            )
        )

        with self.assertRaisesRegex(
            CorrelationInterpretationError,
            "wrong fingerprint algorithm",
        ):
            interpret_fingerprint_bundle(bundle)

    def test_wrong_audio_algorithm_is_rejected(self) -> None:
        bundle = _bundle(
            audio=_correlation_run(
                algorithm_key=VIDEO_DHASH64_V1.key,
                comparisons=(),
            )
        )

        with self.assertRaisesRegex(
            CorrelationInterpretationError,
            "wrong fingerprint algorithm",
        ):
            interpret_fingerprint_bundle(bundle)

    def test_mismatched_correlation_plan_signatures_are_rejected(self) -> None:
        bundle = _bundle(
            audio=_correlation_run(
                algorithm_key=AUDIO_ENVELOPE_DHASH64_V1.key,
                comparisons=(),
                signature="b" * 64,
            )
        )

        with self.assertRaisesRegex(
            CorrelationInterpretationError,
            "same scan and correlation plan",
        ):
            interpret_fingerprint_bundle(bundle)

    def test_mismatched_planned_cohort_sizes_are_rejected(self) -> None:
        bundle = _bundle(
            audio=_correlation_run(
                algorithm_key=AUDIO_ENVELOPE_DHASH64_V1.key,
                comparisons=(),
                planned_file_count=3,
                planned_pair_count=3,
            )
        )

        with self.assertRaisesRegex(
            CorrelationInterpretationError,
            "planned correlation cohort",
        ):
            interpret_fingerprint_bundle(bundle)

    def test_incomplete_audio_remains_single_modality_without_fabrication(self) -> None:
        audio = _correlation_run(
            algorithm_key=AUDIO_ENVELOPE_DHASH64_V1.key,
            comparisons=(),
            coverage_complete=False,
        )
        result = interpret_fingerprint_bundle(
            _bundle(audio=audio)
        )

        self.assertEqual(result.complete_modalities, ("video",))
        self.assertFalse(result.fully_multimodal)
        self.assertEqual(result.pair_count, 1)
        self.assertEqual(
            result.pairs[0].agreement,
            MultimodalAgreement.SINGLE_MODALITY,
        )

    def test_contradictory_bundle_counts_contradiction_without_verdict(self) -> None:
        audio = _correlation_run(
            algorithm_key=AUDIO_ENVELOPE_DHASH64_V1.key,
            comparisons=(
                _comparison(
                    algorithm_key=AUDIO_ENVELOPE_DHASH64_V1.key,
                    algorithm_version=AUDIO_ENVELOPE_DHASH64_V1.version,
                    mean=0.50,
                    median=0.50,
                ),
            ),
        )
        result = interpret_fingerprint_bundle(
            _bundle(audio=audio)
        )

        self.assertEqual(result.contradiction_count, 1)
        self.assertEqual(result.both_high_count, 0)
        self.assertTrue(result.pairs[0].contradictory)

    def test_invalid_bundle_plan_signature_fails_closed(self) -> None:
        bundle = _bundle(signature="not-a-sha")

        with self.assertRaisesRegex(
            CorrelationInterpretationError,
            "valid correlation-plan signature",
        ):
            interpret_fingerprint_bundle(bundle)

if __name__ == "__main__":
    unittest.main()

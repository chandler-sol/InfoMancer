from __future__ import annotations

import unittest

from app.media_identity.correlation_interpretation import (
    CorrelationInterpretationPolicy,
    MultimodalAgreement,
    interpret_pair,
)
from app.media_identity.correlation_patterns import (
    CorrelationPatternError,
    DuplicatePatternStrength,
    SwapPatternObservation,
    SwapPatternStatus,
    detect_correlation_patterns,
)
from app.media_identity.fingerprint import (
    AUDIO_ENVELOPE_DHASH64_V1,
    VIDEO_DHASH64_V1,
    FingerprintComparison,
)
from app.media_identity.models import IdentityResultState
from app.media_identity.sequence_correlation import (
    SequenceHypothesis,
    SequenceOffsetPolicy,
)


def _comparison(
    left: int,
    right: int,
    *,
    modality: str,
    mean: float,
    median: float,
    coverage: float = 1.0,
) -> FingerprintComparison:
    algorithm = (
        VIDEO_DHASH64_V1
        if modality == "video"
        else AUDIO_ENVELOPE_DHASH64_V1
    )
    return FingerprintComparison(
        left_file_id=left,
        right_file_id=right,
        algorithm_key=algorithm.key,
        algorithm_version=algorithm.version,
        compared_samples=8,
        alignment_shift=0,
        coverage=coverage,
        mean_similarity=mean,
        median_similarity=median,
        minimum_similarity=min(mean, median),
    )


def _pair(
    left: int,
    right: int,
    *,
    video: tuple[float, float] | None,
    audio: tuple[float, float] | None,
):
    return interpret_pair(
        left_file_id=left,
        right_file_id=right,
        video=(
            None
            if video is None
            else _comparison(
                left,
                right,
                modality="video",
                mean=video[0],
                median=video[1],
            )
        ),
        audio=(
            None
            if audio is None
            else _comparison(
                left,
                right,
                modality="audio",
                mean=audio[0],
                median=audio[1],
            )
        ),
        policy=CorrelationInterpretationPolicy(),
    )


def _hypothesis(
    file_id: int,
    claimed_episode: int,
    hypothesis_episode: int,
    *,
    season: int = 1,
    support: float = 0.75,
    conflict: float = 0.10,
    margin: float = 0.20,
    categories: int = 2,
    content_support: bool = True,
    state: IdentityResultState = IdentityResultState.LIKELY_MISMATCH,
) -> SequenceHypothesis:
    return SequenceHypothesis(
        file_id=file_id,
        scan_id=100 + file_id,
        result_revision=3,
        claimed_season=season,
        claimed_episode=claimed_episode,
        claimed_episode_end=claimed_episode,
        candidate_key=f"candidate:{file_id}",
        hypothesis_season=season,
        hypothesis_episode=hypothesis_episode,
        result_state=state,
        support_strength=support,
        conflict_strength=conflict,
        margin=margin,
        independent_categories=categories,
        content_support=content_support,
    )


class DuplicatePatternTests(unittest.TestCase):
    def test_both_high_is_strong_duplicate(self) -> None:
        analysis = detect_correlation_patterns(
            pairs=[
                _pair(
                    1,
                    2,
                    video=(0.95, 0.95),
                    audio=(0.95, 0.95),
                )
            ],
            hypotheses=[],
        )

        self.assertEqual(len(analysis.duplicate_observations), 1)
        duplicate = analysis.duplicate_observations[0]
        self.assertEqual(
            duplicate.strength,
            DuplicatePatternStrength.STRONG_MULTIMODAL,
        )
        self.assertEqual(
            duplicate.agreement,
            MultimodalAgreement.BOTH_HIGH,
        )
        self.assertEqual(
            analysis.strong_duplicates,
            analysis.duplicate_observations,
        )

    def test_both_support_is_non_strong_duplicate_observation(self) -> None:
        analysis = detect_correlation_patterns(
            pairs=[
                _pair(
                    1,
                    2,
                    video=(0.83, 0.83),
                    audio=(0.85, 0.85),
                )
            ],
            hypotheses=[],
        )

        self.assertEqual(len(analysis.duplicate_observations), 1)
        self.assertEqual(
            analysis.duplicate_observations[0].strength,
            DuplicatePatternStrength.SUPPORTED_MULTIMODAL,
        )
        self.assertEqual(analysis.strong_duplicates, ())

    def test_single_modality_similarity_does_not_create_duplicate(self) -> None:
        analysis = detect_correlation_patterns(
            pairs=[
                _pair(
                    1,
                    2,
                    video=(0.95, 0.95),
                    audio=None,
                )
            ],
            hypotheses=[],
        )

        self.assertEqual(analysis.duplicate_observations, ())


class SwapPatternTests(unittest.TestCase):
    def test_reciprocal_hypotheses_plus_both_low_is_corroborated_swap(self) -> None:
        analysis = detect_correlation_patterns(
            pairs=[
                _pair(
                    1,
                    2,
                    video=(0.50, 0.50),
                    audio=(0.50, 0.50),
                )
            ],
            hypotheses=[
                _hypothesis(1, 1, 2),
                _hypothesis(2, 2, 1),
            ],
        )

        self.assertEqual(len(analysis.swap_observations), 1)
        swap = analysis.swap_observations[0]
        self.assertEqual(
            swap.status,
            SwapPatternStatus.CORROBORATED_DISTINCT,
        )
        self.assertEqual(
            swap.fingerprint_agreement,
            MultimodalAgreement.BOTH_LOW,
        )
        self.assertFalse(swap.fingerprint_similarity_support)
        self.assertEqual(analysis.possible_swaps, (swap,))

    def test_reciprocal_hypotheses_without_pair_are_hypothesis_only(self) -> None:
        analysis = detect_correlation_patterns(
            pairs=[],
            hypotheses=[
                _hypothesis(1, 1, 2),
                _hypothesis(2, 2, 1),
            ],
        )

        swap = analysis.swap_observations[0]
        self.assertEqual(
            swap.status,
            SwapPatternStatus.HYPOTHESIS_ONLY,
        )
        self.assertIsNone(swap.fingerprint_agreement)
        self.assertEqual(analysis.possible_swaps, (swap,))

    def test_same_content_support_conflicts_with_swap(self) -> None:
        analysis = detect_correlation_patterns(
            pairs=[
                _pair(
                    1,
                    2,
                    video=(0.95, 0.95),
                    audio=(0.95, 0.95),
                )
            ],
            hypotheses=[
                _hypothesis(1, 1, 2),
                _hypothesis(2, 2, 1),
            ],
        )

        self.assertEqual(len(analysis.strong_duplicates), 1)
        swap = analysis.swap_observations[0]
        self.assertEqual(
            swap.status,
            SwapPatternStatus.CONFLICTED_SIMILARITY,
        )
        self.assertTrue(swap.fingerprint_similarity_support)
        self.assertEqual(analysis.possible_swaps, ())

    def test_contradictory_fingerprints_conflict_with_swap(self) -> None:
        analysis = detect_correlation_patterns(
            pairs=[
                _pair(
                    1,
                    2,
                    video=(0.95, 0.95),
                    audio=(0.50, 0.50),
                )
            ],
            hypotheses=[
                _hypothesis(1, 1, 2),
                _hypothesis(2, 2, 1),
            ],
        )

        swap = analysis.swap_observations[0]
        self.assertEqual(
            swap.status,
            SwapPatternStatus.CONFLICTED_MODALITIES,
        )
        self.assertEqual(
            swap.fingerprint_agreement,
            MultimodalAgreement.CONTRADICTORY,
        )
        self.assertEqual(analysis.possible_swaps, ())

    def test_one_way_identity_hypothesis_is_not_swap(self) -> None:
        analysis = detect_correlation_patterns(
            pairs=[],
            hypotheses=[
                _hypothesis(1, 1, 2),
                _hypothesis(
                    2,
                    2,
                    2,
                    state=IdentityResultState.VERIFIED,
                    support=0.90,
                    conflict=0.02,
                    margin=0.30,
                ),
            ],
        )

        self.assertEqual(analysis.swap_observations, ())

    def test_weak_reciprocal_hypothesis_cannot_form_swap(self) -> None:
        analysis = detect_correlation_patterns(
            pairs=[],
            hypotheses=[
                _hypothesis(1, 1, 2),
                _hypothesis(
                    2,
                    2,
                    1,
                    support=0.20,
                ),
            ],
        )

        self.assertEqual(analysis.swap_observations, ())

    def test_swap_observation_rejects_fake_corroboration(self) -> None:
        with self.assertRaisesRegex(
            CorrelationPatternError,
            "multimodal difference support",
        ):
            SwapPatternObservation(
                left_file_id=1,
                right_file_id=2,
                left_claimed=(1, 1),
                right_claimed=(1, 2),
                left_hypothesis=(1, 2),
                right_hypothesis=(1, 1),
                status=SwapPatternStatus.CORROBORATED_DISTINCT,
                fingerprint_agreement=MultimodalAgreement.BOTH_HIGH,
                fingerprint_similarity_support=True,
            )


class CyclePatternTests(unittest.TestCase):
    def test_three_file_rotation_is_cycle_not_pairwise_swaps(self) -> None:
        analysis = detect_correlation_patterns(
            pairs=[],
            hypotheses=[
                _hypothesis(1, 1, 2),
                _hypothesis(2, 2, 3),
                _hypothesis(3, 3, 1),
            ],
        )

        self.assertEqual(analysis.swap_observations, ())
        self.assertEqual(len(analysis.identity_cycles), 1)
        cycle = analysis.identity_cycles[0]
        self.assertEqual(cycle.file_ids, (1, 2, 3))
        self.assertEqual(cycle.target_file_ids, (2, 3, 1))
        self.assertEqual(
            set(cycle.target_file_ids),
            set(cycle.file_ids),
        )

    def test_four_file_rotation_is_cycle_not_multiple_swaps(self) -> None:
        analysis = detect_correlation_patterns(
            pairs=[],
            hypotheses=[
                _hypothesis(1, 1, 2),
                _hypothesis(2, 2, 3),
                _hypothesis(3, 3, 4),
                _hypothesis(4, 4, 1),
            ],
        )

        self.assertEqual(analysis.swap_observations, ())
        self.assertEqual(
            analysis.identity_cycles[0].file_ids,
            (1, 2, 3, 4),
        )


class AmbiguousClaimTests(unittest.TestCase):
    def test_duplicate_claim_coordinates_block_swap_ownership(self) -> None:
        analysis = detect_correlation_patterns(
            pairs=[],
            hypotheses=[
                _hypothesis(1, 1, 2),
                _hypothesis(2, 2, 1),
                _hypothesis(3, 2, 1),
            ],
        )

        self.assertEqual(analysis.swap_observations, ())
        self.assertEqual(
            analysis.ambiguous_claim_file_ids,
            (2, 3),
        )

    def test_duplicate_pair_interpretations_are_rejected(self) -> None:
        pair = _pair(
            1,
            2,
            video=(0.50, 0.50),
            audio=(0.50, 0.50),
        )
        with self.assertRaisesRegex(
            CorrelationPatternError,
            "duplicate file pair",
        ):
            detect_correlation_patterns(
                pairs=[pair, pair],
                hypotheses=[],
            )


if __name__ == "__main__":
    unittest.main()

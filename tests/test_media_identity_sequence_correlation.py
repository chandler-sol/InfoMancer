from __future__ import annotations

import unittest

from app.media_identity.models import IdentityResultState
from app.media_identity.sequence_correlation import (
    SequenceCorrelationError,
    SequenceHypothesis,
    SequenceOffsetAnalysis,
    SequenceOffsetPolicy,
    detect_sequence_offsets,
)


def _hypothesis(
    file_id: int,
    claimed_episode: int,
    hypothesis_episode: int,
    *,
    season: int = 1,
    hypothesis_season: int | None = None,
    episode_end: int | None = None,
    state: IdentityResultState = IdentityResultState.LIKELY_MISMATCH,
    support: float = 0.70,
    conflict: float = 0.10,
    margin: float = 0.20,
    categories: int = 2,
    content_support: bool = True,
) -> SequenceHypothesis:
    return SequenceHypothesis(
        file_id=file_id,
        scan_id=100 + file_id,
        result_revision=3,
        claimed_season=season,
        claimed_episode=claimed_episode,
        claimed_episode_end=(
            claimed_episode
            if episode_end is None
            else episode_end
        ),
        candidate_key=f"candidate:{file_id}",
        hypothesis_season=(
            season
            if hypothesis_season is None
            else hypothesis_season
        ),
        hypothesis_episode=hypothesis_episode,
        result_state=state,
        support_strength=support,
        conflict_strength=conflict,
        margin=margin,
        independent_categories=categories,
        content_support=content_support,
    )


class SequenceOffsetPolicyTests(unittest.TestCase):
    def test_default_policy_is_conservative(self) -> None:
        policy = SequenceOffsetPolicy()
        payload = policy.identity_payload()

        self.assertEqual(payload["version"], 1)
        self.assertEqual(policy.minimum_files, 3)
        self.assertEqual(policy.minimum_support_ratio, 0.75)
        self.assertEqual(policy.maximum_claim_gap, 2)
        self.assertEqual(policy.max_abs_offset, 3)
        self.assertIn(
            IdentityResultState.INCONCLUSIVE.value,
            payload["eligible_states"],
        )
        self.assertNotIn(
            IdentityResultState.EPISODE_ORDER_CONFLICT.value,
            payload["eligible_states"],
        )

    def test_support_ratio_below_majority_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            SequenceCorrelationError,
            "between 0.50 and 1",
        ):
            SequenceOffsetPolicy(
                minimum_support_ratio=0.49,
            )

    def test_boolean_limits_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            SequenceCorrelationError,
            "maximum offset",
        ):
            SequenceOffsetPolicy(max_abs_offset=True)


class SequenceHypothesisTests(unittest.TestCase):
    def test_offset_requires_same_season_single_episode(self) -> None:
        multi = _hypothesis(
            1,
            1,
            2,
            episode_end=2,
        )
        cross_season = _hypothesis(
            2,
            1,
            2,
            hypothesis_season=2,
        )

        self.assertIsNone(multi.offset)
        self.assertIsNone(cross_season.offset)

    def test_alternate_order_conflict_is_not_sequence_usable(self) -> None:
        item = _hypothesis(
            1,
            1,
            2,
            state=IdentityResultState.EPISODE_ORDER_CONFLICT,
        )

        self.assertFalse(item.usable(SequenceOffsetPolicy()))

    def test_content_support_is_required(self) -> None:
        item = _hypothesis(
            1,
            1,
            2,
            content_support=False,
        )

        self.assertFalse(item.usable(SequenceOffsetPolicy()))

    def test_low_margin_is_excluded(self) -> None:
        item = _hypothesis(
            1,
            1,
            2,
            state=IdentityResultState.INCONCLUSIVE,
            margin=0.09,
        )

        self.assertFalse(item.usable(SequenceOffsetPolicy()))


class SequenceAnalysisValidationTests(unittest.TestCase):
    def test_analysis_rejects_inconsistent_excluded_count(self) -> None:
        with self.assertRaisesRegex(
            SequenceCorrelationError,
            "excluded-file count",
        ):
            SequenceOffsetAnalysis(
                policy=SequenceOffsetPolicy(),
                hypothesis_count=3,
                usable_count=2,
                excluded_file_ids=(),
                observations=(),
                conflicted_seasons=(),
            )


class SequenceOffsetDetectionTests(unittest.TestCase):
    def test_three_of_four_plus_one_hypotheses_qualify(self) -> None:
        analysis = detect_sequence_offsets([
            _hypothesis(1, 1, 2),
            _hypothesis(2, 2, 3),
            _hypothesis(3, 3, 4),
            _hypothesis(
                4,
                4,
                4,
                state=IdentityResultState.VERIFIED,
                support=0.85,
                conflict=0.05,
                margin=0.30,
            ),
        ])

        self.assertEqual(analysis.usable_count, 4)
        self.assertEqual(len(analysis.observations), 1)
        item = analysis.observations[0]
        self.assertEqual(item.season, 1)
        self.assertEqual(item.offset, 1)
        self.assertEqual(item.support_count, 3)
        self.assertEqual(item.usable_count, 4)
        self.assertEqual(item.support_ratio, 0.75)
        self.assertEqual(item.longest_chain, 3)
        self.assertEqual(
            analysis.authoritative_observations,
            analysis.observations,
        )

    def test_zero_offset_anchors_can_defeat_false_shift(self) -> None:
        hypotheses = [
            _hypothesis(1, 1, 2),
            _hypothesis(2, 2, 3),
            _hypothesis(3, 3, 4),
        ]
        hypotheses.extend(
            _hypothesis(
                file_id,
                episode,
                episode,
                state=IdentityResultState.VERIFIED,
                support=0.90,
                conflict=0.02,
                margin=0.30,
            )
            for file_id, episode in zip(
                range(4, 11),
                range(4, 11),
            )
        )

        analysis = detect_sequence_offsets(hypotheses)

        self.assertEqual(analysis.usable_count, 10)
        self.assertEqual(analysis.observations, ())

    def test_one_missing_episode_does_not_break_coherent_chain(self) -> None:
        analysis = detect_sequence_offsets([
            _hypothesis(1, 1, 2),
            _hypothesis(2, 2, 3),
            _hypothesis(3, 4, 5),
        ])

        self.assertEqual(len(analysis.observations), 1)
        self.assertEqual(
            analysis.observations[0].longest_chain,
            3,
        )

    def test_two_missing_episodes_break_chain(self) -> None:
        analysis = detect_sequence_offsets([
            _hypothesis(1, 1, 2),
            _hypothesis(2, 2, 3),
            _hypothesis(3, 5, 6),
        ])

        self.assertEqual(analysis.observations, ())

    def test_specials_do_not_participate(self) -> None:
        analysis = detect_sequence_offsets([
            _hypothesis(1, 1, 2, season=0),
            _hypothesis(2, 2, 3, season=0),
            _hypothesis(3, 3, 4, season=0),
        ])

        self.assertEqual(analysis.usable_count, 0)
        self.assertEqual(analysis.observations, ())
        self.assertEqual(analysis.excluded_file_ids, (1, 2, 3))

    def test_multi_episode_files_do_not_participate(self) -> None:
        analysis = detect_sequence_offsets([
            _hypothesis(1, 1, 2, episode_end=2),
            _hypothesis(2, 2, 3, episode_end=3),
            _hypothesis(3, 3, 4, episode_end=4),
        ])

        self.assertEqual(analysis.usable_count, 0)

    def test_cross_season_hypotheses_count_against_same_season_shift(self) -> None:
        analysis = detect_sequence_offsets([
            _hypothesis(1, 1, 1, hypothesis_season=2),
            _hypothesis(2, 2, 2, hypothesis_season=2),
            _hypothesis(3, 3, 3, hypothesis_season=2),
        ])

        self.assertEqual(analysis.usable_count, 3)
        self.assertEqual(analysis.observations, ())

    def test_cross_season_competitors_can_defeat_false_shift(self) -> None:
        analysis = detect_sequence_offsets([
            _hypothesis(1, 1, 2),
            _hypothesis(2, 2, 3),
            _hypothesis(3, 3, 4),
            _hypothesis(4, 4, 4, hypothesis_season=2),
            _hypothesis(5, 5, 5, hypothesis_season=2),
        ])

        self.assertEqual(analysis.usable_count, 5)
        self.assertEqual(analysis.observations, ())

    def test_out_of_bound_offset_counts_against_supported_ratio(self) -> None:
        analysis = detect_sequence_offsets([
            _hypothesis(1, 1, 2),
            _hypothesis(2, 2, 3),
            _hypothesis(3, 3, 4),
            _hypothesis(4, 4, 10),
        ])

        self.assertEqual(analysis.usable_count, 4)
        self.assertEqual(len(analysis.observations), 1)
        self.assertEqual(
            analysis.observations[0].support_ratio,
            0.75,
        )

    def test_weak_hypothesis_is_excluded_not_counted_as_anchor(self) -> None:
        analysis = detect_sequence_offsets([
            _hypothesis(1, 1, 2),
            _hypothesis(2, 2, 3),
            _hypothesis(3, 3, 4),
            _hypothesis(
                4,
                4,
                4,
                support=0.20,
                state=IdentityResultState.INCONCLUSIVE,
            ),
        ])

        self.assertEqual(analysis.usable_count, 3)
        self.assertEqual(analysis.excluded_file_ids, (4,))
        self.assertEqual(
            analysis.observations[0].support_ratio,
            1.0,
        )

    def test_negative_offset_is_supported_symmetrically(self) -> None:
        analysis = detect_sequence_offsets([
            _hypothesis(1, 2, 1),
            _hypothesis(2, 3, 2),
            _hypothesis(3, 4, 3),
        ])

        self.assertEqual(len(analysis.observations), 1)
        self.assertEqual(analysis.observations[0].offset, -1)

    def test_competing_offsets_in_same_season_fail_authoritative(self) -> None:
        policy = SequenceOffsetPolicy(
            minimum_support_ratio=0.50,
        )
        analysis = detect_sequence_offsets([
            _hypothesis(1, 1, 2),
            _hypothesis(2, 2, 3),
            _hypothesis(3, 3, 4),
            _hypothesis(4, 4, 6),
            _hypothesis(5, 5, 7),
            _hypothesis(6, 6, 8),
        ], policy=policy)

        self.assertEqual(len(analysis.observations), 2)
        self.assertEqual(analysis.conflicted_seasons, (1,))
        self.assertEqual(
            analysis.authoritative_observations,
            (),
        )
        self.assertTrue(
            all(item.conflicted for item in analysis.observations)
        )

    def test_duplicate_file_ids_are_rejected(self) -> None:
        with self.assertRaisesRegex(
            SequenceCorrelationError,
            "unique file IDs",
        ):
            detect_sequence_offsets([
                _hypothesis(1, 1, 2),
                _hypothesis(1, 2, 3),
                _hypothesis(3, 3, 4),
            ])


if __name__ == "__main__":
    unittest.main()

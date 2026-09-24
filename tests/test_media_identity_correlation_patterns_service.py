from __future__ import annotations

import hashlib
import json
import unittest

from app.media_identity.correlation_interpretation import (
    CorrelationInterpretationPolicy,
    interpret_pair,
)
from app.media_identity.correlation_interpretation_service import (
    DeepCorrelationInterpretationRun,
)
from app.media_identity.correlation_patterns_service import (
    DeepCorrelationPatternError,
    correlate_deep_patterns,
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
    detect_sequence_offsets,
)
from app.media_identity.sequence_correlation_service import (
    DeepSequenceCorrelationRun,
)


def _canonical(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _comparison(
    left: int,
    right: int,
    *,
    modality: str,
    mean: float,
    median: float,
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
        coverage=1.0,
        mean_similarity=mean,
        median_similarity=median,
        minimum_similarity=min(mean, median),
    )


def _pair(
    left: int,
    right: int,
    *,
    video: tuple[float, float],
    audio: tuple[float, float],
):
    return interpret_pair(
        left_file_id=left,
        right_file_id=right,
        video=_comparison(
            left,
            right,
            modality="video",
            mean=video[0],
            median=video[1],
        ),
        audio=_comparison(
            left,
            right,
            modality="audio",
            mean=audio[0],
            median=audio[1],
        ),
    )


def _hypothesis(
    file_id: int,
    claimed: int,
    actual: int,
) -> SequenceHypothesis:
    return SequenceHypothesis(
        file_id=file_id,
        scan_id=100 + file_id,
        result_revision=7,
        claimed_season=1,
        claimed_episode=claimed,
        claimed_episode_end=claimed,
        candidate_key=f"candidate:{file_id}",
        hypothesis_season=1,
        hypothesis_episode=actual,
        result_state=IdentityResultState.LIKELY_MISMATCH,
        support_strength=0.75,
        conflict_strength=0.10,
        margin=0.20,
        independent_categories=2,
        content_support=True,
    )


def _sequence(
    hypotheses: tuple[SequenceHypothesis, ...],
) -> DeepSequenceCorrelationRun:
    policy = SequenceOffsetPolicy()
    analysis = detect_sequence_offsets(
        hypotheses,
        policy=policy,
    )
    payload = policy.identity_payload()
    signature = hashlib.sha256(_canonical(payload)).hexdigest()
    return DeepSequenceCorrelationRun(
        scan_id=11,
        target_file_id=hypotheses[0].file_id,
        result_revision=7,
        correlation_plan_signature="a" * 64,
        sequence_policy_signature=signature,
        sequence_policy_identity=payload,
        planned_file_count=len(hypotheses),
        hypothesis_count=len(hypotheses),
        missing_scan_file_ids=(),
        invalid_scan_file_ids=(),
        hypotheses=hypotheses,
        analysis=analysis,
    )


def _interpretation(
    pairs,
    *,
    planned_pair_count: int,
    plan_signature: str = "a" * 64,
) -> DeepCorrelationInterpretationRun:
    policy = CorrelationInterpretationPolicy()
    payload = policy.identity_payload()
    signature = hashlib.sha256(_canonical(payload)).hexdigest()
    return DeepCorrelationInterpretationRun(
        scan_id=11,
        result_revision=7,
        correlation_plan_signature=plan_signature,
        interpretation_version=1,
        policy_signature=signature,
        policy_identity=payload,
        complete_modalities=(),
        planned_pair_count=planned_pair_count,
        pairs=tuple(pairs),
    )


class PatternSequenceArbitrationTests(unittest.TestCase):
    def test_plain_sequence_shift_remains_authoritative(self) -> None:
        hypotheses = (
            _hypothesis(1, 1, 2),
            _hypothesis(2, 2, 3),
            _hypothesis(3, 3, 4),
        )
        sequence = _sequence(hypotheses)
        interpretation = _interpretation(
            [],
            planned_pair_count=3,
        )

        result = correlate_deep_patterns(
            interpretation,
            sequence,
        )

        self.assertEqual(
            len(sequence.analysis.authoritative_observations),
            1,
        )
        self.assertEqual(result.sequence_conflict_seasons, ())
        self.assertEqual(
            result.authoritative_sequence_observations,
            sequence.analysis.authoritative_observations,
        )

    def test_four_file_rotation_conflicts_with_apparent_plus_one_shift(self) -> None:
        hypotheses = (
            _hypothesis(1, 1, 2),
            _hypothesis(2, 2, 3),
            _hypothesis(3, 3, 4),
            _hypothesis(4, 4, 1),
        )
        sequence = _sequence(hypotheses)

        result = correlate_deep_patterns(
            _interpretation([], planned_pair_count=6),
            sequence,
        )

        self.assertEqual(
            len(sequence.analysis.authoritative_observations),
            1,
        )
        self.assertEqual(len(result.patterns.identity_cycles), 1)
        self.assertEqual(result.sequence_conflict_seasons, (1,))
        self.assertEqual(
            result.authoritative_sequence_observations,
            (),
        )

    def test_strong_duplicate_conflicts_with_sequence_shift(self) -> None:
        hypotheses = (
            _hypothesis(1, 1, 2),
            _hypothesis(2, 2, 3),
            _hypothesis(3, 3, 4),
        )
        sequence = _sequence(hypotheses)
        duplicate = _pair(
            1,
            2,
            video=(0.95, 0.95),
            audio=(0.95, 0.95),
        )

        result = correlate_deep_patterns(
            _interpretation([duplicate], planned_pair_count=3),
            sequence,
        )

        self.assertEqual(len(result.patterns.strong_duplicates), 1)
        self.assertEqual(result.sequence_conflict_seasons, (1,))
        self.assertEqual(
            result.authoritative_sequence_observations,
            (),
        )

    def test_supported_but_not_strong_duplicate_does_not_cancel_shift(self) -> None:
        hypotheses = (
            _hypothesis(1, 1, 2),
            _hypothesis(2, 2, 3),
            _hypothesis(3, 3, 4),
        )
        sequence = _sequence(hypotheses)
        duplicate = _pair(
            1,
            2,
            video=(0.83, 0.83),
            audio=(0.85, 0.85),
        )

        result = correlate_deep_patterns(
            _interpretation([duplicate], planned_pair_count=3),
            sequence,
        )

        self.assertEqual(
            len(result.patterns.duplicate_observations),
            1,
        )
        self.assertEqual(result.patterns.strong_duplicates, ())
        self.assertEqual(result.sequence_conflict_seasons, ())
        self.assertEqual(
            len(result.authoritative_sequence_observations),
            1,
        )

    def test_reciprocal_swap_conflicts_with_broader_shift(self) -> None:
        hypotheses = (
            _hypothesis(1, 1, 2),
            _hypothesis(2, 2, 1),
            _hypothesis(3, 3, 4),
            _hypothesis(4, 4, 5),
            _hypothesis(5, 5, 6),
        )
        sequence = _sequence(hypotheses)
        distinct = _pair(
            1,
            2,
            video=(0.50, 0.50),
            audio=(0.50, 0.50),
        )

        result = correlate_deep_patterns(
            _interpretation([distinct], planned_pair_count=10),
            sequence,
        )

        self.assertEqual(len(result.patterns.possible_swaps), 1)
        self.assertEqual(
            len(sequence.analysis.authoritative_observations),
            1,
        )
        self.assertEqual(result.sequence_conflict_seasons, (1,))
        self.assertEqual(
            result.authoritative_sequence_observations,
            (),
        )

    def test_mismatched_plan_signature_is_rejected(self) -> None:
        hypotheses = (
            _hypothesis(1, 1, 2),
            _hypothesis(2, 2, 3),
            _hypothesis(3, 3, 4),
        )
        sequence = _sequence(hypotheses)

        with self.assertRaisesRegex(
            DeepCorrelationPatternError,
            "same sealed scan/cohort baseline",
        ):
            correlate_deep_patterns(
                _interpretation(
                    [],
                    planned_pair_count=3,
                    plan_signature="b" * 64,
                ),
                sequence,
            )


if __name__ == "__main__":
    unittest.main()

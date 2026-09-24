from __future__ import annotations

from dataclasses import dataclass

from .correlation_interpretation_service import (
    DeepCorrelationInterpretationRun,
)
from .correlation_patterns import (
    CorrelationPatternAnalysis,
    CorrelationPatternError,
    detect_correlation_patterns,
)
from .sequence_correlation import SequenceOffsetObservation
from .sequence_correlation_service import DeepSequenceCorrelationRun
from .versions import (
    DEEP_CORRELATION_INTERPRETATION_VERSION,
    DEEP_PATTERN_CORRELATION_VERSION,
)


class DeepCorrelationPatternError(RuntimeError):
    """J4 correlation inputs no longer describe the same sealed cohort."""


@dataclass(frozen=True)
class DeepCorrelationPatternRun:
    scan_id: int
    result_revision: int
    correlation_plan_signature: str
    interpretation_policy_signature: str
    sequence_policy_signature: str
    pattern_version: int
    patterns: CorrelationPatternAnalysis
    sequence: DeepSequenceCorrelationRun
    sequence_conflict_seasons: tuple[int, ...]

    def __post_init__(self) -> None:
        for label, value in (
            ("scan ID", self.scan_id),
            ("result revision", self.result_revision),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
            ):
                raise DeepCorrelationPatternError(
                    f"J4 pattern run {label} must be a positive integer."
                )
        for label, digest in (
            ("correlation-plan signature", self.correlation_plan_signature),
            (
                "interpretation-policy signature",
                self.interpretation_policy_signature,
            ),
            ("sequence-policy signature", self.sequence_policy_signature),
        ):
            normalized = str(digest or "").strip().casefold()
            if (
                len(normalized) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in normalized
                )
            ):
                raise DeepCorrelationPatternError(
                    f"J4 pattern run {label} is invalid."
                )
        if self.pattern_version != DEEP_PATTERN_CORRELATION_VERSION:
            raise DeepCorrelationPatternError(
                "J4 pattern run version is stale."
            )
        if not isinstance(self.patterns, CorrelationPatternAnalysis):
            raise DeepCorrelationPatternError(
                "J4 pattern analysis is malformed."
            )
        if not isinstance(self.sequence, DeepSequenceCorrelationRun):
            raise DeepCorrelationPatternError(
                "J4 sequence correlation input is malformed."
            )
        if (
            self.sequence.scan_id != self.scan_id
            or self.sequence.result_revision != self.result_revision
            or self.sequence.correlation_plan_signature
            != self.correlation_plan_signature
            or self.sequence.sequence_policy_signature
            != self.sequence_policy_signature
        ):
            raise DeepCorrelationPatternError(
                "J4 pattern run inputs do not share one sequence baseline."
            )
        if (
            len(set(self.sequence_conflict_seasons))
            != len(self.sequence_conflict_seasons)
            or tuple(sorted(self.sequence_conflict_seasons))
            != self.sequence_conflict_seasons
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
                for value in self.sequence_conflict_seasons
            )
        ):
            raise DeepCorrelationPatternError(
                "J4 sequence-conflict seasons are invalid."
            )

    @property
    def authoritative_sequence_observations(
        self,
    ) -> tuple[SequenceOffsetObservation, ...]:
        conflicts = set(self.sequence_conflict_seasons)
        return tuple(
            item
            for item in self.sequence.analysis.authoritative_observations
            if item.season not in conflicts
        )


def _claimed_season_by_file(
    sequence: DeepSequenceCorrelationRun,
) -> dict[int, int]:
    return {
        item.file_id: item.claimed_season
        for item in sequence.hypotheses
    }


def _sequence_conflicts(
    patterns: CorrelationPatternAnalysis,
    sequence: DeepSequenceCorrelationRun,
) -> tuple[int, ...]:
    authoritative_seasons = {
        item.season
        for item in sequence.analysis.authoritative_observations
    }
    if not authoritative_seasons:
        return ()

    claimed_season = _claimed_season_by_file(sequence)
    conflicted: set[int] = set()

    for duplicate in patterns.strong_duplicates:
        left = claimed_season.get(duplicate.left_file_id)
        right = claimed_season.get(duplicate.right_file_id)
        if (
            left is not None
            and left == right
            and left in authoritative_seasons
        ):
            conflicted.add(left)

    for swap in patterns.possible_swaps:
        left = swap.left_claimed[0]
        right = swap.right_claimed[0]
        if left == right and left in authoritative_seasons:
            conflicted.add(left)

    for cycle in patterns.identity_cycles:
        seasons = {
            coordinate[0]
            for coordinate in cycle.claimed_coordinates
        }
        if len(seasons) == 1:
            season = next(iter(seasons))
            if season in authoritative_seasons:
                conflicted.add(season)

    return tuple(sorted(conflicted))


def correlate_deep_patterns(
    interpretation: DeepCorrelationInterpretationRun,
    sequence: DeepSequenceCorrelationRun,
) -> DeepCorrelationPatternRun:
    if not isinstance(
        interpretation,
        DeepCorrelationInterpretationRun,
    ):
        raise DeepCorrelationPatternError(
            "J4.3 requires a DeepCorrelationInterpretationRun."
        )
    if not isinstance(sequence, DeepSequenceCorrelationRun):
        raise DeepCorrelationPatternError(
            "J4.3 requires a DeepSequenceCorrelationRun."
        )
    if (
        interpretation.interpretation_version
        != DEEP_CORRELATION_INTERPRETATION_VERSION
    ):
        raise DeepCorrelationPatternError(
            "J4.3 interpretation semantics are stale."
        )
    if (
        interpretation.scan_id != sequence.scan_id
        or interpretation.result_revision != sequence.result_revision
        or interpretation.correlation_plan_signature
        != sequence.correlation_plan_signature
    ):
        raise DeepCorrelationPatternError(
            "J4.3 inputs must share the same sealed scan/cohort baseline."
        )

    try:
        patterns = detect_correlation_patterns(
            pairs=interpretation.pairs,
            hypotheses=sequence.hypotheses,
            credibility_policy=sequence.analysis.policy,
        )
    except CorrelationPatternError as exc:
        raise DeepCorrelationPatternError(str(exc)) from exc

    sequence_conflicts = _sequence_conflicts(
        patterns,
        sequence,
    )
    return DeepCorrelationPatternRun(
        scan_id=interpretation.scan_id,
        result_revision=interpretation.result_revision,
        correlation_plan_signature=(
            interpretation.correlation_plan_signature
        ),
        interpretation_policy_signature=(
            interpretation.policy_signature
        ),
        sequence_policy_signature=(
            sequence.sequence_policy_signature
        ),
        pattern_version=DEEP_PATTERN_CORRELATION_VERSION,
        patterns=patterns,
        sequence=sequence,
        sequence_conflict_seasons=sequence_conflicts,
    )

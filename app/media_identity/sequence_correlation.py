from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

from .models import IdentityResultState
from .versions import DEEP_SEQUENCE_CORRELATION_VERSION


class SequenceCorrelationError(ValueError):
    """Cross-file identity hypotheses cannot be correlated safely."""


_SEQUENCE_ELIGIBLE_STATES = frozenset({
    IdentityResultState.VERIFIED,
    IdentityResultState.PROBABLY_CORRECT,
    IdentityResultState.INCONCLUSIVE,
    IdentityResultState.POSSIBLE_MISMATCH,
    IdentityResultState.LIKELY_MISMATCH,
    IdentityResultState.STRONG_MATCH_OTHER,
})


@dataclass(frozen=True)
class SequenceOffsetPolicy:
    max_abs_offset: int = 3
    minimum_files: int = 3
    minimum_support_ratio: float = 0.75
    maximum_claim_gap: int = 2
    minimum_support_strength: float = 0.50
    maximum_conflict_strength: float = 0.25
    minimum_margin: float = 0.10
    minimum_independent_categories: int = 1

    def __post_init__(self) -> None:
        for label, value, minimum, maximum in (
            ("maximum offset", self.max_abs_offset, 1, 12),
            ("minimum files", self.minimum_files, 3, 48),
            ("maximum claim gap", self.maximum_claim_gap, 1, 4),
            (
                "minimum independent categories",
                self.minimum_independent_categories,
                1,
                16,
            ),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < minimum
                or value > maximum
            ):
                raise SequenceCorrelationError(
                    f"Sequence {label} is outside the supported bound."
                )
        if (
            isinstance(self.minimum_support_ratio, bool)
            or not isinstance(self.minimum_support_ratio, (int, float))
            or not math.isfinite(float(self.minimum_support_ratio))
            or not 0.50 <= float(self.minimum_support_ratio) <= 1.0
        ):
            raise SequenceCorrelationError(
                "Sequence minimum support ratio must be between 0.50 and 1."
            )
        for label, value in (
            ("minimum support strength", self.minimum_support_strength),
            ("maximum conflict strength", self.maximum_conflict_strength),
            ("minimum margin", self.minimum_margin),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                raise SequenceCorrelationError(
                    f"Sequence {label} must be between 0 and 1."
                )

    def identity_payload(self) -> dict[str, object]:
        return {
            "version": DEEP_SEQUENCE_CORRELATION_VERSION,
            "max_abs_offset": self.max_abs_offset,
            "minimum_files": self.minimum_files,
            "minimum_support_ratio": float(self.minimum_support_ratio),
            "maximum_claim_gap": self.maximum_claim_gap,
            "minimum_support_strength": float(
                self.minimum_support_strength
            ),
            "maximum_conflict_strength": float(
                self.maximum_conflict_strength
            ),
            "minimum_margin": float(self.minimum_margin),
            "minimum_independent_categories": (
                self.minimum_independent_categories
            ),
            "eligible_states": sorted(
                state.value for state in _SEQUENCE_ELIGIBLE_STATES
            ),
        }


@dataclass(frozen=True)
class SequenceHypothesis:
    file_id: int
    scan_id: int
    result_revision: int
    claimed_season: int
    claimed_episode: int
    claimed_episode_end: int
    candidate_key: str
    hypothesis_season: int
    hypothesis_episode: int
    result_state: IdentityResultState
    support_strength: float
    conflict_strength: float
    margin: float
    independent_categories: int
    content_support: bool

    def __post_init__(self) -> None:
        for label, value in (
            ("file ID", self.file_id),
            ("scan ID", self.scan_id),
            ("result revision", self.result_revision),
            ("claimed season", self.claimed_season),
            ("claimed episode", self.claimed_episode),
            ("claimed episode end", self.claimed_episode_end),
            ("hypothesis season", self.hypothesis_season),
            ("hypothesis episode", self.hypothesis_episode),
            ("independent categories", self.independent_categories),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise SequenceCorrelationError(
                    f"Sequence hypothesis {label} must be an integer."
                )
        if (
            self.file_id < 1
            or self.scan_id < 1
            or self.result_revision < 1
            or self.claimed_season < 0
            or self.claimed_episode < 0
            or self.claimed_episode_end < self.claimed_episode
            or self.hypothesis_season < 0
            or self.hypothesis_episode < 0
            or self.independent_categories < 0
        ):
            raise SequenceCorrelationError(
                "Sequence hypothesis coordinates or identifiers are invalid."
            )
        if not isinstance(self.candidate_key, str) or not self.candidate_key:
            raise SequenceCorrelationError(
                "Sequence hypothesis requires a candidate key."
            )
        if not isinstance(self.result_state, IdentityResultState):
            raise SequenceCorrelationError(
                "Sequence hypothesis requires an IdentityResultState."
            )
        if not isinstance(self.content_support, bool):
            raise SequenceCorrelationError(
                "Sequence hypothesis content-support flag must be boolean."
            )
        for label, value in (
            ("support strength", self.support_strength),
            ("conflict strength", self.conflict_strength),
            ("margin", self.margin),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                raise SequenceCorrelationError(
                    f"Sequence hypothesis {label} must be between 0 and 1."
                )

    @property
    def single_episode(self) -> bool:
        return self.claimed_episode == self.claimed_episode_end

    @property
    def offset(self) -> int | None:
        if (
            not self.single_episode
            or self.claimed_season != self.hypothesis_season
        ):
            return None
        return self.hypothesis_episode - self.claimed_episode

    def usable(self, policy: SequenceOffsetPolicy) -> bool:
        return (
            self.single_episode
            and self.claimed_season > 0
            and self.result_state in _SEQUENCE_ELIGIBLE_STATES
            and self.content_support
            and self.support_strength >= policy.minimum_support_strength
            and self.conflict_strength <= policy.maximum_conflict_strength
            and self.margin >= policy.minimum_margin
            and self.independent_categories
            >= policy.minimum_independent_categories
        )


@dataclass(frozen=True)
class SequenceOffsetObservation:
    season: int
    offset: int
    supporting_file_ids: tuple[int, ...]
    usable_file_ids: tuple[int, ...]
    support_count: int
    usable_count: int
    support_ratio: float
    longest_chain: int
    first_claimed_episode: int
    last_claimed_episode: int
    conflicted: bool = False

    def __post_init__(self) -> None:
        if self.season <= 0 or self.offset == 0:
            raise SequenceCorrelationError(
                "Sequence observations require a regular season and non-zero offset."
            )
        if self.support_count != len(self.supporting_file_ids):
            raise SequenceCorrelationError(
                "Sequence observation support count is inconsistent."
            )
        if self.usable_count != len(self.usable_file_ids):
            raise SequenceCorrelationError(
                "Sequence observation usable count is inconsistent."
            )
        if (
            self.support_count < 1
            or self.usable_count < self.support_count
            or self.longest_chain < 1
            or self.longest_chain > self.support_count
        ):
            raise SequenceCorrelationError(
                "Sequence observation counts are inconsistent."
            )
        expected_ratio = self.support_count / float(self.usable_count)
        if abs(float(self.support_ratio) - expected_ratio) > 1e-9:
            raise SequenceCorrelationError(
                "Sequence observation support ratio is inconsistent."
            )
        if self.first_claimed_episode > self.last_claimed_episode:
            raise SequenceCorrelationError(
                "Sequence observation episode span is invalid."
            )


@dataclass(frozen=True)
class SequenceOffsetAnalysis:
    policy: SequenceOffsetPolicy
    hypothesis_count: int
    usable_count: int
    excluded_file_ids: tuple[int, ...]
    observations: tuple[SequenceOffsetObservation, ...]
    conflicted_seasons: tuple[int, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.policy, SequenceOffsetPolicy):
            raise SequenceCorrelationError(
                "Sequence analysis requires a SequenceOffsetPolicy."
            )
        for label, value in (
            ("hypothesis count", self.hypothesis_count),
            ("usable count", self.usable_count),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise SequenceCorrelationError(
                    f"Sequence analysis {label} must be a non-negative integer."
                )
        if self.usable_count > self.hypothesis_count:
            raise SequenceCorrelationError(
                "Sequence analysis usable count exceeds its hypothesis count."
            )
        if (
            len(self.excluded_file_ids)
            != self.hypothesis_count - self.usable_count
        ):
            raise SequenceCorrelationError(
                "Sequence analysis excluded-file count is inconsistent."
            )
        if (
            len(set(self.excluded_file_ids))
            != len(self.excluded_file_ids)
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
                for value in self.excluded_file_ids
            )
        ):
            raise SequenceCorrelationError(
                "Sequence analysis excluded file IDs are invalid."
            )
        if any(
            not isinstance(item, SequenceOffsetObservation)
            for item in self.observations
        ):
            raise SequenceCorrelationError(
                "Sequence analysis observations are malformed."
            )
        expected_conflicts = tuple(sorted({
            item.season
            for item in self.observations
            if item.conflicted
        }))
        if self.conflicted_seasons != expected_conflicts:
            raise SequenceCorrelationError(
                "Sequence analysis conflicted seasons are inconsistent."
            )
        by_season: dict[int, int] = {}
        for item in self.observations:
            by_season[item.season] = by_season.get(item.season, 0) + 1
            if item.usable_count > self.usable_count:
                raise SequenceCorrelationError(
                    "Sequence observation exceeds the analysis usable count."
                )
        for season, count in by_season.items():
            if (count > 1) != (season in set(self.conflicted_seasons)):
                raise SequenceCorrelationError(
                    "Sequence analysis conflict markers are inconsistent."
                )

    @property
    def authoritative_observations(
        self,
    ) -> tuple[SequenceOffsetObservation, ...]:
        return tuple(
            item for item in self.observations
            if not item.conflicted
        )


def _longest_chain(
    hypotheses: list[SequenceHypothesis],
    *,
    maximum_gap: int,
) -> int:
    if not hypotheses:
        return 0
    episodes = sorted({
        item.claimed_episode
        for item in hypotheses
    })
    longest = current = 1
    previous = episodes[0]
    for episode in episodes[1:]:
        gap = episode - previous
        if 1 <= gap <= maximum_gap:
            current += 1
        else:
            current = 1
        longest = max(longest, current)
        previous = episode
    return longest


def detect_sequence_offsets(
    hypotheses: Iterable[SequenceHypothesis],
    *,
    policy: SequenceOffsetPolicy | None = None,
) -> SequenceOffsetAnalysis:
    policy = policy or SequenceOffsetPolicy()
    prepared = tuple(hypotheses)
    if len({item.file_id for item in prepared}) != len(prepared):
        raise SequenceCorrelationError(
            "Sequence hypotheses must contain unique file IDs."
        )

    usable = [
        item for item in prepared
        if item.usable(policy)
    ]
    excluded = tuple(sorted(
        item.file_id for item in prepared
        if item not in usable
    ))

    by_season: dict[int, list[SequenceHypothesis]] = {}
    for item in usable:
        by_season.setdefault(
            item.claimed_season,
            [],
        ).append(item)

    provisional: list[SequenceOffsetObservation] = []
    for season, season_hypotheses in sorted(by_season.items()):
        usable_file_ids = tuple(sorted(
            item.file_id for item in season_hypotheses
        ))
        by_offset: dict[int, list[SequenceHypothesis]] = {}
        for item in season_hypotheses:
            offset = item.offset
            if (
                offset is None
                or offset == 0
                or abs(offset) > policy.max_abs_offset
            ):
                continue
            by_offset.setdefault(offset, []).append(item)

        for offset, supporters in sorted(by_offset.items()):
            support_count = len(supporters)
            usable_count = len(season_hypotheses)
            ratio = support_count / float(usable_count)
            chain = _longest_chain(
                supporters,
                maximum_gap=policy.maximum_claim_gap,
            )
            if (
                support_count < policy.minimum_files
                or ratio < policy.minimum_support_ratio
                or chain < policy.minimum_files
            ):
                continue
            claimed_episodes = sorted(
                item.claimed_episode
                for item in supporters
            )
            provisional.append(
                SequenceOffsetObservation(
                    season=season,
                    offset=offset,
                    supporting_file_ids=tuple(sorted(
                        item.file_id for item in supporters
                    )),
                    usable_file_ids=usable_file_ids,
                    support_count=support_count,
                    usable_count=usable_count,
                    support_ratio=ratio,
                    longest_chain=chain,
                    first_claimed_episode=claimed_episodes[0],
                    last_claimed_episode=claimed_episodes[-1],
                )
            )

    counts_by_season: dict[int, int] = {}
    for item in provisional:
        counts_by_season[item.season] = (
            counts_by_season.get(item.season, 0) + 1
        )
    conflicted_seasons = tuple(sorted(
        season
        for season, count in counts_by_season.items()
        if count > 1
    ))
    conflict_set = set(conflicted_seasons)
    observations = tuple(
        SequenceOffsetObservation(
            season=item.season,
            offset=item.offset,
            supporting_file_ids=item.supporting_file_ids,
            usable_file_ids=item.usable_file_ids,
            support_count=item.support_count,
            usable_count=item.usable_count,
            support_ratio=item.support_ratio,
            longest_chain=item.longest_chain,
            first_claimed_episode=item.first_claimed_episode,
            last_claimed_episode=item.last_claimed_episode,
            conflicted=item.season in conflict_set,
        )
        for item in provisional
    )

    return SequenceOffsetAnalysis(
        policy=policy,
        hypothesis_count=len(prepared),
        usable_count=len(usable),
        excluded_file_ids=excluded,
        observations=observations,
        conflicted_seasons=conflicted_seasons,
    )

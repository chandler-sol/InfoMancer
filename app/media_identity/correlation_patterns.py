from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .correlation_interpretation import (
    MultimodalAgreement,
    PairInterpretation,
)
from .sequence_correlation import (
    SequenceHypothesis,
    SequenceOffsetPolicy,
)
from .versions import DEEP_PATTERN_CORRELATION_VERSION


class CorrelationPatternError(ValueError):
    """Cross-file duplicate/swap patterns cannot be interpreted safely."""


class DuplicatePatternStrength(str, Enum):
    STRONG_MULTIMODAL = "strong_multimodal"
    SUPPORTED_MULTIMODAL = "supported_multimodal"


class SwapPatternStatus(str, Enum):
    CORROBORATED_DISTINCT = "corroborated_distinct"
    HYPOTHESIS_ONLY = "hypothesis_only"
    CONFLICTED_SIMILARITY = "conflicted_similarity"
    CONFLICTED_MODALITIES = "conflicted_modalities"


@dataclass(frozen=True)
class DuplicatePatternObservation:
    left_file_id: int
    right_file_id: int
    strength: DuplicatePatternStrength
    agreement: MultimodalAgreement

    def __post_init__(self) -> None:
        if (
            isinstance(self.left_file_id, bool)
            or isinstance(self.right_file_id, bool)
            or not isinstance(self.left_file_id, int)
            or not isinstance(self.right_file_id, int)
            or self.left_file_id < 1
            or self.right_file_id < 1
            or self.left_file_id >= self.right_file_id
        ):
            raise CorrelationPatternError(
                "Duplicate observations require normalized distinct file IDs."
            )
        if not isinstance(self.strength, DuplicatePatternStrength):
            raise CorrelationPatternError(
                "Duplicate observation strength is invalid."
            )
        if self.strength is DuplicatePatternStrength.STRONG_MULTIMODAL:
            expected = MultimodalAgreement.BOTH_HIGH
        else:
            expected = MultimodalAgreement.BOTH_SUPPORT
        if self.agreement is not expected:
            raise CorrelationPatternError(
                "Duplicate observation strength disagrees with fingerprint evidence."
            )


@dataclass(frozen=True)
class SwapPatternObservation:
    left_file_id: int
    right_file_id: int
    left_claimed: tuple[int, int]
    right_claimed: tuple[int, int]
    left_hypothesis: tuple[int, int]
    right_hypothesis: tuple[int, int]
    status: SwapPatternStatus
    fingerprint_agreement: MultimodalAgreement | None
    fingerprint_similarity_support: bool

    def __post_init__(self) -> None:
        if (
            isinstance(self.left_file_id, bool)
            or isinstance(self.right_file_id, bool)
            or not isinstance(self.left_file_id, int)
            or not isinstance(self.right_file_id, int)
            or self.left_file_id < 1
            or self.right_file_id < 1
            or self.left_file_id >= self.right_file_id
        ):
            raise CorrelationPatternError(
                "Swap observations require normalized distinct file IDs."
            )
        if (
            self.left_hypothesis != self.right_claimed
            or self.right_hypothesis != self.left_claimed
        ):
            raise CorrelationPatternError(
                "Swap observation hypotheses are not reciprocal."
            )
        if self.left_claimed == self.right_claimed:
            raise CorrelationPatternError(
                "Swap observations require distinct claimed coordinates."
            )
        if not isinstance(self.status, SwapPatternStatus):
            raise CorrelationPatternError(
                "Swap observation status is invalid."
            )
        if (
            self.fingerprint_agreement is not None
            and not isinstance(
                self.fingerprint_agreement,
                MultimodalAgreement,
            )
        ):
            raise CorrelationPatternError(
                "Swap fingerprint agreement is invalid."
            )
        if not isinstance(
            self.fingerprint_similarity_support,
            bool,
        ):
            raise CorrelationPatternError(
                "Swap fingerprint similarity-support flag is invalid."
            )
        if self.status is SwapPatternStatus.CORROBORATED_DISTINCT:
            if (
                self.fingerprint_agreement
                is not MultimodalAgreement.BOTH_LOW
                or self.fingerprint_similarity_support
            ):
                raise CorrelationPatternError(
                    "Corroborated swaps require multimodal difference support."
                )
        elif self.status is SwapPatternStatus.CONFLICTED_MODALITIES:
            if (
                self.fingerprint_agreement
                is not MultimodalAgreement.CONTRADICTORY
            ):
                raise CorrelationPatternError(
                    "Modality-conflicted swaps require contradictory fingerprints."
                )
        elif self.status is SwapPatternStatus.CONFLICTED_SIMILARITY:
            if not self.fingerprint_similarity_support:
                raise CorrelationPatternError(
                    "Similarity-conflicted swaps require similarity support."
                )
        elif self.status is SwapPatternStatus.HYPOTHESIS_ONLY:
            if self.fingerprint_similarity_support:
                raise CorrelationPatternError(
                    "Hypothesis-only swaps cannot carry fingerprint similarity support."
                )


@dataclass(frozen=True)
class IdentityCycleObservation:
    file_ids: tuple[int, ...]
    target_file_ids: tuple[int, ...]
    claimed_coordinates: tuple[tuple[int, int], ...]
    hypothesis_coordinates: tuple[tuple[int, int], ...]

    def __post_init__(self) -> None:
        if len(self.file_ids) < 3:
            raise CorrelationPatternError(
                "Identity cycles require at least three files."
            )
        if (
            len(set(self.file_ids)) != len(self.file_ids)
            or tuple(sorted(self.file_ids)) != self.file_ids
        ):
            raise CorrelationPatternError(
                "Identity cycle file IDs must be unique and normalized."
            )
        if (
            len(self.target_file_ids) != len(self.file_ids)
            or len(self.claimed_coordinates) != len(self.file_ids)
            or len(self.hypothesis_coordinates) != len(self.file_ids)
        ):
            raise CorrelationPatternError(
                "Identity cycle coordinate counts are inconsistent."
            )
        if (
            set(self.target_file_ids) != set(self.file_ids)
            or any(
                source == target
                for source, target in zip(
                    self.file_ids,
                    self.target_file_ids,
                )
            )
        ):
            raise CorrelationPatternError(
                "Identity cycle targets do not form a closed file cycle."
            )


@dataclass(frozen=True)
class CorrelationPatternAnalysis:
    version: int
    duplicate_observations: tuple[DuplicatePatternObservation, ...]
    swap_observations: tuple[SwapPatternObservation, ...]
    identity_cycles: tuple[IdentityCycleObservation, ...]
    ambiguous_claim_file_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.version != DEEP_PATTERN_CORRELATION_VERSION:
            raise CorrelationPatternError(
                "Correlation pattern analysis version is stale."
            )
        duplicate_pairs = [
            (item.left_file_id, item.right_file_id)
            for item in self.duplicate_observations
        ]
        swap_pairs = [
            (item.left_file_id, item.right_file_id)
            for item in self.swap_observations
        ]
        if len(set(duplicate_pairs)) != len(duplicate_pairs):
            raise CorrelationPatternError(
                "Correlation pattern analysis contains duplicate duplicate-pairs."
            )
        if len(set(swap_pairs)) != len(swap_pairs):
            raise CorrelationPatternError(
                "Correlation pattern analysis contains duplicate swap-pairs."
            )
        if (
            len(set(self.ambiguous_claim_file_ids))
            != len(self.ambiguous_claim_file_ids)
            or tuple(sorted(self.ambiguous_claim_file_ids))
            != self.ambiguous_claim_file_ids
        ):
            raise CorrelationPatternError(
                "Ambiguous claim file IDs must be unique and normalized."
            )

    @property
    def strong_duplicates(
        self,
    ) -> tuple[DuplicatePatternObservation, ...]:
        return tuple(
            item
            for item in self.duplicate_observations
            if item.strength
            is DuplicatePatternStrength.STRONG_MULTIMODAL
        )

    @property
    def possible_swaps(
        self,
    ) -> tuple[SwapPatternObservation, ...]:
        return tuple(
            item
            for item in self.swap_observations
            if item.status
            in {
                SwapPatternStatus.CORROBORATED_DISTINCT,
                SwapPatternStatus.HYPOTHESIS_ONLY,
            }
        )


def _pair_key(left: int, right: int) -> tuple[int, int]:
    return (min(left, right), max(left, right))


def _coordinate(item: SequenceHypothesis) -> tuple[int, int]:
    return item.claimed_season, item.claimed_episode


def _hypothesis_coordinate(
    item: SequenceHypothesis,
) -> tuple[int, int]:
    return item.hypothesis_season, item.hypothesis_episode


def _credible_hypotheses(
    hypotheses: Iterable[SequenceHypothesis],
    *,
    policy: SequenceOffsetPolicy,
) -> tuple[SequenceHypothesis, ...]:
    prepared = tuple(hypotheses)
    if any(
        not isinstance(item, SequenceHypothesis)
        for item in prepared
    ):
        raise CorrelationPatternError(
            "Pattern analysis requires SequenceHypothesis values."
        )
    if len({item.file_id for item in prepared}) != len(prepared):
        raise CorrelationPatternError(
            "Pattern hypotheses must contain unique file IDs."
        )
    return tuple(
        item
        for item in prepared
        if item.usable(policy)
    )


def _claim_owners(
    hypotheses: tuple[SequenceHypothesis, ...],
) -> tuple[
    dict[tuple[int, int], int],
    tuple[int, ...],
]:
    grouped: dict[tuple[int, int], list[int]] = {}
    for item in hypotheses:
        grouped.setdefault(
            _coordinate(item),
            [],
        ).append(item.file_id)

    owners: dict[tuple[int, int], int] = {}
    ambiguous: set[int] = set()
    for coordinate, file_ids in grouped.items():
        if len(file_ids) == 1:
            owners[coordinate] = file_ids[0]
        else:
            ambiguous.update(file_ids)
    return owners, tuple(sorted(ambiguous))


def _find_cycles(
    edges: dict[int, int],
    hypotheses_by_file: dict[int, SequenceHypothesis],
) -> tuple[
    tuple[tuple[int, int], ...],
    tuple[IdentityCycleObservation, ...],
]:
    visited: set[int] = set()
    swap_pairs: set[tuple[int, int]] = set()
    cycles: list[IdentityCycleObservation] = []

    for start in sorted(edges):
        if start in visited:
            continue
        path: list[int] = []
        index_by_file: dict[int, int] = {}
        current = start
        while current in edges and current not in visited:
            if current in index_by_file:
                cycle = path[index_by_file[current]:]
                if len(cycle) == 2:
                    swap_pairs.add(_pair_key(cycle[0], cycle[1]))
                elif len(cycle) >= 3:
                    normalized = tuple(sorted(cycle))
                    claimed = tuple(
                        _coordinate(hypotheses_by_file[file_id])
                        for file_id in normalized
                    )
                    hypothesis_coordinates = tuple(
                        _hypothesis_coordinate(
                            hypotheses_by_file[file_id]
                        )
                        for file_id in normalized
                    )
                    target_file_ids = tuple(
                        edges[file_id]
                        for file_id in normalized
                    )
                    cycles.append(
                        IdentityCycleObservation(
                            file_ids=normalized,
                            target_file_ids=target_file_ids,
                            claimed_coordinates=claimed,
                            hypothesis_coordinates=(
                                hypothesis_coordinates
                            ),
                        )
                    )
                break
            index_by_file[current] = len(path)
            path.append(current)
            current = edges[current]
        visited.update(path)

    unique_cycles: dict[
        tuple[int, ...],
        IdentityCycleObservation,
    ] = {
        item.file_ids: item
        for item in cycles
    }
    return (
        tuple(sorted(swap_pairs)),
        tuple(
            unique_cycles[key]
            for key in sorted(unique_cycles)
        ),
    )


def _swap_status(
    pair: PairInterpretation | None,
) -> tuple[
    SwapPatternStatus,
    MultimodalAgreement | None,
    bool,
]:
    if pair is None:
        return SwapPatternStatus.HYPOTHESIS_ONLY, None, False
    agreement = pair.agreement
    similarity_support = pair.has_any_similarity_support
    if agreement is MultimodalAgreement.BOTH_LOW:
        return (
            SwapPatternStatus.CORROBORATED_DISTINCT,
            agreement,
            False,
        )
    if agreement is MultimodalAgreement.CONTRADICTORY:
        return (
            SwapPatternStatus.CONFLICTED_MODALITIES,
            agreement,
            similarity_support,
        )
    if similarity_support:
        return (
            SwapPatternStatus.CONFLICTED_SIMILARITY,
            agreement,
            True,
        )
    return (
        SwapPatternStatus.HYPOTHESIS_ONLY,
        agreement,
        False,
    )


def detect_correlation_patterns(
    *,
    pairs: Iterable[PairInterpretation],
    hypotheses: Iterable[SequenceHypothesis],
    credibility_policy: SequenceOffsetPolicy | None = None,
) -> CorrelationPatternAnalysis:
    credibility_policy = (
        credibility_policy or SequenceOffsetPolicy()
    )
    pair_items = tuple(pairs)
    if any(
        not isinstance(item, PairInterpretation)
        for item in pair_items
    ):
        raise CorrelationPatternError(
            "Pattern analysis requires PairInterpretation values."
        )
    pair_by_key: dict[tuple[int, int], PairInterpretation] = {}
    for item in pair_items:
        key = (item.left_file_id, item.right_file_id)
        if key in pair_by_key:
            raise CorrelationPatternError(
                "Pattern interpretation contains a duplicate file pair."
            )
        pair_by_key[key] = item

    duplicates: list[DuplicatePatternObservation] = []
    for item in pair_items:
        if item.agreement is MultimodalAgreement.BOTH_HIGH:
            duplicates.append(
                DuplicatePatternObservation(
                    left_file_id=item.left_file_id,
                    right_file_id=item.right_file_id,
                    strength=(
                        DuplicatePatternStrength.STRONG_MULTIMODAL
                    ),
                    agreement=item.agreement,
                )
            )
        elif item.agreement is MultimodalAgreement.BOTH_SUPPORT:
            duplicates.append(
                DuplicatePatternObservation(
                    left_file_id=item.left_file_id,
                    right_file_id=item.right_file_id,
                    strength=(
                        DuplicatePatternStrength.SUPPORTED_MULTIMODAL
                    ),
                    agreement=item.agreement,
                )
            )

    credible = _credible_hypotheses(
        hypotheses,
        policy=credibility_policy,
    )
    owners, ambiguous = _claim_owners(credible)
    hypotheses_by_file = {
        item.file_id: item
        for item in credible
    }

    edges: dict[int, int] = {}
    for item in credible:
        target = owners.get(_hypothesis_coordinate(item))
        if target is None or target == item.file_id:
            continue
        edges[item.file_id] = target

    swap_pairs, cycles = _find_cycles(
        edges,
        hypotheses_by_file,
    )
    swaps: list[SwapPatternObservation] = []
    for left, right in swap_pairs:
        left_item = hypotheses_by_file[left]
        right_item = hypotheses_by_file[right]
        pair = pair_by_key.get((left, right))
        status, agreement, similarity_support = _swap_status(
            pair
        )
        swaps.append(
            SwapPatternObservation(
                left_file_id=left,
                right_file_id=right,
                left_claimed=_coordinate(left_item),
                right_claimed=_coordinate(right_item),
                left_hypothesis=_hypothesis_coordinate(left_item),
                right_hypothesis=_hypothesis_coordinate(right_item),
                status=status,
                fingerprint_agreement=agreement,
                fingerprint_similarity_support=(
                    similarity_support
                ),
            )
        )

    return CorrelationPatternAnalysis(
        version=DEEP_PATTERN_CORRELATION_VERSION,
        duplicate_observations=tuple(sorted(
            duplicates,
            key=lambda item: (
                item.left_file_id,
                item.right_file_id,
            ),
        )),
        swap_observations=tuple(sorted(
            swaps,
            key=lambda item: (
                item.left_file_id,
                item.right_file_id,
            ),
        )),
        identity_cycles=cycles,
        ambiguous_claim_file_ids=ambiguous,
    )

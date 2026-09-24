from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Iterable, Mapping

from .fingerprint import FingerprintComparison
from .versions import DEEP_CORRELATION_INTERPRETATION_VERSION


class CorrelationInterpretationError(ValueError):
    """Fingerprint measurements cannot be interpreted under the bounded policy."""


class SimilarityBand(str, Enum):
    HIGH = "high"
    MODERATE = "moderate"
    AMBIGUOUS = "ambiguous"
    LOW = "low"
    INSUFFICIENT = "insufficient"


class MultimodalAgreement(str, Enum):
    BOTH_HIGH = "both_high"
    BOTH_SUPPORT = "both_support"
    BOTH_LOW = "both_low"
    CONTRADICTORY = "contradictory"
    SINGLE_MODALITY = "single_modality"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class ModalityThresholds:
    high_median: float
    high_mean: float
    moderate_median: float
    moderate_mean: float
    low_median: float
    low_mean: float
    minimum_coverage: float
    minimum_samples: int

    def __post_init__(self) -> None:
        numeric = (
            ("high median", self.high_median),
            ("high mean", self.high_mean),
            ("moderate median", self.moderate_median),
            ("moderate mean", self.moderate_mean),
            ("low median", self.low_median),
            ("low mean", self.low_mean),
            ("minimum coverage", self.minimum_coverage),
        )
        for label, value in numeric:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                raise CorrelationInterpretationError(
                    f"Correlation {label} must be between 0 and 1."
                )
        if (
            isinstance(self.minimum_samples, bool)
            or not isinstance(self.minimum_samples, int)
            or self.minimum_samples < 1
            or self.minimum_samples > 32
        ):
            raise CorrelationInterpretationError(
                "Correlation minimum sample count is outside the supported bound."
            )
        if not (
            self.high_median > self.moderate_median > self.low_median
            and self.high_mean > self.moderate_mean > self.low_mean
        ):
            raise CorrelationInterpretationError(
                "Correlation similarity thresholds must be strictly ordered."
            )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "high_median": float(self.high_median),
            "high_mean": float(self.high_mean),
            "moderate_median": float(self.moderate_median),
            "moderate_mean": float(self.moderate_mean),
            "low_median": float(self.low_median),
            "low_mean": float(self.low_mean),
            "minimum_coverage": float(self.minimum_coverage),
            "minimum_samples": self.minimum_samples,
        }


@dataclass(frozen=True)
class CorrelationInterpretationPolicy:
    video: ModalityThresholds = ModalityThresholds(
        high_median=0.90,
        high_mean=0.88,
        moderate_median=0.82,
        moderate_mean=0.80,
        low_median=0.66,
        low_mean=0.68,
        minimum_coverage=0.50,
        minimum_samples=6,
    )
    audio: ModalityThresholds = ModalityThresholds(
        high_median=0.91,
        high_mean=0.89,
        moderate_median=0.84,
        moderate_mean=0.82,
        low_median=0.64,
        low_mean=0.66,
        minimum_coverage=0.75,
        minimum_samples=6,
    )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "version": DEEP_CORRELATION_INTERPRETATION_VERSION,
            "video": self.video.identity_payload(),
            "audio": self.audio.identity_payload(),
        }


@dataclass(frozen=True)
class ModalityInterpretation:
    modality: str
    left_file_id: int
    right_file_id: int
    band: SimilarityBand
    compared_samples: int
    coverage: float
    mean_similarity: float
    median_similarity: float
    minimum_similarity: float
    alignment_shift: int
    sufficient: bool

    def __post_init__(self) -> None:
        if self.modality not in {"video", "audio"}:
            raise CorrelationInterpretationError(
                "Correlation modality must be video or audio."
            )

    @property
    def supports_similarity(self) -> bool:
        return self.band in {
            SimilarityBand.HIGH,
            SimilarityBand.MODERATE,
        }

    @property
    def strongly_supports_similarity(self) -> bool:
        return self.band is SimilarityBand.HIGH

    @property
    def supports_difference(self) -> bool:
        return self.band is SimilarityBand.LOW


@dataclass(frozen=True)
class PairInterpretation:
    left_file_id: int
    right_file_id: int
    video: ModalityInterpretation | None
    audio: ModalityInterpretation | None
    agreement: MultimodalAgreement

    def __post_init__(self) -> None:
        if (
            isinstance(self.left_file_id, bool)
            or isinstance(self.right_file_id, bool)
            or not isinstance(self.left_file_id, int)
            or not isinstance(self.right_file_id, int)
            or self.left_file_id < 1
            or self.right_file_id < 1
            or self.left_file_id == self.right_file_id
        ):
            raise CorrelationInterpretationError(
                "Pair interpretation requires two distinct positive file IDs."
            )
        expected_pair = frozenset(
            (self.left_file_id, self.right_file_id)
        )
        for item in (self.video, self.audio):
            if item is None:
                continue
            if frozenset(
                (item.left_file_id, item.right_file_id)
            ) != expected_pair:
                raise CorrelationInterpretationError(
                    "Modality interpretation belongs to a different file pair."
                )

    @property
    def has_high_multimodal_support(self) -> bool:
        return self.agreement is MultimodalAgreement.BOTH_HIGH

    @property
    def has_any_similarity_support(self) -> bool:
        return any(
            item is not None and item.supports_similarity
            for item in (self.video, self.audio)
        )

    @property
    def contradictory(self) -> bool:
        return self.agreement is MultimodalAgreement.CONTRADICTORY


def _thresholds_for(
    modality: str,
    policy: CorrelationInterpretationPolicy,
) -> ModalityThresholds:
    if modality == "video":
        return policy.video
    if modality == "audio":
        return policy.audio
    raise CorrelationInterpretationError(
        "Correlation modality must be video or audio."
    )


def interpret_modality(
    comparison: FingerprintComparison | None,
    *,
    modality: str,
    policy: CorrelationInterpretationPolicy | None = None,
) -> ModalityInterpretation | None:
    if comparison is None:
        return None
    if not isinstance(comparison, FingerprintComparison):
        raise CorrelationInterpretationError(
            "Correlation interpretation requires FingerprintComparison values."
        )
    policy = policy or CorrelationInterpretationPolicy()
    thresholds = _thresholds_for(modality, policy)
    sufficient = (
        comparison.compared_samples >= thresholds.minimum_samples
        and comparison.coverage >= thresholds.minimum_coverage
    )
    if not sufficient:
        band = SimilarityBand.INSUFFICIENT
    elif (
        comparison.median_similarity >= thresholds.high_median
        and comparison.mean_similarity >= thresholds.high_mean
    ):
        band = SimilarityBand.HIGH
    elif (
        comparison.median_similarity >= thresholds.moderate_median
        and comparison.mean_similarity >= thresholds.moderate_mean
    ):
        band = SimilarityBand.MODERATE
    elif (
        comparison.median_similarity <= thresholds.low_median
        and comparison.mean_similarity <= thresholds.low_mean
    ):
        band = SimilarityBand.LOW
    else:
        band = SimilarityBand.AMBIGUOUS

    return ModalityInterpretation(
        modality=modality,
        left_file_id=comparison.left_file_id,
        right_file_id=comparison.right_file_id,
        band=band,
        compared_samples=comparison.compared_samples,
        coverage=comparison.coverage,
        mean_similarity=comparison.mean_similarity,
        median_similarity=comparison.median_similarity,
        minimum_similarity=comparison.minimum_similarity,
        alignment_shift=comparison.alignment_shift,
        sufficient=sufficient,
    )


def _agreement(
    video: ModalityInterpretation | None,
    audio: ModalityInterpretation | None,
) -> MultimodalAgreement:
    present = tuple(
        item for item in (video, audio)
        if item is not None and item.sufficient
    )
    if len(present) == 1:
        return MultimodalAgreement.SINGLE_MODALITY
    if len(present) == 0:
        return MultimodalAgreement.INCONCLUSIVE

    assert video is not None
    assert audio is not None
    if (
        video.band is SimilarityBand.HIGH
        and audio.band is SimilarityBand.HIGH
    ):
        return MultimodalAgreement.BOTH_HIGH
    if (
        video.supports_similarity
        and audio.supports_similarity
    ):
        return MultimodalAgreement.BOTH_SUPPORT
    if (
        video.supports_difference
        and audio.supports_difference
    ):
        return MultimodalAgreement.BOTH_LOW
    if (
        (
            video.supports_similarity
            and audio.supports_difference
        )
        or (
            audio.supports_similarity
            and video.supports_difference
        )
    ):
        return MultimodalAgreement.CONTRADICTORY
    return MultimodalAgreement.INCONCLUSIVE


def interpret_pair(
    *,
    left_file_id: int,
    right_file_id: int,
    video: FingerprintComparison | None,
    audio: FingerprintComparison | None,
    policy: CorrelationInterpretationPolicy | None = None,
) -> PairInterpretation:
    policy = policy or CorrelationInterpretationPolicy()
    video_interpretation = interpret_modality(
        video,
        modality="video",
        policy=policy,
    )
    audio_interpretation = interpret_modality(
        audio,
        modality="audio",
        policy=policy,
    )
    pair = PairInterpretation(
        left_file_id=left_file_id,
        right_file_id=right_file_id,
        video=video_interpretation,
        audio=audio_interpretation,
        agreement=_agreement(
            video_interpretation,
            audio_interpretation,
        ),
    )
    return pair


def _pair_key(
    left_file_id: int,
    right_file_id: int,
) -> tuple[int, int]:
    if (
        isinstance(left_file_id, bool)
        or isinstance(right_file_id, bool)
        or not isinstance(left_file_id, int)
        or not isinstance(right_file_id, int)
        or left_file_id < 1
        or right_file_id < 1
        or left_file_id == right_file_id
    ):
        raise CorrelationInterpretationError(
            "Correlation pairs require two distinct positive integer file IDs."
        )
    return (
        min(left_file_id, right_file_id),
        max(left_file_id, right_file_id),
    )


def interpret_matrix(
    *,
    video: Iterable[FingerprintComparison],
    audio: Iterable[FingerprintComparison],
    policy: CorrelationInterpretationPolicy | None = None,
) -> tuple[PairInterpretation, ...]:
    policy = policy or CorrelationInterpretationPolicy()
    video_by_pair: dict[tuple[int, int], FingerprintComparison] = {}
    audio_by_pair: dict[tuple[int, int], FingerprintComparison] = {}

    for modality, comparisons, target in (
        ("video", video, video_by_pair),
        ("audio", audio, audio_by_pair),
    ):
        for comparison in comparisons:
            if not isinstance(comparison, FingerprintComparison):
                raise CorrelationInterpretationError(
                    f"{modality} correlation matrix contains an invalid record."
                )
            key = _pair_key(
                comparison.left_file_id,
                comparison.right_file_id,
            )
            if key in target:
                raise CorrelationInterpretationError(
                    f"{modality} correlation matrix contains a duplicate pair."
                )
            target[key] = comparison

    pairs = sorted(set(video_by_pair) | set(audio_by_pair))
    return tuple(
        interpret_pair(
            left_file_id=left,
            right_file_id=right,
            video=video_by_pair.get((left, right)),
            audio=audio_by_pair.get((left, right)),
            policy=policy,
        )
        for left, right in pairs
    )

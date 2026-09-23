from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
import hashlib
import json
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

from .external import (
    ExternalAnalysisError,
    ExternalCapability,
    ExternalPreviewUnavailable,
    ExternalSourceFailure,
    ExternalSourceRegistry,
    PreviewFrameRef,
)
from .models import AnalyzerContext, IdentityProfile
from .visual_budget import (
    VisualAttemptBudget,
    VisualBudgetExceeded,
    visual_budget_scope,
)


NORMAL_OCR_CACHE_VERSION = 2


class NormalIdentityError(ValueError):
    """Raised when Normal-profile OCR inputs cannot be handled safely."""


class NormalSamplingStage(IntEnum):
    INITIAL = 1
    EXPANDED = 2
    FINAL = 3


@dataclass(frozen=True)
class NormalResourceLimits:
    """Conservative per-scan ceilings for Normal visual analysis."""

    initial_preview_frames: int = 5
    expanded_preview_frames: int = 9
    max_preview_frames: int = 12
    max_preview_bytes_per_frame: int = 8 * 1024 * 1024
    max_preview_bytes_total: int = 48 * 1024 * 1024
    max_source_bytes_total: int = 48 * 1024 * 1024
    max_ocr_text_chars: int = 64_000

    def __post_init__(self) -> None:
        values = (
            self.initial_preview_frames,
            self.expanded_preview_frames,
            self.max_preview_frames,
            self.max_preview_bytes_per_frame,
            self.max_preview_bytes_total,
            self.max_source_bytes_total,
            self.max_ocr_text_chars,
        )
        if any(int(value) <= 0 for value in values):
            raise NormalIdentityError("Normal OCR resource limits must be positive.")
        if self.initial_preview_frames > self.expanded_preview_frames:
            raise NormalIdentityError(
                "Normal OCR initial frame limit cannot exceed the expanded limit."
            )
        if self.expanded_preview_frames > self.max_preview_frames:
            raise NormalIdentityError(
                "Normal OCR expanded frame limit cannot exceed the final limit."
            )
        if self.max_preview_bytes_per_frame > self.max_preview_bytes_total:
            raise NormalIdentityError(
                "Normal OCR per-frame byte limit cannot exceed its total byte budget."
            )


@dataclass(frozen=True)
class OcrTextResult:
    text: str
    confidence: float | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.confidence is not None and not 0.0 <= float(self.confidence) <= 1.0:
            raise NormalIdentityError("OCR confidence must be between 0 and 1.")


@runtime_checkable
class OcrEngine(Protocol):
    """Optional scene-text engine used by Normal/Deep identity analysis."""

    key: str
    version: str

    def available(self) -> bool:
        """Return whether the optional OCR runtime/model is currently usable."""
        ...

    def cache_identity(self) -> Mapping[str, Any]:
        """Return deterministic output-affecting engine/runtime/model settings."""
        ...

    def recognize(self, image: bytes) -> OcrTextResult:
        """Extract scene text from one bounded image without deciding identity."""
        ...


@dataclass(frozen=True)
class PreviewFrameSample:
    frame: PreviewFrameRef
    stage: NormalSamplingStage
    ordinal: int


_INITIAL_FRACTIONS = (0.05, 0.25, 0.50, 0.75, 0.95)
_EXPANDED_FRACTIONS = (0.125, 0.375, 0.625, 0.875)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _frame_identity(frame: PreviewFrameRef) -> tuple[str, str, int, str, str]:
    return (
        str(frame.source_key or "").strip().casefold(),
        str(frame.item_id or "").strip(),
        int(frame.timestamp_ms),
        str(frame.asset_ref or ""),
        str(frame.source_signature or ""),
    )


def _normalized_frames(
    frames: Sequence[PreviewFrameRef],
) -> tuple[PreviewFrameRef, ...]:
    unique: dict[tuple[str, str, int, str, str], PreviewFrameRef] = {}
    for frame in frames:
        if int(frame.timestamp_ms) < 0:
            raise NormalIdentityError("Preview frame timestamps cannot be negative.")
        identity = _frame_identity(frame)
        if not identity[0] or not identity[1] or not identity[3] or not identity[4]:
            raise NormalIdentityError(
                "Preview frames require source, item, asset, and source-signature provenance."
            )
        unique.setdefault(identity, frame)
    return tuple(
        sorted(
            unique.values(),
            key=lambda frame: (
                int(frame.timestamp_ms),
                str(frame.source_key).casefold(),
                str(frame.item_id),
                str(frame.asset_ref),
                str(frame.source_signature),
            ),
        )
    )


def _nearest_fraction_index(count: int, fraction: float) -> int:
    if count <= 1:
        return 0
    return max(0, min(count - 1, int(round(float(fraction) * (count - 1)))))


def _even_fill_indices(
    count: int,
    desired_total: int,
    selected: set[int],
) -> list[int]:
    if desired_total <= len(selected) or count <= len(selected):
        return []
    candidates = [index for index in range(count) if index not in selected]
    needed = min(desired_total - len(selected), len(candidates))
    if needed <= 0:
        return []
    if needed == len(candidates):
        return candidates

    chosen: list[int] = []
    available = set(candidates)
    for slot in range(needed):
        target = ((slot + 0.5) / needed) * max(count - 1, 1)
        index = min(
            available,
            key=lambda candidate: (abs(candidate - target), candidate),
        )
        chosen.append(index)
        available.remove(index)
    return sorted(chosen)


def select_staged_preview_frames(
    frames: Sequence[PreviewFrameRef],
    *,
    limits: NormalResourceLimits | None = None,
) -> tuple[PreviewFrameSample, ...]:
    """Select deterministic, progressively denser preview samples.

    The stages are nested conceptually but returned only once per frame. H2 can
    process INITIAL first, stop when evidence is sufficient, then continue into
    EXPANDED and FINAL without changing frame identities or cache keys.
    """
    policy = limits or NormalResourceLimits()
    ordered = _normalized_frames(frames)
    if not ordered:
        return ()

    selected: set[int] = set()
    staged: list[tuple[int, NormalSamplingStage]] = []

    def add_indices(
        indices: Sequence[int],
        stage: NormalSamplingStage,
        desired_total: int,
    ) -> None:
        target_total = min(int(desired_total), policy.max_preview_frames, len(ordered))
        for index in sorted(set(int(value) for value in indices)):
            if index in selected:
                continue
            if len(selected) >= target_total:
                return
            selected.add(index)
            staged.append((index, stage))

    initial = [
        _nearest_fraction_index(len(ordered), fraction)
        for fraction in _INITIAL_FRACTIONS
    ]
    add_indices(
        initial,
        NormalSamplingStage.INITIAL,
        policy.initial_preview_frames,
    )
    add_indices(
        _even_fill_indices(
            len(ordered),
            min(policy.initial_preview_frames, len(ordered)),
            selected,
        ),
        NormalSamplingStage.INITIAL,
        policy.initial_preview_frames,
    )

    expanded = [
        _nearest_fraction_index(len(ordered), fraction)
        for fraction in _EXPANDED_FRACTIONS
    ]
    add_indices(
        expanded,
        NormalSamplingStage.EXPANDED,
        policy.expanded_preview_frames,
    )
    add_indices(
        _even_fill_indices(
            len(ordered),
            min(policy.expanded_preview_frames, len(ordered)),
            selected,
        ),
        NormalSamplingStage.EXPANDED,
        policy.expanded_preview_frames,
    )

    add_indices(
        _even_fill_indices(
            len(ordered),
            min(policy.max_preview_frames, len(ordered)),
            selected,
        ),
        NormalSamplingStage.FINAL,
        policy.max_preview_frames,
    )

    samples = [
        PreviewFrameSample(
            frame=ordered[index],
            stage=stage,
            ordinal=ordinal,
        )
        for ordinal, (index, stage) in enumerate(staged, start=1)
    ]
    return tuple(samples)


def ocr_preview_cache_key(
    frame: PreviewFrameRef,
    engine: OcrEngine,
    *,
    parameters: Mapping[str, Any] | None = None,
    preview_sha256: str = "",
) -> str:
    """Return a deterministic artifact key for derived OCR text.

    Source preview bytes themselves are not copied merely for inspection. The
    reusable boundary is the OCR result tied to the exact external asset
    signature, engine version, and OCR parameters.
    """
    key = str(getattr(engine, "key", "") or "").strip().casefold()
    version = str(getattr(engine, "version", "") or "").strip()
    if not key or not version:
        raise NormalIdentityError("OCR engines require stable key and version values.")
    try:
        engine_identity = dict(engine.cache_identity())
    except (AttributeError, TypeError, ValueError) as exc:
        raise NormalIdentityError(
            "OCR engines require a deterministic cache identity."
        ) from exc
    if not engine_identity:
        raise NormalIdentityError(
            "OCR engines require a deterministic cache identity."
        )

    payload = {
        "cache_version": NORMAL_OCR_CACHE_VERSION,
        "engine": {
            "key": key,
            "version": version,
            "identity": engine_identity,
        },
        "frame": {
            "source_key": str(frame.source_key or "").strip().casefold(),
            "item_id": str(frame.item_id or "").strip(),
            "timestamp_ms": int(frame.timestamp_ms),
            "asset_ref": str(frame.asset_ref or ""),
            "source_signature": str(frame.source_signature or ""),
            "width": frame.width,
            "height": frame.height,
            "content_sha256": str(preview_sha256 or ""),
        },
        "parameters": dict(parameters or {}),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PreviewOcrObservation:
    source_key: str
    item_id: str
    timestamp_ms: int
    stage: NormalSamplingStage
    ordinal: int
    cache_key: str
    source_signature: str
    asset_ref: str
    text: str
    confidence: float | None
    image_bytes: int
    reused: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalPreviewOcrRun:
    source_key: str = ""
    observations: tuple[PreviewOcrObservation, ...] = ()
    failures: tuple[str, ...] = ()
    total_image_bytes: int = 0
    total_source_bytes: int = 0
    total_text_chars: int = 0
    total_frame_attempts: int = 0
    budget_exhausted: bool = False
    planned_frame_count: int = 0
    completed_frame_count: int = 0
    coverage_complete: bool = False

    @property
    def has_text(self) -> bool:
        return any(item.text.strip() for item in self.observations)


class NormalPreviewOcrExecutor:
    """Run bounded OCR against the first usable external preview source.

    External sources are tried in registry order. A source may be unavailable,
    malformed, textless, or too weak without making Normal verification fail.
    Callers may provide source-level sufficiency/preference callbacks so existing
    preview sources can be compared before more expensive local fallback.
    """

    def __init__(
        self,
        registry: ExternalSourceRegistry,
        engine: OcrEngine,
        *,
        limits: NormalResourceLimits | None = None,
        cache_lookup: Callable[
            [PreviewFrameRef, str], OcrTextResult | None
        ] | None = None,
    ) -> None:
        self.registry = registry
        self.engine = engine
        self.limits = limits or NormalResourceLimits()
        self.cache_lookup = cache_lookup

    def run(
        self,
        context: AnalyzerContext,
        *,
        max_stage: NormalSamplingStage = NormalSamplingStage.FINAL,
        stage_sufficient: Callable[
            [NormalPreviewOcrRun, NormalSamplingStage], bool
        ] | None = None,
        source_sufficient: Callable[
            [NormalPreviewOcrRun], bool
        ] | None = None,
        source_preference: Callable[
            [NormalPreviewOcrRun, NormalPreviewOcrRun], NormalPreviewOcrRun
        ] | None = None,
        initial_image_bytes: int = 0,
        initial_text_chars: int = 0,
        budget: VisualAttemptBudget | None = None,
    ) -> NormalPreviewOcrRun:
        if not IdentityProfile.parse(context.profile).permits(IdentityProfile.NORMAL):
            raise NormalIdentityError(
                "External preview OCR requires the Normal or Deep identity profile."
            )
        if not self.engine.available():
            return NormalPreviewOcrRun(
                failures=("ocr-engine-unavailable",),
            )

        if budget is not None and (
            int(initial_image_bytes) != 0 or int(initial_text_chars) != 0
        ):
            raise NormalIdentityError(
                "Shared Normal visual budgets cannot also use initial counters."
            )
        if budget is None:
            try:
                budget = VisualAttemptBudget(
                    max_frame_attempts=int(self.limits.max_preview_frames),
                    max_source_bytes=int(self.limits.max_source_bytes_total),
                    max_image_bytes=int(self.limits.max_preview_bytes_total),
                    max_text_chars=int(self.limits.max_ocr_text_chars),
                    image_bytes=max(0, int(initial_image_bytes)),
                    text_chars=max(0, int(initial_text_chars)),
                )
            except ValueError as exc:
                raise NormalIdentityError(str(exc)) from exc

        if budget.exhausted:
            return NormalPreviewOcrRun(
                failures=("normal:resource-budget-exhausted",),
                total_image_bytes=budget.image_bytes,
                total_source_bytes=budget.source_bytes,
                total_text_chars=budget.text_chars,
                total_frame_attempts=budget.frame_attempts,
                budget_exhausted=True,
            )

        failures: list[str] = []
        best_result: NormalPreviewOcrRun | None = None

        def choose_result(
            current: NormalPreviewOcrRun | None,
            candidate: NormalPreviewOcrRun,
        ) -> NormalPreviewOcrRun:
            if current is None:
                return candidate
            if source_preference is not None:
                return source_preference(current, candidate)
            if candidate.has_text and not current.has_text:
                return candidate
            return current

        for source in self.registry.available_for(ExternalCapability.PREVIEW_FRAMES):
            try:
                with visual_budget_scope(budget):
                    media = source.resolve_media(context)
            except ExternalAnalysisError as exc:
                failures.append(f"{source.source_key}:resolve:{exc}")
                continue
            if media is None:
                continue
            try:
                with visual_budget_scope(budget):
                    frames = tuple(source.preview_frames(media))
            except ExternalAnalysisError as exc:
                failures.append(f"{source.source_key}:preview-list:{exc}")
                continue
            if not frames:
                continue

            try:
                samples = tuple(
                    sample
                    for sample in select_staged_preview_frames(
                        frames,
                        limits=self.limits,
                    )
                    if sample.stage <= max_stage
                )
            except NormalIdentityError as exc:
                failures.append(
                    f"{source.source_key}:preview-normalize:{exc}"
                )
                continue
            result = self._run_source(
                source,
                samples,
                failures,
                cache_parameters={
                    "file_id": int(context.media.file_id),
                    "size_bytes": int(context.media.size_bytes),
                    "modified_at": context.media.modified_at,
                    "sha256": context.media.sha256 or "",
                },
                stage_sufficient=stage_sufficient,
                budget=budget,
            )
            failures = list(result.failures)
            aggregate_exhausted = budget.exhausted
            if aggregate_exhausted and not result.budget_exhausted:
                failures.append("normal:resource-budget-exhausted")
                result = NormalPreviewOcrRun(
                    source_key=result.source_key,
                    observations=result.observations,
                    failures=tuple(failures),
                    total_image_bytes=budget.image_bytes,
                    total_source_bytes=budget.source_bytes,
                    total_text_chars=budget.text_chars,
                    total_frame_attempts=budget.frame_attempts,
                    budget_exhausted=True,
                    planned_frame_count=result.planned_frame_count,
                    completed_frame_count=result.completed_frame_count,
                    coverage_complete=False,
                )
            if result.has_text:
                best_result = choose_result(best_result, result)
                if (
                    source_sufficient is None
                    or source_sufficient(result)
                ):
                    return result
                failures.append(
                    f"{source.source_key}:ocr:weak-visual-signal"
                )
            elif result.observations:
                best_result = choose_result(best_result, result)
                failures.append(f"{source.source_key}:ocr:no-visual-text")

            if result.budget_exhausted or budget.exhausted:
                selected = best_result or result
                return NormalPreviewOcrRun(
                    source_key=selected.source_key,
                    observations=selected.observations,
                    failures=tuple(failures),
                    total_image_bytes=budget.image_bytes,
                    total_source_bytes=budget.source_bytes,
                    total_text_chars=budget.text_chars,
                    total_frame_attempts=budget.frame_attempts,
                    budget_exhausted=True,
                    planned_frame_count=selected.planned_frame_count,
                    completed_frame_count=selected.completed_frame_count,
                    coverage_complete=False,
                )

        if best_result is not None:
            aggregate_exhausted = (
                best_result.budget_exhausted or budget.exhausted
            )
            return NormalPreviewOcrRun(
                source_key=best_result.source_key,
                observations=best_result.observations,
                failures=tuple(failures),
                total_image_bytes=budget.image_bytes,
                    total_source_bytes=budget.source_bytes,
                total_text_chars=budget.text_chars,
                    total_frame_attempts=budget.frame_attempts,
                budget_exhausted=aggregate_exhausted,
                planned_frame_count=best_result.planned_frame_count,
                completed_frame_count=best_result.completed_frame_count,
                coverage_complete=(
                    best_result.coverage_complete and not aggregate_exhausted
                ),
            )
        return NormalPreviewOcrRun(
            failures=tuple(failures),
            total_image_bytes=budget.image_bytes,
                    total_source_bytes=budget.source_bytes,
            total_text_chars=budget.text_chars,
                    total_frame_attempts=budget.frame_attempts,
            budget_exhausted=budget.exhausted,
        )

    def _run_source(
        self,
        source: Any,
        samples: Sequence[PreviewFrameSample],
        prior_failures: Sequence[str],
        *,
        cache_parameters: Mapping[str, Any],
        stage_sufficient: Callable[
            [NormalPreviewOcrRun, NormalSamplingStage], bool
        ] | None,
        budget: VisualAttemptBudget,
    ) -> NormalPreviewOcrRun:
        observations: list[PreviewOcrObservation] = []
        failures = list(prior_failures)
        total_image_bytes = int(budget.image_bytes)
        total_text_chars = int(budget.text_chars)
        budget_exhausted = False
        coverage_missing = False
        completed_frame_count = 0
        current_stage: NormalSamplingStage | None = None

        def current_run() -> NormalPreviewOcrRun:
            return NormalPreviewOcrRun(
                source_key=str(getattr(source, "source_key", "") or ""),
                observations=tuple(observations),
                failures=tuple(failures),
                total_image_bytes=budget.image_bytes,
                total_source_bytes=budget.source_bytes,
                total_text_chars=budget.text_chars,
                total_frame_attempts=budget.frame_attempts,
                budget_exhausted=budget_exhausted,
                planned_frame_count=len(samples),
                completed_frame_count=completed_frame_count,
                coverage_complete=(not coverage_missing and not budget_exhausted),
            )

        for sample in samples:
            if current_stage is None:
                current_stage = sample.stage
            elif sample.stage != current_stage:
                partial = current_run()
                if (
                    stage_sufficient is not None
                    and partial.has_text
                    and stage_sufficient(partial, current_stage)
                ):
                    return partial
                current_stage = sample.stage
            try:
                budget.reserve_frame_attempt()
                with visual_budget_scope(budget):
                    payload = bytes(source.read_preview(sample.frame))
            except VisualBudgetExceeded as exc:
                coverage_missing = True
                budget_exhausted = True
                failures.append(
                    f"{source.source_key}:budget:{exc}"
                )
                break
            except ExternalPreviewUnavailable as exc:
                coverage_missing = True
                failures.append(
                    f"{source.source_key}:preview:{sample.ordinal}:{exc}"
                )
                continue
            except ExternalSourceFailure as exc:
                coverage_missing = True
                failures.append(f"{source.source_key}:source:{exc}")
                break
            except ExternalAnalysisError as exc:
                coverage_missing = True
                failures.append(
                    f"{source.source_key}:preview:{sample.ordinal}:{exc}"
                )
                continue

            frame_bytes = len(payload)
            if frame_bytes <= 0:
                coverage_missing = True
                failures.append(
                    f"{source.source_key}:preview:{sample.ordinal}:empty"
                )
                continue
            if frame_bytes > self.limits.max_preview_bytes_per_frame:
                coverage_missing = True
                failures.append(
                    f"{source.source_key}:preview:{sample.ordinal}:frame-byte-limit"
                )
                continue
            try:
                budget.reserve_image_bytes(frame_bytes)
            except VisualBudgetExceeded:
                coverage_missing = True
                budget_exhausted = True
                failures.append(f"{source.source_key}:total-preview-byte-limit")
                break

            total_image_bytes = budget.image_bytes
            preview_sha256 = hashlib.sha256(payload).hexdigest()
            cache_key = ocr_preview_cache_key(
                sample.frame,
                self.engine,
                parameters=cache_parameters,
                preview_sha256=preview_sha256,
            )
            cached = (
                self.cache_lookup(sample.frame, cache_key)
                if self.cache_lookup is not None
                else None
            )
            if cached is not None:
                remaining_chars = budget.remaining_text_chars
                if remaining_chars <= 0:
                    coverage_missing = True
                    budget_exhausted = True
                    failures.append(f"{source.source_key}:total-ocr-text-limit")
                    break
                text = str(cached.text or "")
                if len(text) > remaining_chars:
                    text = text[:remaining_chars]
                    budget_exhausted = True
                budget.reserve_text_chars(len(text))
                total_text_chars = budget.text_chars
                cached_details = dict(cached.details)
                cached_details["preview_sha256"] = preview_sha256
                observations.append(
                    PreviewOcrObservation(
                        source_key=str(sample.frame.source_key),
                        item_id=str(sample.frame.item_id),
                        timestamp_ms=int(sample.frame.timestamp_ms),
                        stage=sample.stage,
                        ordinal=sample.ordinal,
                        cache_key=cache_key,
                        source_signature=str(sample.frame.source_signature),
                        asset_ref=str(sample.frame.asset_ref),
                        text=text,
                        confidence=cached.confidence,
                        image_bytes=frame_bytes,
                        reused=True,
                        details=cached_details,
                    )
                )
                completed_frame_count += 1
                if budget_exhausted:
                    failures.append(f"{source.source_key}:total-ocr-text-limit")
                    break
                continue

            try:
                result = self.engine.recognize(payload)
            except NormalIdentityError as exc:
                coverage_missing = True
                failures.append(
                    f"{source.source_key}:ocr:{sample.ordinal}:{exc}"
                )
                continue

            remaining_chars = budget.remaining_text_chars
            if remaining_chars <= 0:
                coverage_missing = True
                budget_exhausted = True
                failures.append(f"{source.source_key}:total-ocr-text-limit")
                break

            text = str(result.text or "")
            if len(text) > remaining_chars:
                text = text[:remaining_chars]
                budget_exhausted = True
            budget.reserve_text_chars(len(text))
            total_text_chars = budget.text_chars

            observations.append(
                PreviewOcrObservation(
                    source_key=str(sample.frame.source_key),
                    item_id=str(sample.frame.item_id),
                    timestamp_ms=int(sample.frame.timestamp_ms),
                    stage=sample.stage,
                    ordinal=sample.ordinal,
                    cache_key=cache_key,
                    source_signature=str(sample.frame.source_signature),
                    asset_ref=str(sample.frame.asset_ref),
                    text=text,
                    confidence=result.confidence,
                    image_bytes=frame_bytes,
                    details={
                        **dict(result.details),
                        "preview_sha256": preview_sha256,
                    },
                )
            )
            completed_frame_count += 1
            if budget_exhausted:
                failures.append(f"{source.source_key}:total-ocr-text-limit")
                break

        return current_run()


from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence

from .external import PreviewFrameRef
from .normal import NormalIdentityError, NormalResourceLimits, select_staged_preview_frames
from .versions import DEEP_SAMPLING_ALGORITHM_VERSION


MAX_DEEP_VISUAL_FRAMES = 40
MAX_DEEP_VISUAL_BYTES_TOTAL = 256 * 1024 * 1024
MAX_DEEP_VISUAL_TEXT_CHARS = 512_000


class DeepSamplingError(ValueError):
    """Raised when a Deep sampling plan violates its deterministic bounds."""


def _strict_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DeepSamplingError(f"{name} must be an integer.")
    return value


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DeepSamplingError(
            "Deep sampling identity must be JSON-safe."
        ) from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DeepSamplingPolicy:
    """Bounded visual-work budget for one Deep episode verification."""

    visual_frame_count: int = 24
    max_preview_bytes_per_frame: int = 8 * 1024 * 1024
    max_preview_bytes_total: int = 128 * 1024 * 1024
    max_source_bytes_total: int = 128 * 1024 * 1024
    max_ocr_text_chars: int = 128_000

    def __post_init__(self) -> None:
        frame_count = _strict_int(
            self.visual_frame_count,
            name="Deep visual frame count",
        )
        per_frame = _strict_int(
            self.max_preview_bytes_per_frame,
            name="Deep per-frame byte limit",
        )
        total = _strict_int(
            self.max_preview_bytes_total,
            name="Deep image-byte limit",
        )
        source_total = _strict_int(
            self.max_source_bytes_total,
            name="Deep source-byte limit",
        )
        text_chars = _strict_int(
            self.max_ocr_text_chars,
            name="Deep OCR-text limit",
        )
        if frame_count < 1 or frame_count > MAX_DEEP_VISUAL_FRAMES:
            raise DeepSamplingError(
                "Deep visual frame count exceeds the supported bound."
            )
        if per_frame < 1 or per_frame > 16 * 1024 * 1024:
            raise DeepSamplingError(
                "Deep per-frame byte limit exceeds the supported bound."
            )
        if total < per_frame or total > MAX_DEEP_VISUAL_BYTES_TOTAL:
            raise DeepSamplingError(
                "Deep image-byte limit is outside the supported bound."
            )
        if source_total < per_frame or source_total > MAX_DEEP_VISUAL_BYTES_TOTAL:
            raise DeepSamplingError(
                "Deep source-byte limit is outside the supported bound."
            )
        if text_chars < 1 or text_chars > MAX_DEEP_VISUAL_TEXT_CHARS:
            raise DeepSamplingError(
                "Deep OCR-text limit exceeds the supported bound."
            )

    def identity_payload(self) -> dict[str, int]:
        return {
            "visual_frame_count": self.visual_frame_count,
            "max_preview_bytes_per_frame": self.max_preview_bytes_per_frame,
            "max_preview_bytes_total": self.max_preview_bytes_total,
            "max_source_bytes_total": self.max_source_bytes_total,
            "max_ocr_text_chars": self.max_ocr_text_chars,
        }

    @classmethod
    def from_payload(cls, value: object) -> "DeepSamplingPolicy":
        if not isinstance(value, Mapping):
            raise DeepSamplingError("Deep sampling policy metadata is missing.")
        try:
            return cls(
                visual_frame_count=value["visual_frame_count"],
                max_preview_bytes_per_frame=value[
                    "max_preview_bytes_per_frame"
                ],
                max_preview_bytes_total=value["max_preview_bytes_total"],
                max_source_bytes_total=value["max_source_bytes_total"],
                max_ocr_text_chars=value["max_ocr_text_chars"],
            )
        except KeyError as exc:
            raise DeepSamplingError(
                "Deep sampling policy metadata is incomplete."
            ) from exc


@dataclass(frozen=True)
class DeepVisualSample:
    frame: PreviewFrameRef
    ordinal: int
    inherited_normal: bool
    work_key: str


@dataclass(frozen=True)
class DeepVisualPlan:
    samples: tuple[DeepVisualSample, ...]
    policy: DeepSamplingPolicy
    plan_signature: str

    @property
    def planned_frame_count(self) -> int:
        return len(self.samples)


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
        if not isinstance(frame, PreviewFrameRef):
            raise DeepSamplingError(
                "Deep visual sampling requires PreviewFrameRef values."
            )
        if int(frame.timestamp_ms) < 0:
            raise DeepSamplingError(
                "Deep preview timestamps cannot be negative."
            )
        identity = _frame_identity(frame)
        if not identity[0] or not identity[1] or not identity[3] or not identity[4]:
            raise DeepSamplingError(
                "Deep previews require source, item, asset, and source-signature provenance."
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


def _frame_payload(frame: PreviewFrameRef) -> dict[str, Any]:
    return {
        "source_key": str(frame.source_key or "").strip().casefold(),
        "item_id": str(frame.item_id or "").strip(),
        "timestamp_ms": int(frame.timestamp_ms),
        "asset_ref": str(frame.asset_ref or ""),
        "source_signature": str(frame.source_signature or ""),
        "width": frame.width,
        "height": frame.height,
    }


def _sample_work_key(
    frame: PreviewFrameRef,
    *,
    ordinal: int,
) -> str:
    return _digest({
        "version": DEEP_SAMPLING_ALGORITHM_VERSION,
        "kind": "deep-visual-work-item",
        "ordinal": int(ordinal),
        "frame": _frame_payload(frame),
    })


def _farthest_fill_indices(
    count: int,
    selected: set[int],
    desired_total: int,
) -> list[int]:
    """Choose remaining indices by deterministic maximin coverage."""

    result: list[int] = []
    target = min(max(0, int(desired_total)), count)
    while len(selected) < target:
        remaining = [index for index in range(count) if index not in selected]
        if not remaining:
            break
        if selected:
            index = max(
                remaining,
                key=lambda candidate: (
                    min(abs(candidate - chosen) for chosen in selected),
                    -candidate,
                ),
            )
        else:
            index = (count - 1) // 2
        selected.add(index)
        result.append(index)
    return result


def build_deep_visual_plan(
    frames: Sequence[PreviewFrameRef],
    *,
    policy: DeepSamplingPolicy | None = None,
) -> DeepVisualPlan:
    """Build a denser plan while preserving Normal's first-pass sample set.

    Normal-selected frames are always scheduled first when they exist. Deep then
    fills the largest remaining timeline gaps until its explicit frame bound is
    reached. This gives interrupted runs useful broad coverage and maximizes
    exact artifact reuse from a prior Normal pass.
    """

    policy = policy or DeepSamplingPolicy()
    ordered = _normalized_frames(frames)
    if not ordered:
        payload = {
            "version": DEEP_SAMPLING_ALGORITHM_VERSION,
            "kind": "deep-visual-plan",
            "policy": policy.identity_payload(),
            "samples": [],
        }
        return DeepVisualPlan(
            samples=(),
            policy=policy,
            plan_signature=_digest(payload),
        )

    target = min(policy.visual_frame_count, len(ordered))
    index_by_identity = {
        _frame_identity(frame): index
        for index, frame in enumerate(ordered)
    }

    try:
        normal_samples = select_staged_preview_frames(
            ordered,
            limits=NormalResourceLimits(),
        )
    except NormalIdentityError as exc:
        raise DeepSamplingError(str(exc)) from exc

    selected: set[int] = set()
    scheduled: list[tuple[int, bool]] = []
    for sample in normal_samples:
        index = index_by_identity.get(_frame_identity(sample.frame))
        if index is None or index in selected:
            continue
        if len(scheduled) >= target:
            break
        selected.add(index)
        scheduled.append((index, True))

    for index in _farthest_fill_indices(len(ordered), selected, target):
        scheduled.append((index, False))

    samples: list[DeepVisualSample] = []
    for ordinal, (index, inherited) in enumerate(scheduled, start=1):
        frame = ordered[index]
        samples.append(
            DeepVisualSample(
                frame=frame,
                ordinal=ordinal,
                inherited_normal=inherited,
                work_key=_sample_work_key(frame, ordinal=ordinal),
            )
        )

    payload = {
        "version": DEEP_SAMPLING_ALGORITHM_VERSION,
        "kind": "deep-visual-plan",
        "policy": policy.identity_payload(),
        "samples": [
            {
                "ordinal": item.ordinal,
                "inherited_normal": item.inherited_normal,
                "work_key": item.work_key,
                "frame": _frame_payload(item.frame),
            }
            for item in samples
        ],
    }
    return DeepVisualPlan(
        samples=tuple(samples),
        policy=policy,
        plan_signature=_digest(payload),
    )

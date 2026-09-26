from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Mapping

from .speech import SpeechWindow
from .speech_audio import validate_speech_window_plan
from .speech_service import (
    NORMAL_SPEECH_WINDOW_MS,
    plan_normal_speech_windows,
)
from .versions import DEEP_SPEECH_SAMPLING_VERSION


MAX_DEEP_SPEECH_WINDOWS = 16
MAX_DEEP_SPEECH_TOTAL_MS = 480_000
MAX_DEEP_SPEECH_AUDIO_BYTES = 18 * 1024 * 1024


class DeepSpeechError(ValueError):
    """Raised when Deep speech sampling exceeds its deterministic contract."""


def _strict_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DeepSpeechError(f"{name} must be an integer.")
    return value


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DeepSpeechError(
            "Deep speech identity must be JSON-safe."
        ) from exc


def _digest(value: object) -> str:
    return hashlib.sha256(
        _canonical_json(value).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class DeepSpeechPolicy:
    max_windows: int = MAX_DEEP_SPEECH_WINDOWS
    max_total_ms: int = MAX_DEEP_SPEECH_TOTAL_MS
    max_audio_bytes: int = MAX_DEEP_SPEECH_AUDIO_BYTES

    def __post_init__(self) -> None:
        max_windows = _strict_int(
            self.max_windows,
            name="Deep speech window limit",
        )
        max_total_ms = _strict_int(
            self.max_total_ms,
            name="Deep speech duration limit",
        )
        max_audio_bytes = _strict_int(
            self.max_audio_bytes,
            name="Deep speech audio-byte limit",
        )
        if max_windows < 8 or max_windows > MAX_DEEP_SPEECH_WINDOWS:
            raise DeepSpeechError(
                "Deep speech window limit is outside the supported bound."
            )
        if max_total_ms < 240_000 or max_total_ms > MAX_DEEP_SPEECH_TOTAL_MS:
            raise DeepSpeechError(
                "Deep speech duration limit is outside the supported bound."
            )
        if (
            max_audio_bytes < 9 * 1024 * 1024
            or max_audio_bytes > MAX_DEEP_SPEECH_AUDIO_BYTES
        ):
            raise DeepSpeechError(
                "Deep speech audio-byte limit is outside the supported bound."
            )

    def identity_payload(self) -> dict[str, int]:
        return {
            "max_windows": self.max_windows,
            "max_total_ms": self.max_total_ms,
            "max_audio_bytes": self.max_audio_bytes,
        }

    @classmethod
    def from_payload(cls, value: object) -> "DeepSpeechPolicy":
        if not isinstance(value, Mapping):
            raise DeepSpeechError("Deep speech policy metadata is missing.")
        try:
            return cls(
                max_windows=value["max_windows"],
                max_total_ms=value["max_total_ms"],
                max_audio_bytes=value["max_audio_bytes"],
            )
        except KeyError as exc:
            raise DeepSpeechError(
                "Deep speech policy metadata is incomplete."
            ) from exc


@dataclass(frozen=True)
class DeepSpeechSample:
    window: SpeechWindow
    ordinal: int
    inherited_normal: bool
    work_key: str


@dataclass(frozen=True)
class DeepSpeechPlan:
    samples: tuple[DeepSpeechSample, ...]
    policy: DeepSpeechPolicy
    plan_signature: str

    @property
    def windows(self) -> tuple[SpeechWindow, ...]:
        return tuple(item.window for item in self.samples)


def _runtime_ms(runtime_seconds: Any) -> int:
    if isinstance(runtime_seconds, bool):
        return 0
    try:
        runtime_value = float(runtime_seconds)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(runtime_value) or runtime_value <= 0:
        return 0
    return max(1, int(round(runtime_value * 1000.0)))


def _overlaps(left: SpeechWindow, right: SpeechWindow) -> bool:
    return left.start_ms < right.end_ms and right.start_ms < left.end_ms


def _center(window: SpeechWindow) -> float:
    return (window.start_ms + window.end_ms) / 2.0


def _candidate_windows(runtime_ms: int) -> tuple[SpeechWindow, ...]:
    duration = min(NORMAL_SPEECH_WINDOW_MS, runtime_ms)
    # A bounded candidate lattice gives the maximin filler enough choices to
    # avoid Normal windows without making planning proportional to runtime.
    segment_count = MAX_DEEP_SPEECH_WINDOWS * 4
    result: list[SpeechWindow] = []
    seen: set[tuple[int, int]] = set()
    segment_width = runtime_ms / float(segment_count)
    for index in range(segment_count):
        center = int(round((index + 0.5) * segment_width))
        start = max(0, min(runtime_ms - duration, center - duration // 2))
        end = min(runtime_ms, start + duration)
        identity = (start, end)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(
            SpeechWindow(
                start_ms=start,
                end_ms=end,
                purpose="deep-candidate",
            )
        )
    return tuple(result)


def build_deep_speech_plan(
    runtime_seconds: Any,
    *,
    policy: DeepSpeechPolicy | None = None,
) -> DeepSpeechPlan:
    """Preserve Normal coverage, then fill the largest remaining timeline gaps."""

    policy = policy or DeepSpeechPolicy()
    runtime_ms = _runtime_ms(runtime_seconds)
    if runtime_ms <= 0:
        payload = {
            "version": DEEP_SPEECH_SAMPLING_VERSION,
            "policy": policy.identity_payload(),
            "runtime_ms": 0,
            "samples": [],
        }
        return DeepSpeechPlan(
            samples=(),
            policy=policy,
            plan_signature=_digest(payload),
        )

    normal_windows = plan_normal_speech_windows(runtime_seconds)
    selected: list[SpeechWindow] = list(normal_windows)
    if (
        len(selected) > policy.max_windows
        or sum(item.duration_ms for item in selected) > policy.max_total_ms
    ):
        raise DeepSpeechError(
            "Deep speech policy cannot contain the inherited Normal plan."
        )

    target_ms = min(runtime_ms, policy.max_total_ms)
    # Normal already covers every byte of short runtimes. Extra overlapping
    # windows would only spend CPU without adding temporal evidence.
    if sum(item.duration_ms for item in selected) < target_ms:
        candidates = list(_candidate_windows(runtime_ms))
        while (
            len(selected) < policy.max_windows
            and sum(item.duration_ms for item in selected) < target_ms
        ):
            eligible = [
                candidate
                for candidate in candidates
                if not any(_overlaps(candidate, existing) for existing in selected)
            ]
            if not eligible:
                break
            candidate = max(
                eligible,
                key=lambda item: (
                    min(
                        abs(_center(item) - _center(existing))
                        for existing in selected
                    ) if selected else float("inf"),
                    -item.start_ms,
                ),
            )
            remaining_ms = target_ms - sum(
                item.duration_ms for item in selected
            )
            if candidate.duration_ms > remaining_ms:
                start = candidate.start_ms
                end = min(runtime_ms, start + remaining_ms)
                if end <= start:
                    break
                candidate = SpeechWindow(
                    start_ms=start,
                    end_ms=end,
                    purpose="deep-candidate",
                )
                if any(_overlaps(candidate, existing) for existing in selected):
                    break
            selected.append(candidate)
            candidates = [
                item
                for item in candidates
                if (item.start_ms, item.end_ms)
                != (candidate.start_ms, candidate.end_ms)
            ]

    try:
        validated = validate_speech_window_plan(
            selected,
            max_windows=policy.max_windows,
            max_total_ms=policy.max_total_ms,
            profile_label="Deep",
        )
    except Exception as exc:
        raise DeepSpeechError(str(exc)) from exc

    normal_identities = {
        (item.start_ms, item.end_ms)
        for item in normal_windows
    }
    samples: list[DeepSpeechSample] = []
    for ordinal, window in enumerate(validated, start=1):
        inherited = (
            window.start_ms,
            window.end_ms,
        ) in normal_identities
        planned = SpeechWindow(
            start_ms=window.start_ms,
            end_ms=window.end_ms,
            purpose=(
                window.purpose
                if inherited
                else f"deep-target-{ordinal}"
            ),
        )
        work_key = _digest({
            "version": DEEP_SPEECH_SAMPLING_VERSION,
            "ordinal": ordinal,
            "start_ms": planned.start_ms,
            "end_ms": planned.end_ms,
            "inherited_normal": inherited,
        })
        samples.append(
            DeepSpeechSample(
                window=planned,
                ordinal=ordinal,
                inherited_normal=inherited,
                work_key=work_key,
            )
        )

    payload = {
        "version": DEEP_SPEECH_SAMPLING_VERSION,
        "policy": policy.identity_payload(),
        "runtime_ms": runtime_ms,
        "samples": [
            {
                "ordinal": item.ordinal,
                "start_ms": item.window.start_ms,
                "end_ms": item.window.end_ms,
                "inherited_normal": item.inherited_normal,
                "work_key": item.work_key,
            }
            for item in samples
        ],
    }
    return DeepSpeechPlan(
        samples=tuple(samples),
        policy=policy,
        plan_signature=_digest(payload),
    )

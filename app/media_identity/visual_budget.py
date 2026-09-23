from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator

from .external import ExternalPreviewUnavailable


class VisualBudgetExceeded(ExternalPreviewUnavailable):
    """A shared Normal visual-attempt resource ceiling was reached."""


@dataclass
class VisualAttemptBudget:
    max_frame_attempts: int
    max_source_bytes: int
    max_image_bytes: int
    max_text_chars: int
    frame_attempts: int = 0
    source_bytes: int = 0
    image_bytes: int = 0
    text_chars: int = 0
    blocked: bool = False
    _source_cache: dict[str, bytes] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for label, value in (
            ("frame attempts", self.max_frame_attempts),
            ("source bytes", self.max_source_bytes),
            ("image bytes", self.max_image_bytes),
            ("text characters", self.max_text_chars),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"Visual budget {label} must be a positive integer.")
        for label, value, maximum in (
            ("frame attempts", self.frame_attempts, self.max_frame_attempts),
            ("source bytes", self.source_bytes, self.max_source_bytes),
            ("image bytes", self.image_bytes, self.max_image_bytes),
            ("text characters", self.text_chars, self.max_text_chars),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                or value > maximum
            ):
                raise ValueError(f"Initial visual budget {label} is outside its limit.")

    @property
    def remaining_frame_attempts(self) -> int:
        return max(0, self.max_frame_attempts - self.frame_attempts)

    @property
    def remaining_source_bytes(self) -> int:
        return max(0, self.max_source_bytes - self.source_bytes)

    @property
    def remaining_image_bytes(self) -> int:
        return max(0, self.max_image_bytes - self.image_bytes)

    @property
    def remaining_text_chars(self) -> int:
        return max(0, self.max_text_chars - self.text_chars)

    def _deny(self, detail: str) -> None:
        self.blocked = True
        raise VisualBudgetExceeded(detail)

    def reserve_frame_attempt(self) -> None:
        if self.remaining_frame_attempts <= 0:
            self._deny("Normal visual frame-attempt budget is exhausted.")
        self.frame_attempts += 1

    def reserve_source_bytes(self, amount: int) -> None:
        value = int(amount)
        if value < 0 or value > self.remaining_source_bytes:
            self._deny("Normal visual source-byte budget is exhausted.")
        self.source_bytes += value

    def reserve_image_bytes(self, amount: int) -> None:
        value = int(amount)
        if value < 0 or value > self.remaining_image_bytes:
            self._deny("Normal visual OCR-image byte budget is exhausted.")
        self.image_bytes += value

    def reserve_text_chars(self, amount: int) -> None:
        value = int(amount)
        if value < 0 or value > self.remaining_text_chars:
            self._deny("Normal visual OCR-text budget is exhausted.")
        self.text_chars += value

    @property
    def exhausted(self) -> bool:
        return bool(self.blocked)

    def cached_source_asset(self, key: str) -> bytes | None:
        return self._source_cache.get(str(key))

    def cache_source_asset(self, key: str, payload: bytes) -> None:
        self._source_cache[str(key)] = bytes(payload)


_CURRENT_VISUAL_BUDGET: ContextVar[VisualAttemptBudget | None] = ContextVar(
    "infomancer_visual_attempt_budget",
    default=None,
)


@contextmanager
def visual_budget_scope(
    budget: VisualAttemptBudget | None,
) -> Iterator[VisualAttemptBudget | None]:
    token = _CURRENT_VISUAL_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _CURRENT_VISUAL_BUDGET.reset(token)


def current_visual_budget() -> VisualAttemptBudget | None:
    return _CURRENT_VISUAL_BUDGET.get()


def source_read_plan(static_limit: int) -> tuple[int, bool]:
    """Return the body-read ceiling and whether the shared budget tightened it."""
    limit = max(1, int(static_limit))
    budget = current_visual_budget()
    if budget is None:
        return limit, False
    remaining = budget.remaining_source_bytes
    if remaining <= 0:
        budget._deny("Normal visual source-byte budget is exhausted.")
    if remaining <= limit:
        return remaining, True
    return limit, False


def account_source_bytes(amount: int) -> None:
    budget = current_visual_budget()
    if budget is not None:
        budget.reserve_source_bytes(int(amount))

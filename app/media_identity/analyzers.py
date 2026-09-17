from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from .models import (
    AnalyzerContext,
    AnalyzerResult,
    EvidenceCategory,
    IdentityCandidate,
    IdentityProfile,
)


@runtime_checkable
class IdentityAnalyzer(Protocol):
    """Contract for one reusable source of media-identity evidence.

    Analyzers observe and return evidence. They do not decide the final identity,
    create MIE findings, or mutate media. The orchestrator owns progressive profile
    escalation, cache lookup, early stopping, persistence, and final confidence.
    """

    key: str
    version: str
    minimum_profile: IdentityProfile
    evidence_categories: frozenset[EvidenceCategory]

    def available(self, context: AnalyzerContext) -> bool:
        """Return whether dependencies/evidence inputs are usable for this file."""
        ...

    def cache_key(self, context: AnalyzerContext) -> str:
        """Return a deterministic cache identity for this analyzer input."""
        ...

    def analyze(
        self,
        context: AnalyzerContext,
        candidates: Sequence[IdentityCandidate],
    ) -> AnalyzerResult:
        """Observe media/evidence and return structured evidence/artifacts only."""
        ...

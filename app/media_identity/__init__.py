"""Generic media identity verification contracts for InfoMancer 0.9."""

from .analyzers import IdentityAnalyzer
from .external import ExternalAnalysisSource
from .fast import (
    FastIdentityScanError,
    FastIdentityService,
    FastIdentityStaleError,
    FastScanResult,
)
from .models import (
    AnalyzerContext,
    AnalyzerResult,
    EvidenceCategory,
    EvidenceRelation,
    IdentityCandidate,
    IdentityEvidence,
    IdentityProfile,
    IdentityReference,
    IdentityResultState,
    MediaIdentityFile,
)

__all__ = [
    "AnalyzerContext",
    "AnalyzerResult",
    "EvidenceCategory",
    "EvidenceRelation",
    "ExternalAnalysisSource",
    "FastIdentityScanError",
    "FastIdentityService",
    "FastIdentityStaleError",
    "FastScanResult",
    "IdentityAnalyzer",
    "IdentityCandidate",
    "IdentityEvidence",
    "IdentityProfile",
    "IdentityReference",
    "IdentityResultState",
    "MediaIdentityFile",
]

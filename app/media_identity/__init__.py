"""Generic media identity verification contracts for InfoMancer 0.9."""

from .analyzers import IdentityAnalyzer
from .external import ExternalAnalysisSource
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

from .speech import (
    SpeechBinaryIdentity,
    SpeechEngine,
    SpeechIdentityError,
    SpeechModelIdentity,
    SpeechRequest,
    SpeechTranscript,
    SpeechWindow,
    speech_transcript_cache_key,
)

__all__ = [
    "AnalyzerContext",
    "AnalyzerResult",
    "EvidenceCategory",
    "EvidenceRelation",
    "ExternalAnalysisSource",
    "IdentityAnalyzer",
    "IdentityCandidate",
    "IdentityEvidence",
    "IdentityProfile",
    "IdentityReference",
    "IdentityResultState",
    "MediaIdentityFile",
    "SpeechBinaryIdentity",
    "SpeechEngine",
    "SpeechIdentityError",
    "SpeechModelIdentity",
    "SpeechRequest",
    "SpeechTranscript",
    "SpeechWindow",
    "speech_transcript_cache_key",
]

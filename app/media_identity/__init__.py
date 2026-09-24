"""Generic media identity verification contracts for InfoMancer 0.9."""

from .analyzers import IdentityAnalyzer
from .deep import (
    DeepCandidatePlan,
    DeepCandidatePolicy,
    DeepCorrelationPlan,
    DeepCorrelationPolicy,
    DeepFileSnapshot,
    DeepIdentityError,
    generate_deep_episode_candidates,
    plan_deep_correlation,
)

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
    SpeechAudioIdentity,
    SpeechBinaryIdentity,
    SpeechEngine,
    SpeechIdentityError,
    SpeechModelIdentity,
    SpeechRequest,
    SpeechTranscript,
    SpeechWindow,
    speech_transcript_cache_key,
)

from .speech_audio import (
    ExtractedSpeechAudio,
    LocalFfmpegSpeechAudioExtractor,
    SpeechAudioError,
    SpeechAudioStaleError,
    SpeechAudioStream,
    SpeechAudioUnavailable,
    select_speech_audio_stream,
    validate_normal_speech_audio_budget,
    validate_normal_speech_window_plan,
)

__all__ = [
    "AnalyzerContext",
    "AnalyzerResult",
    "DeepCandidatePlan",
    "DeepCandidatePolicy",
    "DeepCorrelationPlan",
    "DeepCorrelationPolicy",
    "DeepFileSnapshot",
    "DeepIdentityError",
    "generate_deep_episode_candidates",
    "plan_deep_correlation",
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
    "ExtractedSpeechAudio",
    "LocalFfmpegSpeechAudioExtractor",
    "SpeechAudioError",
    "SpeechAudioIdentity",
    "SpeechAudioStaleError",
    "SpeechAudioStream",
    "SpeechAudioUnavailable",
    "SpeechBinaryIdentity",
    "SpeechEngine",
    "SpeechIdentityError",
    "SpeechModelIdentity",
    "SpeechRequest",
    "SpeechTranscript",
    "SpeechWindow",
    "select_speech_audio_stream",
    "speech_transcript_cache_key",
    "validate_normal_speech_audio_budget",
    "validate_normal_speech_window_plan",
]

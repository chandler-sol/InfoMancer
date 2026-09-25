from __future__ import annotations

# Persisted semantic versions for Episode Identity conclusions.
#
# Bump the decision version whenever resolver semantics change in a way that can
# alter whether a persisted scan is actionable. Bump the Normal evidence version
# whenever the Normal analyzer changes how visual evidence is selected, weighted,
# or interpreted. Bump the speech orchestration version whenever Normal changes
# whether, where, or how persisted speech fragments are collected/reused. Bump
# the speech evidence version whenever transcript-to-candidate evidence or its
# correlation semantics change. Bump the Deep orchestration version whenever
# candidate widening, cross-file cohort selection, fingerprint orchestration, or
# sequence-correlation semantics change.
EPISODE_IDENTITY_DECISION_ALGORITHM_VERSION = 1
NORMAL_EVIDENCE_ALGORITHM_VERSION = 5
NORMAL_SPEECH_ORCHESTRATION_VERSION = 4
NORMAL_SPEECH_EVIDENCE_ALGORITHM_VERSION = 2

DEEP_ORCHESTRATION_VERSION = 9
DEEP_SAMPLING_ALGORITHM_VERSION = 1

DEEP_SPEECH_SAMPLING_VERSION = 1

DEEP_FINGERPRINT_CONTRACT_VERSION = 2
DEEP_FINGERPRINT_MATCH_VERSION = 2
DEEP_FINGERPRINT_CORRELATION_VERSION = 1
DEEP_CORRELATION_INTERPRETATION_VERSION = 1
DEEP_SEQUENCE_CORRELATION_VERSION = 1
DEEP_PATTERN_CORRELATION_VERSION = 1
DEEP_CORRELATION_ARTIFACT_VERSION = 2

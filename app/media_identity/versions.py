from __future__ import annotations

# Persisted semantic versions for Episode Identity conclusions.
#
# Bump the decision version whenever resolver semantics change in a way that can
# alter whether a persisted scan is actionable. Bump the Normal evidence version
# whenever the Normal analyzer changes how visual evidence is selected, weighted,
# or interpreted.
EPISODE_IDENTITY_DECISION_ALGORITHM_VERSION = 1
NORMAL_EVIDENCE_ALGORITHM_VERSION = 1

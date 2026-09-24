from __future__ import annotations

from typing import Callable

from ..db import Database
from .deep import DeepCorrelationPolicy
from .fingerprint import (
    AUDIO_ENVELOPE_DHASH64_V1,
    FingerprintMatchPolicy,
)
from .fingerprint_audio import (
    LocalAudioFingerprintExtractor,
    plan_audio_fingerprint_timestamps,
)
from .fingerprint_correlation import DeepFingerprintCorrelationService
from .fingerprint_service import DeepFingerprintArtifactService


class DeepAudioFingerprintArtifactService(DeepFingerprintArtifactService):
    """Trusted file-owned artifact service for the J3 audio fingerprint modality."""

    def __init__(
        self,
        database: Database,
        *,
        extractor_factory: Callable[..., LocalAudioFingerprintExtractor] = (
            LocalAudioFingerprintExtractor
        ),
    ) -> None:
        super().__init__(
            database,
            extractor_factory=extractor_factory,
            algorithm=AUDIO_ENVELOPE_DHASH64_V1,
            source_kind="local_ffmpeg_audio",
            timestamp_planner=plan_audio_fingerprint_timestamps,
        )


class DeepAudioFingerprintCorrelationService(
    DeepFingerprintCorrelationService
):
    """Complete candidate-neutral pairwise matrix for audio fingerprints."""

    def __init__(
        self,
        database: Database,
        *,
        artifact_service: DeepAudioFingerprintArtifactService | None = None,
        correlation_policy: DeepCorrelationPolicy | None = None,
        match_policy: FingerprintMatchPolicy | None = None,
    ) -> None:
        super().__init__(
            database,
            artifact_service=(
                artifact_service
                if artifact_service is not None
                else DeepAudioFingerprintArtifactService(database)
            ),
            correlation_policy=correlation_policy,
            match_policy=match_policy,
        )

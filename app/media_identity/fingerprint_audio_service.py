from __future__ import annotations

from typing import Any, Callable, Mapping

from ..db import Database
from .deep import DeepCorrelationPolicy
from .fingerprint import (
    AUDIO_ENVELOPE_DHASH64_V1,
    FingerprintError,
    FingerprintMatchPolicy,
)
from .fingerprint_audio import (
    LocalAudioFingerprintExtractor,
    plan_audio_fingerprint_timestamps,
)
from .fingerprint_correlation import DeepFingerprintCorrelationService
from .fingerprint_service import DeepFingerprintArtifactService
from .speech_audio import (
    SpeechAudioStream,
    SpeechAudioUnavailable,
    normalize_speech_language,
    select_speech_audio_stream,
)


class DeepAudioFingerprintArtifactService(DeepFingerprintArtifactService):
    """Trusted file-owned artifact service for the J3 audio fingerprint modality."""

    def __init__(
        self,
        database: Database,
        *,
        extractor_factory: Callable[..., LocalAudioFingerprintExtractor] = (
            LocalAudioFingerprintExtractor
        ),
        preferred_language: str = "eng",
    ) -> None:
        self.preferred_language = normalize_speech_language(
            preferred_language
        )
        super().__init__(
            database,
            extractor_factory=extractor_factory,
            algorithm=AUDIO_ENVELOPE_DHASH64_V1,
            source_kind="local_ffmpeg_audio",
            timestamp_planner=plan_audio_fingerprint_timestamps,
        )

    def _file_snapshot(self, conn, file_id: int) -> dict[str, Any]:
        snapshot = super()._file_snapshot(conn, file_id)
        rows = [
            dict(row)
            for row in conn.execute(
                """SELECT stream_index,stream_type,codec,language,title,
                          channels,channel_layout,sample_rate,default_flag,
                          forced_flag,hearing_impaired,visual_impaired,
                          commentary,disposition_json
                   FROM media_streams
                   WHERE file_id=? ORDER BY stream_index""",
                (int(file_id),),
            ).fetchall()
        ]
        try:
            stream = select_speech_audio_stream(
                rows,
                preferred_language=self.preferred_language,
            )
        except SpeechAudioUnavailable:
            stream = None
        snapshot["_fingerprint_audio_stream"] = stream
        return snapshot

    def _build_extractor(
        self,
        media,
        snapshot: Mapping[str, Any],
    ):
        stream = snapshot.get("_fingerprint_audio_stream")
        if not isinstance(stream, SpeechAudioStream):
            raise FingerprintError(
                "No usable primary audio stream is available for fingerprinting."
            )
        return self.extractor_factory(
            media,
            int(snapshot["runtime_ms"]),
            stream=stream,
        )

    def _fingerprint_matches_snapshot(
        self,
        fingerprint,
        snapshot: Mapping[str, Any],
    ) -> bool:
        stream = snapshot.get("_fingerprint_audio_stream")
        raw_stream = fingerprint.parameters.get("stream")
        if (
            not isinstance(stream, SpeechAudioStream)
            or not isinstance(raw_stream, Mapping)
        ):
            return False
        return dict(raw_stream) == dict(stream.cache_identity())


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
        preferred_language: str = "eng",
    ) -> None:
        super().__init__(
            database,
            artifact_service=(
                artifact_service
                if artifact_service is not None
                else DeepAudioFingerprintArtifactService(
                    database,
                    preferred_language=preferred_language,
                )
            ),
            correlation_policy=correlation_policy,
            match_policy=match_policy,
        )

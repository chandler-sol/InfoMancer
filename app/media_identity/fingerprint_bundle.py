from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable

from ..db import Database
from .decision_snapshot import result_revision
from .deep import DeepCorrelationPolicy
from .fingerprint import FingerprintMatchPolicy
from .fingerprint_audio_service import (
    DeepAudioFingerprintCorrelationService,
)
from .fingerprint_correlation import (
    DeepFingerprintCorrelationRun,
    DeepFingerprintCorrelationService,
)


class DeepFingerprintBundleError(RuntimeError):
    """Video/audio fingerprint matrices no longer share one sealed Deep baseline."""


@dataclass(frozen=True)
class DeepFingerprintBundleRun:
    scan_id: int
    result_revision: int
    correlation_plan_signature: str
    video: DeepFingerprintCorrelationRun
    audio: DeepFingerprintCorrelationRun

    @property
    def complete_modalities(self) -> tuple[str, ...]:
        result: list[str] = []
        if self.video.coverage_complete:
            result.append("video")
        if self.audio.coverage_complete:
            result.append("audio")
        return tuple(result)


def _claimed(scan: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(str(scan.get("claimed_identity_json") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


class DeepFingerprintBundleService:
    """Prepare independent video/audio J3 matrices on one sealed scan revision."""

    def __init__(
        self,
        database: Database,
        *,
        video_service: DeepFingerprintCorrelationService | None = None,
        audio_service: DeepFingerprintCorrelationService | None = None,
        audio_service_factory: Callable[
            [str],
            DeepFingerprintCorrelationService,
        ] | None = None,
        correlation_policy: DeepCorrelationPolicy | None = None,
        match_policy: FingerprintMatchPolicy | None = None,
    ) -> None:
        self.database = database
        self.correlation_policy = correlation_policy or DeepCorrelationPolicy()
        self.match_policy = match_policy or FingerprintMatchPolicy()
        self.video_service = (
            video_service
            if video_service is not None
            else DeepFingerprintCorrelationService(
                database,
                correlation_policy=self.correlation_policy,
                match_policy=self.match_policy,
            )
        )
        self.audio_service = audio_service
        self.audio_service_factory = audio_service_factory

    def _scan_state(self, scan_id: int) -> tuple[dict[str, Any], int, str]:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM media_identity_scans WHERE id=?",
                (int(scan_id),),
            ).fetchone()
        if row is None:
            raise DeepFingerprintBundleError(
                "Episode Identity scan was not found for fingerprint preparation."
            )
        scan = dict(row)
        revision = result_revision(scan)
        if scan["status"] != "complete" or revision <= 0:
            raise DeepFingerprintBundleError(
                "Fingerprint preparation requires a complete sealed scan."
            )
        claimed = _claimed(scan)
        raw_language = str(
            claimed.get("scan_language") or ""
        ).strip().casefold()
        return scan, revision, raw_language

    def _audio(self, preferred_language: str):
        if self.audio_service is not None:
            return self.audio_service
        if self.audio_service_factory is not None:
            return self.audio_service_factory(preferred_language)
        return DeepAudioFingerprintCorrelationService(
            self.database,
            preferred_language=preferred_language,
            correlation_policy=self.correlation_policy,
            match_policy=self.match_policy,
        )

    def _require_same_revision(
        self,
        scan_id: int,
        expected_revision: int,
    ) -> None:
        _scan, revision, _language = self._scan_state(scan_id)
        if revision != int(expected_revision):
            raise DeepFingerprintBundleError(
                "Episode Identity publication changed between fingerprint modalities."
            )

    def run(self, scan_id: int) -> DeepFingerprintBundleRun:
        scan, revision, preferred_language = self._scan_state(
            int(scan_id)
        )
        video = self.video_service.run(int(scan_id))
        self._require_same_revision(int(scan_id), revision)

        audio = self._audio(preferred_language).run(int(scan_id))
        self._require_same_revision(int(scan_id), revision)

        if (
            video.correlation_plan_signature
            != audio.correlation_plan_signature
        ):
            raise DeepFingerprintBundleError(
                "Video and audio fingerprints were prepared against different "
                "correlation cohorts."
            )
        return DeepFingerprintBundleRun(
            scan_id=int(scan["id"]),
            result_revision=revision,
            correlation_plan_signature=video.correlation_plan_signature,
            video=video,
            audio=audio,
        )

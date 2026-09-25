from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from ..db import Database
from .deep_completion_service import (
    DeepCompletionRun,
    DeepCompletionService,
)
from .deep_correlation_service import (
    DeepCorrelationAnalysisService,
    DeepCorrelationArtifactRun,
)
from .deep_evidence_service import (
    DeepEvidencePromotionRun,
    DeepEvidencePromotionService,
)
from .deep_sampling_service import (
    DeepSamplingRun,
    DeepSamplingService,
)
from .deep_speech_service import (
    DeepSpeechSamplingRun,
    DeepSpeechSamplingService,
)
from .models import IdentityProfile
from .normal_service import (
    NormalIdentityService,
    NormalScanResult,
)
from .scoring import IdentityResolution
from .service import MediaIdentityDecisionService


class DeepVerificationError(RuntimeError):
    """The staged Deep verification workflow cannot continue safely."""


@dataclass(frozen=True)
class DeepVerificationRun:
    scan_id: int
    resumed_from_stage: str
    normal: NormalScanResult | None
    visual: DeepSamplingRun | None
    speech: DeepSpeechSamplingRun | None
    promotion: DeepEvidencePromotionRun | None
    resolution: IdentityResolution | None
    completion: DeepCompletionRun | None
    correlation: DeepCorrelationArtifactRun

    @property
    def completed_revision(self) -> int:
        if self.completion is not None:
            return self.completion.completed_revision
        return self.correlation.result_revision


def _json_object(value: object) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


class DeepVerificationService:
    """Resume-safe coordinator for the complete per-file Deep verification path.

    J2 visual/speech services are optional because Deep candidate widening and
    fingerprint/correlation analysis remain valid when local OCR/transcription
    is unavailable. A raw Fast scan must still pass through the Normal attempt
    so the inherited Normal provenance is explicit.
    """

    def __init__(
        self,
        database: Database,
        *,
        normal_service: NormalIdentityService | None,
        visual_service: DeepSamplingService | None,
        speech_service: DeepSpeechSamplingService | None,
        promotion_service: DeepEvidencePromotionService | None = None,
        decision_service: MediaIdentityDecisionService | None = None,
        completion_service: DeepCompletionService | None = None,
        correlation_service: DeepCorrelationAnalysisService | None = None,
    ) -> None:
        self.database = database
        self.normal_service = normal_service
        self.visual_service = visual_service
        self.speech_service = speech_service
        self.promotion_service = (
            promotion_service
            if promotion_service is not None
            else DeepEvidencePromotionService(database)
        )
        self.decision_service = (
            decision_service
            if decision_service is not None
            else MediaIdentityDecisionService(database)
        )
        self.completion_service = (
            completion_service
            if completion_service is not None
            else DeepCompletionService(database)
        )
        self.correlation_service = (
            correlation_service
            if correlation_service is not None
            else DeepCorrelationAnalysisService(database)
        )

    def _snapshot(self, scan_id: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        with self.database.connect() as conn:
            scan, _, evidence = (
                MediaIdentityDecisionService._scan_snapshot(
                    conn,
                    int(scan_id),
                )
            )
            current, _ = (
                MediaIdentityDecisionService._scan_snapshot_is_current(
                    conn,
                    scan,
                    evidence,
                )
            )
        if scan.get("status") != "complete" or not current:
            raise DeepVerificationError(
                "Deep verification requires one current complete Episode Identity scan."
            )
        return scan, evidence

    @staticmethod
    def _normal_attempted(scan: Mapping[str, Any]) -> bool:
        completed = str(
            scan.get("completed_profile") or ""
        )
        if completed in {
            IdentityProfile.NORMAL.value,
            IdentityProfile.DEEP.value,
        }:
            return True
        if (
            completed != IdentityProfile.FAST.value
            or str(scan.get("requested_profile") or "")
            != IdentityProfile.NORMAL.value
        ):
            return False
        claimed = _json_object(
            scan.get("claimed_identity_json")
        )
        return (
            isinstance(claimed.get("normal_ocr"), Mapping)
            and isinstance(claimed.get("normal_speech"), Mapping)
        )

    @staticmethod
    def _has_staged_deep(scan: Mapping[str, Any]) -> bool:
        claimed = _json_object(
            scan.get("claimed_identity_json")
        )
        return (
            str(scan.get("requested_profile") or "")
            == IdentityProfile.DEEP.value
            and isinstance(
                claimed.get("deep_identity"),
                Mapping,
            )
            and isinstance(
                claimed.get("deep_evidence"),
                Mapping,
            )
        )

    def _ensure_resolved(
        self,
        scan_id: int,
        scan: Mapping[str, Any],
    ) -> tuple[dict[str, Any], IdentityResolution | None]:
        if str(scan.get("result_state") or ""):
            return dict(scan), None
        resolution = self.decision_service.resolve_scan(
            int(scan_id)
        )
        updated, _ = self._snapshot(
            int(scan_id)
        )
        if not str(updated.get("result_state") or ""):
            raise DeepVerificationError(
                "Episode Identity resolver did not publish the staged revision."
            )
        return updated, resolution

    def run(self, scan_id: int) -> DeepVerificationRun:
        scan_id = int(scan_id)
        scan, _ = self._snapshot(scan_id)
        resumed_from_stage = str(
            scan.get("stage") or ""
        )

        normal_result: NormalScanResult | None = None
        visual_result: DeepSamplingRun | None = None
        speech_result: DeepSpeechSamplingRun | None = None
        promotion_result: DeepEvidencePromotionRun | None = None
        resolution: IdentityResolution | None = None
        completion: DeepCompletionRun | None = None

        if str(scan.get("completed_profile") or "") == IdentityProfile.DEEP.value:
            correlation = self.correlation_service.run(
                scan_id
            )
            return DeepVerificationRun(
                scan_id=scan_id,
                resumed_from_stage=resumed_from_stage,
                normal=None,
                visual=None,
                speech=None,
                promotion=None,
                resolution=None,
                completion=None,
                correlation=correlation,
            )

        if not self._normal_attempted(scan):
            if self.normal_service is None:
                raise DeepVerificationError(
                    "Deep verification needs a Normal attempt before Deep "
                    "candidate/fingerprint analysis."
                )
            normal_result = self.normal_service.run_scan(
                scan_id
            )
            scan, normal_resolution = self._ensure_resolved(
                scan_id,
                self._snapshot(scan_id)[0],
            )
            resolution = normal_resolution
            if not self._normal_attempted(scan):
                raise DeepVerificationError(
                    "Normal analysis did not publish the provenance required "
                    "for Deep verification."
                )
        else:
            scan, pending_resolution = self._ensure_resolved(
                scan_id,
                scan,
            )
            if pending_resolution is not None:
                resolution = pending_resolution

        if self._has_staged_deep(scan):
            if not str(scan.get("result_state") or ""):
                scan, staged_resolution = self._ensure_resolved(
                    scan_id,
                    scan,
                )
                if staged_resolution is not None:
                    resolution = staged_resolution
            completion = self.completion_service.finalize(
                scan_id
            )
            correlation = self.correlation_service.run(
                scan_id
            )
            return DeepVerificationRun(
                scan_id=scan_id,
                resumed_from_stage=resumed_from_stage,
                normal=normal_result,
                visual=None,
                speech=None,
                promotion=None,
                resolution=resolution,
                completion=completion,
                correlation=correlation,
            )

        if self.visual_service is not None:
            visual_result = self.visual_service.run(
                scan_id
            )
        if self.speech_service is not None:
            speech_result = self.speech_service.run(
                scan_id
            )

        promotion_result = self.promotion_service.promote(
            scan_id,
            visual=visual_result,
            speech=speech_result,
        )
        scan, deep_resolution = self._ensure_resolved(
            scan_id,
            self._snapshot(scan_id)[0],
        )
        resolution = deep_resolution
        completion = self.completion_service.finalize(
            scan_id
        )
        final_scan, _ = self._snapshot(
            scan_id
        )
        if (
            str(final_scan.get("completed_profile") or "")
            != IdentityProfile.DEEP.value
        ):
            raise DeepVerificationError(
                "Deep profile finalization did not produce a current Deep snapshot."
            )

        correlation = self.correlation_service.run(
            scan_id
        )
        return DeepVerificationRun(
            scan_id=scan_id,
            resumed_from_stage=resumed_from_stage,
            normal=normal_result,
            visual=visual_result,
            speech=speech_result,
            promotion=promotion_result,
            resolution=resolution,
            completion=completion,
            correlation=correlation,
        )

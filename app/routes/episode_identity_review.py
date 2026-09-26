from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request

from ..access import require_librarian
from ..media_identity.external_config import (
    ExternalSourceConfigService,
    build_configured_source_registry,
)
from ..media_identity.deep_completion_service import DeepCompletionError
from ..media_identity.deep_correlation_service import (
    DeepCorrelationAnalysisError,
    DeepCorrelationAnalysisService,
)
from ..media_identity.deep_evidence_service import DeepEvidencePromotionError
from ..media_identity.deep_sampling_service import (
    DeepSamplingScanError,
    DeepSamplingService,
)
from ..media_identity.deep_speech_service import (
    DeepSpeechSamplingError,
    DeepSpeechSamplingService,
)
from ..media_identity.deep_verification_service import (
    DeepVerificationError,
    DeepVerificationService,
)
from ..media_identity.fast import FastIdentityScanError, FastIdentityService
from ..media_identity.local_frames import LOCAL_FRAME_SOURCE_KEY
from ..media_identity.models import IdentityProfile
from ..media_identity.normal_service import (
    NormalIdentityScanError,
    NormalIdentityService,
)
from ..media_identity.ocr import RapidOcrCpuEngine
from ..whisper_cpp_speech import WhisperCppSpeechEngine
from ..provider_secrets import ProviderSecretError
from ..media_identity.service import (
    MediaIdentityDecisionError,
    MediaIdentityDecisionService,
)
from .context import RouteContext


def build_router(ctx: RouteContext):
    router = APIRouter()
    db = ctx.live("db")
    templates = ctx.live("templates")
    HTMLResponse = ctx.get("HTMLResponse")
    HTTPException = ctx.get("HTTPException")
    redirect = ctx.live("redirect")
    record_event = ctx.live("record_event")
    analyze_library_health_with_activity = ctx.live(
        "analyze_library_health_with_activity"
    )
    provider_secrets = ctx.live("provider_secrets")
    speech_runtime_component = ctx.live("speech_runtime_component")
    speech_model_components = ctx.live("speech_model_components")

    fast = FastIdentityService(db)

    def _decision_external_registry():
        try:
            secrets = provider_secrets.load()
        except ProviderSecretError:
            secrets = {}
        return build_configured_source_registry(
            ExternalSourceConfigService(db),
            secrets,
        )

    decisions = MediaIdentityDecisionService(
        db,
        external_registry_factory=_decision_external_registry,
    )

    def _configured_analysis_services():
        credential_warning = ""
        try:
            secrets = provider_secrets.load()
        except ProviderSecretError as exc:
            secrets = {}
            credential_warning = str(exc)
        registry = build_configured_source_registry(
            ExternalSourceConfigService(db),
            secrets,
        )
        speech_model_component = speech_model_components["base-q5_1"]
        speech_engine = WhisperCppSpeechEngine(
            speech_runtime_component,
            speech_model_component,
        )
        ocr_engine = RapidOcrCpuEngine()
        normal = NormalIdentityService(
            db,
            registry,
            ocr_engine,
            speech_engine=speech_engine,
            speech_model=speech_model_component.identity,
        )
        deep = DeepVerificationService(
            db,
            normal_service=normal,
            visual_service=DeepSamplingService(
                db,
                ocr_engine,
            ),
            speech_service=DeepSpeechSamplingService(
                db,
                speech_engine,
                speech_model_component.identity,
            ),
            decision_service=decisions,
            correlation_service=DeepCorrelationAnalysisService(db),
        )
        return normal, deep, credential_warning

    def _reviewed_intent(values) -> tuple[int, str, str]:
        try:
            revision = int(str(values.get("result_revision") or "0"))
        except (TypeError, ValueError) as exc:
            raise MediaIdentityDecisionError(
                "The reviewed Episode Identity revision is missing or invalid."
            ) from exc
        digest = str(
            values.get("decision_snapshot_sha256") or ""
        ).strip().casefold()
        candidate_key = str(values.get("candidate_key") or "").strip()
        if (
            revision <= 0
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not candidate_key
        ):
            raise MediaIdentityDecisionError(
                "The reviewed Episode Identity decision token is incomplete. "
                "Refresh the scan before continuing."
            )
        return revision, digest, candidate_key

    def librarian_get(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.get(path, dependencies=dependencies, **kwargs)

    def librarian_post(path: str, **kwargs):
        dependencies = list(kwargs.pop("dependencies", ()))
        dependencies.append(Depends(require_librarian))
        return router.post(path, dependencies=dependencies, **kwargs)

    def _refresh_findings(user_id: int | None = None) -> bool:
        try:
            analyze_library_health_with_activity()
        except sqlite3.Error as exc:
            record_event(
                "mie",
                "Library Health could not refresh after an Episode Identity decision.",
                level="warning",
                detail=str(exc),
                context={"operation": "episode-identity-refresh"},
                user_id=user_id,
            )
            return False
        return True

    @librarian_post("/files/{file_id}/episode-identity/fast")
    def verify_episode_identity(request: Request, file_id: int):
        with db.connect() as conn:
            row = conn.execute(
                "SELECT title_id FROM files WHERE id=?", (int(file_id),)
            ).fetchone()
        if not row:
            raise HTTPException(404, "Media file not found.")
        title_id = int(row["title_id"])
        try:
            scan = fast.scan_file(
                int(file_id), requested_by=request.state.user.id
            )
            resolution = decisions.resolve_scan(scan.scan_id)
            findings_refreshed = _refresh_findings(request.state.user.id)
        except (FastIdentityScanError, MediaIdentityDecisionError) as exc:
            record_event(
                "mie",
                f"Episode Identity verification could not complete for file {file_id}.",
                level="warning",
                detail=str(exc),
                context={"file_id": int(file_id), "title_id": title_id},
                user_id=request.state.user.id,
            )
            return redirect(
                f"/titles/{title_id}",
                f"Episode Identity could not complete: {exc}",
            )
        record_event(
            "mie",
            f"Episode Identity Fast verification completed for file {file_id}.",
            context={
                "file_id": int(file_id),
                "title_id": title_id,
                "scan_id": scan.scan_id,
                "result_state": resolution.state.value,
            },
            user_id=request.state.user.id,
        )
        message = (
            "Episode Identity verification completed. Review the evidence before making any correction."
        )
        if not scan.provider_cache_used:
            message += (
                " TVDB episode identity metadata was unavailable, so this scan used "
                "reduced evidence. Refresh TVDB metadata and verify again to include "
                "provider synopsis and episode-order evidence."
            )
        if not findings_refreshed:
            message += " Library Health will catch up on the next successful analysis."
        return redirect(
            f"/episode-identity/scans/{scan.scan_id}",
            message,
        )

    @librarian_post("/episode-identity/scans/{scan_id}/normal")
    def run_normal_episode_identity(request: Request, scan_id: int):
        try:
            detail = decisions.scan_detail(int(scan_id))
        except MediaIdentityDecisionError as exc:
            raise HTTPException(404, str(exc)) from exc
        file_row = detail.get("file") or {}
        title_id = int(file_row.get("title_id") or 0)
        credential_warning = ""
        try:
            normal, _deep, credential_warning = (
                _configured_analysis_services()
            )
            if credential_warning:
                record_event(
                    "mie",
                    "Episode Identity Normal could not read external integration credentials; local fallback remains available.",
                    level="warning",
                    detail=credential_warning,
                    context={
                        "scan_id": int(scan_id),
                        "title_id": title_id or None,
                        "profile": "normal",
                    },
                    user_id=request.state.user.id,
                )
            result = normal.run_scan(int(scan_id))
            speech_escalated = bool(
                getattr(result, "speech_escalated", False)
            )
            speech_planned_window_count = int(
                getattr(result, "speech_planned_window_count", 0) or 0
            )
            speech_transcript_count = int(
                getattr(result, "speech_transcript_count", 0) or 0
            )
            speech_reused_artifact_count = int(
                getattr(result, "speech_reused_artifact_count", 0) or 0
            )
            speech_failures = tuple(
                getattr(result, "speech_failures", ()) or ()
            )
            speech_budget_exhausted = bool(
                getattr(result, "speech_budget_exhausted", False)
            )
            resolution = decisions.resolve_scan(int(scan_id))
            findings_refreshed = _refresh_findings(request.state.user.id)
        except (NormalIdentityScanError, MediaIdentityDecisionError) as exc:
            record_event(
                "mie",
                f"Episode Identity Normal verification could not complete for scan {scan_id}.",
                level="warning",
                detail=str(exc),
                context={
                    "scan_id": int(scan_id),
                    "title_id": title_id or None,
                    "profile": "normal",
                },
                user_id=request.state.user.id,
            )
            return redirect(
                f"/episode-identity/scans/{scan_id}",
                f"Normal verification could not complete: {exc}",
            )

        record_event(
            "mie",
            f"Episode Identity Normal verification completed for scan {scan_id}.",
            context={
                "scan_id": int(scan_id),
                "title_id": title_id or None,
                "profile": "normal",
                "completed_profile": result.completed_profile.value,
                "source_key": result.source_key,
                "observation_count": result.observation_count,
                "reused_artifact_count": result.reused_artifact_count,
                "sampling_stage": (
                    result.highest_observed_stage.name.casefold()
                    if result.highest_observed_stage is not None
                    else ""
                ),
                "speech_escalated": speech_escalated,
                "speech_planned_window_count": speech_planned_window_count,
                "speech_transcript_count": speech_transcript_count,
                "speech_reused_artifact_count": speech_reused_artifact_count,
                "speech_failures": list(speech_failures),
                "result_state": resolution.state.value,
            },
            user_id=request.state.user.id,
        )

        if result.completed_profile == IdentityProfile.NORMAL:
            if result.observation_count:
                source_label = (
                    "generated local FFmpeg frames"
                    if result.source_key == LOCAL_FRAME_SOURCE_KEY
                    else f"{result.source_key.title()} preview frames"
                    if result.source_key
                    else "preview frames"
                )
                stage_label = (
                    result.highest_observed_stage.name.title()
                    if result.highest_observed_stage is not None
                    else "Unknown"
                )
                message = (
                    f"Normal verification completed using {source_label}. "
                    f"{result.observation_count} preview frame(s) were analyzed "
                    f"through the {stage_label} sampling stage"
                )
                if result.reused_artifact_count:
                    message += (
                        f", including {result.reused_artifact_count} cached OCR artifact(s)"
                    )
                message += "."
            else:
                message = "Normal verification completed without usable visual OCR."

            if speech_escalated:
                if speech_transcript_count:
                    message += (
                        f" Local speech analysis transcribed "
                        f"{speech_transcript_count} targeted window(s)"
                    )
                    if speech_reused_artifact_count:
                        message += (
                            f", including {speech_reused_artifact_count} "
                            "cached transcript(s)"
                        )
                    message += "."
                elif "speech-engine-unavailable" in set(speech_failures):
                    message += (
                        " Local speech escalation was needed, but the optional "
                        "whisper.cpp runtime/model is not currently available."
                    )
                elif speech_failures:
                    message += (
                        " Local speech escalation was attempted but produced no "
                        "reusable transcript."
                    )
            message += " Review the updated evidence before making any correction."
        elif "ocr-engine-unavailable" in set(result.failures):
            message = (
                "Normal OCR is not installed or available on this server, and local "
                "speech did not produce a transcript, so the scan remains at Fast evidence."
            )
        else:
            local_failure = next(
                (
                    item.split(":unavailable:", 1)[1]
                    for item in result.failures
                    if item.startswith(
                        f"{LOCAL_FRAME_SOURCE_KEY}:unavailable:"
                    )
                ),
                "",
            )
            message = (
                "Normal could not obtain usable Plex/Jellyfin previews, generated "
                "local FFmpeg frames, or a reusable targeted speech transcript, so "
                "the scan remains at Fast evidence."
            )
            if local_failure:
                message += f" Local visual fallback: {local_failure}"
            if speech_escalated and speech_failures:
                message += " Local speech escalation was unavailable or unsuccessful."
        if credential_warning:
            message += (
                " Plex/Jellyfin credentials could not be read, so external previews "
                "were skipped and only local fallback was available."
            )
        if result.budget_exhausted:
            message += " Normal visual analysis stopped at its configured resource limit."
        if speech_budget_exhausted:
            message += " Normal speech analysis stopped at its configured resource limit."
        if not findings_refreshed:
            message += " Library Health will catch up on the next successful analysis."
        return redirect(
            f"/episode-identity/scans/{scan_id}",
            message,
        )

    @librarian_post("/episode-identity/scans/{scan_id}/deep")
    def run_deep_episode_identity(request: Request, scan_id: int):
        try:
            detail = decisions.scan_detail(int(scan_id))
        except MediaIdentityDecisionError as exc:
            raise HTTPException(404, str(exc)) from exc
        file_row = detail.get("file") or {}
        title_id = int(file_row.get("title_id") or 0)
        credential_warning = ""
        try:
            _normal, deep, credential_warning = (
                _configured_analysis_services()
            )
            result = deep.run(int(scan_id))
            findings_refreshed = _refresh_findings(
                request.state.user.id
            )
        except (
            DeepVerificationError,
            DeepSamplingScanError,
            DeepSpeechSamplingError,
            DeepEvidencePromotionError,
            DeepCompletionError,
            DeepCorrelationAnalysisError,
            NormalIdentityScanError,
            MediaIdentityDecisionError,
        ) as exc:
            _refresh_findings(request.state.user.id)
            record_event(
                "mie",
                f"Episode Identity Deep verification could not complete for scan {scan_id}.",
                level="warning",
                detail=str(exc),
                context={
                    "scan_id": int(scan_id),
                    "title_id": title_id or None,
                    "profile": "deep",
                },
                user_id=request.state.user.id,
            )
            return redirect(
                f"/episode-identity/scans/{scan_id}",
                f"Deep verification could not complete: {exc}",
            )

        visual_complete = bool(
            result.visual is not None
            and result.visual.coverage_complete
        )
        speech_complete = bool(
            result.speech is not None
            and result.speech.coverage_complete
        )
        record_event(
            "mie",
            f"Episode Identity Deep verification completed for scan {scan_id}.",
            context={
                "scan_id": int(scan_id),
                "title_id": title_id or None,
                "profile": "deep",
                "completed_revision": result.completed_revision,
                "resumed_from_stage": result.resumed_from_stage,
                "visual_complete": visual_complete,
                "speech_complete": speech_complete,
                "visual_failures": (
                    list(result.visual.failures)
                    if result.visual is not None
                    else []
                ),
                "speech_failures": (
                    list(result.speech.failures)
                    if result.speech is not None
                    else []
                ),
                "correlation_artifact_id": (
                    result.correlation.artifact_id
                ),
                "correlation_reused": bool(
                    result.correlation.reused
                ),
                "complete_modalities": list(
                    result.correlation.interpretation.complete_modalities
                ),
                "sequence_peer_coverage_complete": (
                    result.correlation.sequence.analysis.usable_count
                    == result.correlation.sequence.planned_file_count
                    and not result.correlation.sequence.missing_scan_file_ids
                    and not result.correlation.sequence.invalid_scan_file_ids
                ),
            },
            user_id=request.state.user.id,
        )

        message = (
            "Deep verification completed and the resolver was republished "
            "against the widened candidate set."
        )
        if visual_complete:
            message += " Deep OCR coverage completed."
        elif result.visual is not None and result.visual.failures:
            message += (
                " Deep OCR was unavailable or incomplete, so it did not "
                "contribute resolver evidence."
            )
        if speech_complete:
            message += " Deep speech coverage completed."
        elif result.speech is not None and result.speech.failures:
            message += (
                " Deep speech was unavailable or incomplete, so it did not "
                "contribute resolver evidence."
            )
        if result.correlation.interpretation.fully_multimodal:
            message += (
                " Cross-file fingerprint correlation completed with both "
                "video and audio modalities."
            )
        else:
            message += (
                " Cross-file correlation completed with the evidence "
                "modalities that were available."
            )
        message += " Review the updated Deep diagnostics before any correction."
        if credential_warning:
            message += (
                " Plex/Jellyfin credentials could not be read, so external "
                "Normal previews were skipped and local fallback was used."
            )
        if not findings_refreshed:
            message += (
                " Library Health will catch up on the next successful analysis."
            )
        return redirect(
            f"/episode-identity/scans/{scan_id}",
            message,
        )

    @librarian_get(
        "/episode-identity/scans/{scan_id}",
        response_class=HTMLResponse,
    )
    def episode_identity_detail(request: Request, scan_id: int):
        try:
            detail = decisions.scan_detail(int(scan_id))
        except MediaIdentityDecisionError as exc:
            raise HTTPException(404, str(exc)) from exc
        response = templates.TemplateResponse(
            request,
            "episode_identity.html",
            {
                "identity": detail,
                "message": request.query_params.get("message", ""),
            },
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @librarian_get(
        "/episode-identity/scans/{scan_id}/rename-preview",
        response_class=HTMLResponse,
    )
    def episode_identity_rename_preview(request: Request, scan_id: int):
        try:
            revision, digest, candidate_key = _reviewed_intent(
                request.query_params
            )
            preview = decisions.rename_preview(
                int(scan_id),
                expected_result_revision=revision,
                expected_decision_snapshot_sha256=digest,
                expected_candidate_key=candidate_key,
            )
        except MediaIdentityDecisionError as exc:
            raise HTTPException(409, str(exc)) from exc
        response = templates.TemplateResponse(
            request,
            "episode_identity_rename_preview.html",
            {"preview": preview, "identity": preview["scan"]},
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @librarian_post(
        "/episode-identity/scans/{scan_id}/confirm-current"
    )
    async def confirm_current_identity(request: Request, scan_id: int):
        try:
            form = await request.form()
            revision, digest, candidate_key = _reviewed_intent(form)
            decisions.confirm_current(
                int(scan_id),
                request.state.user.id,
                expected_result_revision=revision,
                expected_decision_snapshot_sha256=digest,
                expected_candidate_key=candidate_key,
            )
            findings_refreshed = _refresh_findings(request.state.user.id)
        except MediaIdentityDecisionError as exc:
            return redirect(
                f"/episode-identity/scans/{scan_id}",
                f"Identity confirmation was not saved: {exc}",
            )
        record_event(
            "mie",
            f"Episode Identity scan {scan_id}: current filename marked correct.",
            context={"scan_id": int(scan_id), "decision": "confirm-current"},
            user_id=request.state.user.id,
        )
        message = (
            "Marked correct for this exact media snapshot. A changed file will require confirmation again."
        )
        if not findings_refreshed:
            message += " Library Health will catch up on the next successful analysis."
        return redirect(
            f"/episode-identity/scans/{scan_id}",
            message,
        )

    @librarian_post(
        "/episode-identity/scans/{scan_id}/confirm-best"
    )
    async def confirm_best_identity(request: Request, scan_id: int):
        try:
            form = await request.form()
            revision, digest, candidate_key = _reviewed_intent(form)
            decisions.confirm_best(
                int(scan_id),
                request.state.user.id,
                expected_result_revision=revision,
                expected_decision_snapshot_sha256=digest,
                expected_candidate_key=candidate_key,
            )
            findings_refreshed = _refresh_findings(request.state.user.id)
        except MediaIdentityDecisionError as exc:
            return redirect(
                f"/episode-identity/scans/{scan_id}",
                f"Suggested identity was not confirmed: {exc}",
            )
        record_event(
            "mie",
            f"Episode Identity scan {scan_id}: suggested content identity confirmed.",
            context={"scan_id": int(scan_id), "decision": "confirm-best"},
            user_id=request.state.user.id,
        )
        message = (
            "Suggested content identity confirmed. The catalog claim and filename were not changed."
        )
        if not findings_refreshed:
            message += " Library Health will catch up on the next successful analysis."
        return redirect(
            f"/episode-identity/scans/{scan_id}",
            message,
        )

    return router, {
        "episode_identity_fast": fast,
        "episode_identity_decisions": decisions,
        "verify_episode_identity": verify_episode_identity,
        "run_normal_episode_identity": run_normal_episode_identity,
        "run_deep_episode_identity": run_deep_episode_identity,
        "episode_identity_detail": episode_identity_detail,
        "episode_identity_rename_preview": episode_identity_rename_preview,
        "confirm_current_identity": confirm_current_identity,
        "confirm_best_identity": confirm_best_identity,
    }

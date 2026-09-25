from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.db import Database
from app.media_identity.deep_verification_service import (
    DeepVerificationError,
    DeepVerificationService,
)


class _State:
    def __init__(self, *, raw_fast: bool = True) -> None:
        self.scan = {
            "id": 1,
            "status": "complete",
            "requested_profile": "fast" if raw_fast else "normal",
            "completed_profile": "fast" if raw_fast else "normal",
            "stage": "resolved",
            "result_state": "verified",
            "best_candidate_key": "candidate:base",
            "claimed_identity_json": "{}",
        }
        if not raw_fast:
            self.scan["claimed_identity_json"] = (
                '{"normal_ocr":{},"normal_speech":{}}'
            )
        self.calls: list[str] = []

    def snapshot(self, _scan_id: int):
        return dict(self.scan), []


class _Normal:
    def __init__(self, state: _State) -> None:
        self.state = state

    def run_scan(self, _scan_id: int):
        self.state.calls.append("normal")
        self.state.scan.update({
            "requested_profile": "normal",
            "completed_profile": "normal",
            "stage": "normal_ocr_complete",
            "result_state": None,
            "best_candidate_key": None,
            "claimed_identity_json": (
                '{"normal_ocr":{},"normal_speech":{}}'
            ),
        })
        return SimpleNamespace(scan_id=1)


class _Decision:
    def __init__(self, state: _State) -> None:
        self.state = state

    def resolve_scan(self, _scan_id: int):
        if self.state.scan["requested_profile"] == "deep":
            self.state.calls.append("resolve-deep")
        else:
            self.state.calls.append("resolve-normal")
        self.state.scan.update({
            "stage": "resolved",
            "result_state": "verified",
            "best_candidate_key": "candidate:base",
        })
        return SimpleNamespace(
            best_candidate_key="candidate:base"
        )


class _Visual:
    def __init__(self, state: _State) -> None:
        self.state = state

    def run(self, _scan_id: int):
        self.state.calls.append("visual")
        return SimpleNamespace(
            coverage_complete=False,
            failures=("ocr-engine-unavailable",),
        )


class _Speech:
    def __init__(self, state: _State) -> None:
        self.state = state

    def run(self, _scan_id: int):
        self.state.calls.append("speech")
        return SimpleNamespace(
            coverage_complete=False,
            failures=("speech-engine-unavailable",),
        )


class _Promotion:
    def __init__(self, state: _State) -> None:
        self.state = state

    def promote(self, _scan_id: int, *, visual=None, speech=None):
        self.state.calls.append("promote")
        self.state.scan.update({
            "requested_profile": "deep",
            "stage": "deep_evidence_staged",
            "result_state": None,
            "best_candidate_key": None,
            "claimed_identity_json": (
                '{"normal_ocr":{},"normal_speech":{},'
                '"deep_identity":{},"deep_evidence":{}}'
            ),
        })
        return SimpleNamespace(
            scan_id=1,
            staged_revision=3,
            visual=visual,
            speech=speech,
        )


class _Completion:
    def __init__(self, state: _State) -> None:
        self.state = state

    def finalize(self, _scan_id: int):
        self.state.calls.append("finalize")
        self.state.scan.update({
            "completed_profile": "deep",
            "stage": "deep_resolved",
        })
        return SimpleNamespace(
            scan_id=1,
            resolved_revision=4,
            completed_revision=5,
        )


class _Correlation:
    def __init__(self, state: _State) -> None:
        self.state = state

    def run(self, _scan_id: int):
        self.state.calls.append("correlate")
        return SimpleNamespace(
            scan_id=1,
            artifact_id=900,
            interpretation=SimpleNamespace(
                scan_id=1,
                result_revision=5,
            ),
        )


class DeepVerificationCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(
            Path(self.temporary.name) / "coordinator.db"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _service(
        self,
        state: _State,
        *,
        normal=True,
        visual=True,
        speech=True,
    ) -> DeepVerificationService:
        service = DeepVerificationService(
            self.database,
            normal_service=(
                _Normal(state) if normal else None
            ),
            visual_service=(
                _Visual(state) if visual else None
            ),
            speech_service=(
                _Speech(state) if speech else None
            ),
            promotion_service=_Promotion(state),
            decision_service=_Decision(state),
            completion_service=_Completion(state),
            correlation_service=_Correlation(state),
        )
        service._snapshot = state.snapshot
        return service

    def test_full_workflow_runs_each_stage_once_in_order(self) -> None:
        state = _State(raw_fast=True)
        service = self._service(state)

        result = service.run(1)

        self.assertEqual(
            state.calls,
            [
                "normal",
                "resolve-normal",
                "visual",
                "speech",
                "promote",
                "resolve-deep",
                "finalize",
                "correlate",
            ],
        )
        self.assertEqual(
            state.scan["completed_profile"],
            "deep",
        )
        self.assertEqual(result.completed_revision, 5)
        self.assertIsNotNone(result.normal)
        self.assertIsNotNone(result.visual)
        self.assertIsNotNone(result.speech)
        self.assertIsNotNone(result.promotion)

    def test_candidate_only_deep_does_not_require_optional_j2_services(self) -> None:
        state = _State(raw_fast=False)
        service = self._service(
            state,
            normal=False,
            visual=False,
            speech=False,
        )

        result = service.run(1)

        self.assertEqual(
            state.calls,
            [
                "promote",
                "resolve-deep",
                "finalize",
                "correlate",
            ],
        )
        self.assertIsNone(result.visual)
        self.assertIsNone(result.speech)
        self.assertEqual(
            state.scan["completed_profile"],
            "deep",
        )

    def test_resume_from_unresolved_staged_deep_skips_j2_and_promotion(self) -> None:
        state = _State(raw_fast=False)
        state.scan.update({
            "requested_profile": "deep",
            "stage": "deep_evidence_staged",
            "result_state": None,
            "best_candidate_key": None,
            "claimed_identity_json": (
                '{"normal_ocr":{},"normal_speech":{},'
                '"deep_identity":{},"deep_evidence":{}}'
            ),
        })
        service = self._service(state)

        service.run(1)

        self.assertEqual(
            state.calls,
            [
                "resolve-deep",
                "finalize",
                "correlate",
            ],
        )

    def test_resume_from_resolved_staged_deep_skips_j2_and_promotion(self) -> None:
        state = _State(raw_fast=False)
        state.scan.update({
            "requested_profile": "deep",
            "stage": "resolved",
            "result_state": "verified",
            "best_candidate_key": "candidate:base",
            "claimed_identity_json": (
                '{"normal_ocr":{},"normal_speech":{},'
                '"deep_identity":{},"deep_evidence":{}}'
            ),
        })
        service = self._service(state)

        service.run(1)

        self.assertEqual(
            state.calls,
            ["finalize", "correlate"],
        )

    def test_already_deep_only_runs_current_correlation(self) -> None:
        state = _State(raw_fast=False)
        state.scan.update({
            "requested_profile": "deep",
            "completed_profile": "deep",
            "stage": "deep_resolved",
            "claimed_identity_json": (
                '{"normal_ocr":{},"normal_speech":{},'
                '"deep_identity":{},"deep_evidence":{}}'
            ),
        })
        service = self._service(state)

        result = service.run(1)

        self.assertEqual(
            state.calls,
            ["correlate"],
        )
        self.assertIsNone(result.promotion)
        self.assertIsNone(result.completion)
        self.assertEqual(result.completed_revision, 5)

    def test_raw_fast_scan_requires_normal_service(self) -> None:
        state = _State(raw_fast=True)
        service = self._service(
            state,
            normal=False,
        )

        with self.assertRaisesRegex(
            DeepVerificationError,
            "needs a Normal attempt",
        ):
            service.run(1)

        self.assertEqual(state.calls, [])


if __name__ == "__main__":
    unittest.main()

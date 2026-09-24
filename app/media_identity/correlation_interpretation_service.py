from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Any, Mapping

from .correlation_interpretation import (
    CorrelationInterpretationError,
    CorrelationInterpretationPolicy,
    MultimodalAgreement,
    PairInterpretation,
    interpret_matrix,
)
from .fingerprint import (
    AUDIO_ENVELOPE_DHASH64_V1,
    VIDEO_DHASH64_V1,
)
from .fingerprint_bundle import DeepFingerprintBundleRun
from .versions import DEEP_CORRELATION_INTERPRETATION_VERSION


@dataclass(frozen=True)
class DeepCorrelationInterpretationRun:
    scan_id: int
    result_revision: int
    correlation_plan_signature: str
    interpretation_version: int
    policy_signature: str
    policy_identity: Mapping[str, Any]
    complete_modalities: tuple[str, ...]
    planned_pair_count: int
    pairs: tuple[PairInterpretation, ...]

    def __post_init__(self) -> None:
        for label, value in (
            ("scan ID", self.scan_id),
            ("result revision", self.result_revision),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
            ):
                raise CorrelationInterpretationError(
                    f"J4 interpretation {label} must be a positive integer."
                )
        if (
            self.interpretation_version
            != DEEP_CORRELATION_INTERPRETATION_VERSION
        ):
            raise CorrelationInterpretationError(
                "J4 interpretation version is stale."
            )
        for label, digest in (
            ("correlation-plan signature", self.correlation_plan_signature),
            ("policy signature", self.policy_signature),
        ):
            normalized = str(digest or "").strip().casefold()
            if (
                len(normalized) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in normalized
                )
            ):
                raise CorrelationInterpretationError(
                    f"J4 interpretation {label} is invalid."
                )
        if not isinstance(self.policy_identity, Mapping):
            raise CorrelationInterpretationError(
                "J4 interpretation policy identity is malformed."
            )
        expected_policy_signature = hashlib.sha256(
            _canonical_json_bytes(self.policy_identity)
        ).hexdigest()
        if expected_policy_signature != self.policy_signature:
            raise CorrelationInterpretationError(
                "J4 interpretation policy signature does not match its identity."
            )
        if (
            len(set(self.complete_modalities))
            != len(self.complete_modalities)
            or any(
                item not in {"video", "audio"}
                for item in self.complete_modalities
            )
        ):
            raise CorrelationInterpretationError(
                "J4 interpretation complete modalities are invalid."
            )
        if (
            isinstance(self.planned_pair_count, bool)
            or not isinstance(self.planned_pair_count, int)
            or self.planned_pair_count < 0
        ):
            raise CorrelationInterpretationError(
                "J4 interpretation planned pair count is invalid."
            )
        if any(
            not isinstance(item, PairInterpretation)
            for item in self.pairs
        ):
            raise CorrelationInterpretationError(
                "J4 interpretation pairs are malformed."
            )
        pair_keys = tuple(
            (item.left_file_id, item.right_file_id)
            for item in self.pairs
        )
        if len(set(pair_keys)) != len(pair_keys):
            raise CorrelationInterpretationError(
                "J4 interpretation contains duplicate pairs."
            )
        if len(self.pairs) > self.planned_pair_count:
            raise CorrelationInterpretationError(
                "J4 interpretation exceeds its planned pair count."
            )
        if (
            self.complete_modalities
            and len(self.pairs) != self.planned_pair_count
        ):
            raise CorrelationInterpretationError(
                "J4 complete modality coverage is missing planned pairs."
            )

    @property
    def pair_count(self) -> int:
        return len(self.pairs)

    @property
    def fully_multimodal(self) -> bool:
        return set(self.complete_modalities) == {"video", "audio"}

    @property
    def contradiction_count(self) -> int:
        return sum(1 for item in self.pairs if item.contradictory)

    @property
    def both_high_count(self) -> int:
        return sum(
            1
            for item in self.pairs
            if item.agreement is MultimodalAgreement.BOTH_HIGH
        )


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise CorrelationInterpretationError(
            "Correlation interpretation policy cannot be serialized safely."
        ) from exc


def _freeze_policy_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze_policy_value(item)
            for key, item in value.items()
        })
    if isinstance(value, list):
        return tuple(_freeze_policy_value(item) for item in value)
    return value


def _policy_identity(
    policy: CorrelationInterpretationPolicy,
) -> tuple[Mapping[str, Any], str]:
    payload = policy.identity_payload()
    signature = hashlib.sha256(
        _canonical_json_bytes(payload)
    ).hexdigest()
    frozen = _freeze_policy_value(payload)
    if not isinstance(frozen, Mapping):
        raise CorrelationInterpretationError(
            "Correlation interpretation policy identity is malformed."
        )
    return frozen, signature


def interpret_fingerprint_bundle(
    bundle: DeepFingerprintBundleRun,
    *,
    policy: CorrelationInterpretationPolicy | None = None,
) -> DeepCorrelationInterpretationRun:
    if not isinstance(bundle, DeepFingerprintBundleRun):
        raise CorrelationInterpretationError(
            "J4 interpretation requires a DeepFingerprintBundleRun."
        )
    if (
        isinstance(bundle.scan_id, bool)
        or not isinstance(bundle.scan_id, int)
        or bundle.scan_id < 1
        or isinstance(bundle.result_revision, bool)
        or not isinstance(bundle.result_revision, int)
        or bundle.result_revision < 1
    ):
        raise CorrelationInterpretationError(
            "J4 interpretation requires a sealed positive scan revision."
        )
    plan_signature = str(
        bundle.correlation_plan_signature or ""
    ).strip().casefold()
    if (
        len(plan_signature) != 64
        or any(
            character not in "0123456789abcdef"
            for character in plan_signature
        )
    ):
        raise CorrelationInterpretationError(
            "J4 interpretation requires a valid correlation-plan signature."
        )

    video = bundle.video
    audio = bundle.audio
    if (
        video.scan_id != bundle.scan_id
        or audio.scan_id != bundle.scan_id
        or video.correlation_plan_signature != plan_signature
        or audio.correlation_plan_signature != plan_signature
    ):
        raise CorrelationInterpretationError(
            "J4 modalities are not bound to the same scan and correlation plan."
        )
    if video.algorithm_key != VIDEO_DHASH64_V1.key:
        raise CorrelationInterpretationError(
            "J4 video interpretation received the wrong fingerprint algorithm."
        )
    if audio.algorithm_key != AUDIO_ENVELOPE_DHASH64_V1.key:
        raise CorrelationInterpretationError(
            "J4 audio interpretation received the wrong fingerprint algorithm."
        )
    if (
        video.planned_file_count != audio.planned_file_count
        or video.planned_pair_count != audio.planned_pair_count
    ):
        raise CorrelationInterpretationError(
            "J4 modalities disagree on the planned correlation cohort."
        )

    policy = policy or CorrelationInterpretationPolicy()
    policy_identity, policy_signature = _policy_identity(policy)
    pairs = interpret_matrix(
        video=video.comparisons,
        audio=audio.comparisons,
        policy=policy,
    )

    return DeepCorrelationInterpretationRun(
        scan_id=bundle.scan_id,
        result_revision=bundle.result_revision,
        correlation_plan_signature=plan_signature,
        interpretation_version=DEEP_CORRELATION_INTERPRETATION_VERSION,
        policy_signature=policy_signature,
        policy_identity=policy_identity,
        complete_modalities=bundle.complete_modalities,
        planned_pair_count=video.planned_pair_count,
        pairs=pairs,
    )

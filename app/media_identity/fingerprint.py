from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import math
import statistics
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from .versions import (
    DEEP_FINGERPRINT_CONTRACT_VERSION,
    DEEP_FINGERPRINT_MATCH_VERSION,
)


MAX_FINGERPRINT_SAMPLES = 32
MAX_FINGERPRINT_CANDIDATES = 64
MAX_FINGERPRINT_ALIGNMENT_SHIFT = 2
FINGERPRINT_OUTPUT_SEAL_FIELD = "fingerprint_output_sha256"


class FingerprintError(ValueError):
    """A content fingerprint or comparison request is outside its safe contract."""


class FingerprintFamily(str, Enum):
    PERCEPTUAL_VIDEO = "perceptual_video"
    PERCEPTUAL_AUDIO = "perceptual_audio"


@dataclass(frozen=True)
class FingerprintAlgorithm:
    key: str
    version: str
    family: FingerprintFamily
    bits_per_sample: int
    max_samples: int = MAX_FINGERPRINT_SAMPLES

    def __post_init__(self) -> None:
        if (
            not isinstance(self.key, str)
            or not isinstance(self.version, str)
            or not isinstance(self.family, FingerprintFamily)
        ):
            raise FingerprintError(
                "Fingerprint algorithms require text key/version and a known family."
            )
        key = self.key.strip().casefold()
        version = self.version.strip()
        if not key or not version:
            raise FingerprintError(
                "Fingerprint algorithms require stable key and version values."
            )
        if (
            isinstance(self.bits_per_sample, bool)
            or not isinstance(self.bits_per_sample, int)
            or self.bits_per_sample < 8
            or self.bits_per_sample > 256
            or self.bits_per_sample % 8
        ):
            raise FingerprintError(
                "Fingerprint sample width must be a byte-aligned 8-256 bits."
            )
        if (
            isinstance(self.max_samples, bool)
            or not isinstance(self.max_samples, int)
            or self.max_samples < 1
            or self.max_samples > MAX_FINGERPRINT_SAMPLES
        ):
            raise FingerprintError(
                "Fingerprint algorithms exceed the supported sample bound."
            )
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "version", version)

    def identity_payload(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "version": self.version,
            "family": self.family.value,
            "bits_per_sample": self.bits_per_sample,
            "max_samples": self.max_samples,
        }


VIDEO_DHASH64_V1 = FingerprintAlgorithm(
    key="video-dhash64-sequence",
    version="1",
    family=FingerprintFamily.PERCEPTUAL_VIDEO,
    bits_per_sample=64,
    max_samples=24,
)

AUDIO_ENVELOPE_DHASH64_V1 = FingerprintAlgorithm(
    key="audio-envelope-dhash64-sequence",
    version="1",
    family=FingerprintFamily.PERCEPTUAL_AUDIO,
    bits_per_sample=64,
    max_samples=16,
)


def _valid_sha256(value: object) -> str:
    digest = str(value or "").strip().casefold()
    if (
        len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise FingerprintError(
            "Content fingerprints require an exact media SHA-256 binding."
        )
    return digest


def _freeze_json(value: Any, *, depth: int = 0) -> Any:
    if depth > 16:
        raise FingerprintError("Fingerprint parameters are nested too deeply.")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FingerprintError(
                "Fingerprint parameters cannot contain non-finite numbers."
            )
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise FingerprintError(
                    "Fingerprint parameter keys must be text."
                )
            result[key] = _freeze_json(item, depth=depth + 1)
        return MappingProxyType(result)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, depth=depth + 1) for item in value)
    raise FingerprintError(
        f"Unsupported fingerprint parameter type {type(value).__name__}."
    )


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            _json_ready(_freeze_json(value)),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise FingerprintError(
            "Fingerprint identity could not be serialized deterministically."
        ) from exc


@dataclass(frozen=True)
class FingerprintSample:
    timestamp_ms: int
    value: str
    informative: bool = True

    def __post_init__(self) -> None:
        if (
            isinstance(self.timestamp_ms, bool)
            or not isinstance(self.timestamp_ms, int)
            or self.timestamp_ms < 0
        ):
            raise FingerprintError(
                "Fingerprint sample timestamps must be non-negative integers."
            )
        if not isinstance(self.value, str):
            raise FingerprintError(
                "Fingerprint samples must be hexadecimal text."
            )
        value = self.value.strip().casefold()
        if not value or any(ch not in "0123456789abcdef" for ch in value):
            raise FingerprintError(
                "Fingerprint samples must be hexadecimal values."
            )
        if not isinstance(self.informative, bool):
            raise FingerprintError(
                "Fingerprint sample informativeness must be boolean."
            )
        object.__setattr__(self, "value", value)


@dataclass(frozen=True)
class ContentFingerprint:
    file_id: int
    file_sha256: str
    runtime_ms: int
    algorithm: FingerprintAlgorithm
    samples: tuple[FingerprintSample, ...]
    source_kind: str = "local"
    source_signature: str = ""
    parameters: Mapping[str, Any] = field(default_factory=dict)
    comparison_parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            isinstance(self.file_id, bool)
            or not isinstance(self.file_id, int)
            or self.file_id < 1
        ):
            raise FingerprintError("Content fingerprints require a positive file ID.")
        digest = _valid_sha256(self.file_sha256)
        if (
            isinstance(self.runtime_ms, bool)
            or not isinstance(self.runtime_ms, int)
            or self.runtime_ms < 1
        ):
            raise FingerprintError(
                "Content fingerprints require a positive runtime."
            )
        if not isinstance(self.algorithm, FingerprintAlgorithm):
            raise FingerprintError(
                "Content fingerprints require an explicit algorithm identity."
            )
        samples = tuple(self.samples)
        if not samples or len(samples) > self.algorithm.max_samples:
            raise FingerprintError(
                "Content fingerprint sample count is outside the algorithm bound."
            )
        expected_hex_chars = self.algorithm.bits_per_sample // 4
        previous = -1
        seen: set[int] = set()
        for sample in samples:
            if not isinstance(sample, FingerprintSample):
                raise FingerprintError(
                    "Content fingerprints require FingerprintSample values."
                )
            if len(sample.value) != expected_hex_chars:
                raise FingerprintError(
                    "Fingerprint sample width does not match the algorithm."
                )
            if sample.timestamp_ms >= self.runtime_ms:
                raise FingerprintError(
                    "Fingerprint samples must fall inside the media runtime."
                )
            if sample.timestamp_ms in seen or sample.timestamp_ms <= previous:
                raise FingerprintError(
                    "Fingerprint samples must have unique increasing timestamps."
                )
            seen.add(sample.timestamp_ms)
            previous = sample.timestamp_ms

        if (
            not isinstance(self.source_kind, str)
            or not isinstance(self.source_signature, str)
        ):
            raise FingerprintError(
                "Content fingerprint source provenance must be text."
            )
        source_kind = self.source_kind.strip().casefold()
        source_signature = self.source_signature.strip()
        if not source_kind:
            raise FingerprintError(
                "Content fingerprints require source provenance."
            )
        if not source_signature:
            raise FingerprintError(
                "Content fingerprints require a source signature."
            )

        object.__setattr__(self, "file_sha256", digest)
        object.__setattr__(self, "samples", samples)
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "source_signature", source_signature)
        object.__setattr__(
            self,
            "parameters",
            _freeze_json(self.parameters),
        )
        object.__setattr__(
            self,
            "comparison_parameters",
            _freeze_json(self.comparison_parameters),
        )

    def cache_identity(self) -> dict[str, Any]:
        return {
            "contract_version": DEEP_FINGERPRINT_CONTRACT_VERSION,
            "file_id": self.file_id,
            "file_sha256": self.file_sha256,
            "runtime_ms": self.runtime_ms,
            "algorithm": self.algorithm.identity_payload(),
            "source_kind": self.source_kind,
            "source_signature": self.source_signature,
            "parameters": _json_ready(self.parameters),
            "comparison_parameters": _json_ready(
                self.comparison_parameters
            ),
        }

    def cache_key(self) -> str:
        return hashlib.sha256(
            _canonical_json(self.cache_identity()).encode("utf-8")
        ).hexdigest()

    def output_payload(self) -> dict[str, Any]:
        return {
            "identity": self.cache_identity(),
            "samples": [
                {
                    "timestamp_ms": sample.timestamp_ms,
                    "value": sample.value,
                    "informative": sample.informative,
                }
                for sample in self.samples
            ],
        }

    def output_seal(self) -> str:
        return hashlib.sha256(
            _canonical_json(self.output_payload()).encode("utf-8")
        ).hexdigest()

    def persisted_payload(self) -> dict[str, Any]:
        payload = self.output_payload()
        payload[FINGERPRINT_OUTPUT_SEAL_FIELD] = self.output_seal()
        return payload


def fingerprint_from_payload(value: object) -> ContentFingerprint:
    if not isinstance(value, Mapping):
        raise FingerprintError("Persisted fingerprint payload is missing.")
    seal = str(value.get(FINGERPRINT_OUTPUT_SEAL_FIELD) or "").strip().casefold()
    if (
        len(seal) != 64
        or any(character not in "0123456789abcdef" for character in seal)
    ):
        raise FingerprintError(
            "Persisted fingerprint output seal is missing or malformed."
        )
    identity = value.get("identity")
    samples = value.get("samples")
    if (
        not isinstance(identity, Mapping)
        or not isinstance(samples, list)
        or not isinstance(identity.get("parameters"), Mapping)
        or not isinstance(
            identity.get("comparison_parameters"),
            Mapping,
        )
    ):
        raise FingerprintError("Persisted fingerprint payload is incomplete.")
    try:
        contract_version = int(identity.get("contract_version") or 0)
    except (TypeError, ValueError) as exc:
        raise FingerprintError(
            "Persisted fingerprint contract version is malformed."
        ) from exc
    if contract_version != DEEP_FINGERPRINT_CONTRACT_VERSION:
        raise FingerprintError(
            "Persisted fingerprint contract version is not current."
        )
    algorithm_raw = identity.get("algorithm")
    if not isinstance(algorithm_raw, Mapping):
        raise FingerprintError("Persisted fingerprint algorithm is missing.")
    try:
        algorithm = FingerprintAlgorithm(
            key=algorithm_raw["key"],
            version=algorithm_raw["version"],
            family=FingerprintFamily(algorithm_raw["family"]),
            bits_per_sample=algorithm_raw["bits_per_sample"],
            max_samples=algorithm_raw["max_samples"],
        )
        if not all(isinstance(item, Mapping) for item in samples):
            raise FingerprintError(
                "Persisted fingerprint samples are malformed."
            )
        fingerprint = ContentFingerprint(
            file_id=identity["file_id"],
            file_sha256=identity["file_sha256"],
            runtime_ms=identity["runtime_ms"],
            algorithm=algorithm,
            samples=tuple(
                FingerprintSample(
                    timestamp_ms=item["timestamp_ms"],
                    value=item["value"],
                    informative=item["informative"],
                )
                for item in samples
            ),
            source_kind=identity["source_kind"],
            source_signature=identity["source_signature"],
            parameters=identity["parameters"],
            comparison_parameters=identity["comparison_parameters"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise FingerprintError(
            "Persisted fingerprint payload is malformed."
        ) from exc
    if fingerprint.output_seal() != seal:
        raise FingerprintError(
            "Persisted fingerprint output failed its integrity seal."
        )
    return fingerprint


@dataclass(frozen=True)
class FingerprintMatchPolicy:
    max_candidates: int = MAX_FINGERPRINT_CANDIDATES
    max_samples_per_fingerprint: int = MAX_FINGERPRINT_SAMPLES
    max_alignment_shift: int = MAX_FINGERPRINT_ALIGNMENT_SHIFT
    min_compared_samples: int = 6

    def __post_init__(self) -> None:
        limits = (
            ("candidate", self.max_candidates, 1, MAX_FINGERPRINT_CANDIDATES),
            (
                "sample",
                self.max_samples_per_fingerprint,
                1,
                MAX_FINGERPRINT_SAMPLES,
            ),
            (
                "alignment",
                self.max_alignment_shift,
                0,
                MAX_FINGERPRINT_ALIGNMENT_SHIFT,
            ),
            (
                "minimum compared sample",
                self.min_compared_samples,
                1,
                MAX_FINGERPRINT_SAMPLES,
            ),
        )
        for label, value, minimum, maximum in limits:
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < minimum
                or value > maximum
            ):
                raise FingerprintError(
                    f"Fingerprint {label} limit is outside the supported bound."
                )
        if self.min_compared_samples > self.max_samples_per_fingerprint:
            raise FingerprintError(
                "Minimum compared samples exceed the sample budget."
            )


@dataclass(frozen=True)
class FingerprintComparison:
    left_file_id: int
    right_file_id: int
    algorithm_key: str
    algorithm_version: str
    compared_samples: int
    alignment_shift: int
    coverage: float
    mean_similarity: float
    median_similarity: float
    minimum_similarity: float

    def __post_init__(self) -> None:
        for label, value in (
            ("left file ID", self.left_file_id),
            ("right file ID", self.right_file_id),
            ("compared sample count", self.compared_samples),
            ("alignment shift", self.alignment_shift),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise FingerprintError(
                    f"Fingerprint comparison {label} must be an integer."
                )
        if (
            self.left_file_id < 1
            or self.right_file_id < 1
            or self.left_file_id == self.right_file_id
        ):
            raise FingerprintError(
                "Fingerprint comparisons require two distinct positive file IDs."
            )
        if (
            self.compared_samples < 1
            or self.compared_samples > MAX_FINGERPRINT_SAMPLES
        ):
            raise FingerprintError(
                "Fingerprint comparison sample count is outside the supported bound."
            )
        if abs(self.alignment_shift) > MAX_FINGERPRINT_ALIGNMENT_SHIFT:
            raise FingerprintError(
                "Fingerprint comparison alignment exceeds the supported bound."
            )
        if (
            not isinstance(self.algorithm_key, str)
            or not isinstance(self.algorithm_version, str)
        ):
            raise FingerprintError(
                "Fingerprint comparison algorithm identity must be text."
            )
        algorithm_key = self.algorithm_key.strip().casefold()
        algorithm_version = self.algorithm_version.strip()
        if not algorithm_key or not algorithm_version:
            raise FingerprintError(
                "Fingerprint comparisons require algorithm identity."
            )
        object.__setattr__(self, "algorithm_key", algorithm_key)
        object.__setattr__(self, "algorithm_version", algorithm_version)
        for label, value in (
            ("coverage", self.coverage),
            ("mean similarity", self.mean_similarity),
            ("median similarity", self.median_similarity),
            ("minimum similarity", self.minimum_similarity),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                raise FingerprintError(
                    f"Fingerprint comparison {label} must be between 0 and 1."
                )
            object.__setattr__(self, {
                "coverage": "coverage",
                "mean similarity": "mean_similarity",
                "median similarity": "median_similarity",
                "minimum similarity": "minimum_similarity",
            }[label], float(value))

    @property
    def sort_key(self) -> tuple[float, float, float, int]:
        return (
            self.median_similarity,
            self.mean_similarity,
            self.coverage,
            self.compared_samples,
        )


def fingerprint_comparison_from_payload(
    value: object,
) -> FingerprintComparison:
    if not isinstance(value, Mapping):
        raise FingerprintError(
            "Persisted fingerprint comparison is not a mapping."
        )
    try:
        return FingerprintComparison(
            left_file_id=value["left_file_id"],
            right_file_id=value["right_file_id"],
            algorithm_key=value["algorithm_key"],
            algorithm_version=value["algorithm_version"],
            compared_samples=value["compared_samples"],
            alignment_shift=value["alignment_shift"],
            coverage=value["coverage"],
            mean_similarity=value["mean_similarity"],
            median_similarity=value["median_similarity"],
            minimum_similarity=value["minimum_similarity"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise FingerprintError(
            "Persisted fingerprint comparison is malformed."
        ) from exc


def _hamming_similarity(left: str, right: str, bits: int) -> float:
    distance = (int(left, 16) ^ int(right, 16)).bit_count()
    return 1.0 - (float(distance) / float(bits))


def compare_content_fingerprints(
    left: ContentFingerprint,
    right: ContentFingerprint,
    *,
    policy: FingerprintMatchPolicy | None = None,
) -> FingerprintComparison | None:
    policy = policy or FingerprintMatchPolicy()
    if (
        left.algorithm.identity_payload() != right.algorithm.identity_payload()
        or _json_ready(left.comparison_parameters)
        != _json_ready(right.comparison_parameters)
        or len(left.samples) > policy.max_samples_per_fingerprint
        or len(right.samples) > policy.max_samples_per_fingerprint
    ):
        return None

    best: FingerprintComparison | None = None
    bits = left.algorithm.bits_per_sample
    for shift in range(
        -policy.max_alignment_shift,
        policy.max_alignment_shift + 1,
    ):
        left_start = max(0, -shift)
        right_start = max(0, shift)
        available = min(
            len(left.samples) - left_start,
            len(right.samples) - right_start,
        )
        if available < policy.min_compared_samples:
            continue
        values = [
            _hamming_similarity(
                left.samples[left_start + index].value,
                right.samples[right_start + index].value,
                bits,
            )
            for index in range(available)
            if (
                left.samples[left_start + index].informative
                and right.samples[right_start + index].informative
            )
        ]
        if len(values) < policy.min_compared_samples:
            continue
        comparison = FingerprintComparison(
            left_file_id=left.file_id,
            right_file_id=right.file_id,
            algorithm_key=left.algorithm.key,
            algorithm_version=left.algorithm.version,
            compared_samples=len(values),
            alignment_shift=shift,
            coverage=float(len(values)) / float(
                max(len(left.samples), len(right.samples))
            ),
            mean_similarity=statistics.fmean(values),
            median_similarity=statistics.median(values),
            minimum_similarity=min(values),
        )
        if best is None or comparison.sort_key > best.sort_key:
            best = comparison
    return best


def bounded_fingerprint_matches(
    query: ContentFingerprint,
    candidates: Iterable[ContentFingerprint],
    *,
    policy: FingerprintMatchPolicy | None = None,
) -> tuple[FingerprintComparison, ...]:
    policy = policy or FingerprintMatchPolicy()
    candidate_list = tuple(candidates)
    if len(candidate_list) > policy.max_candidates:
        raise FingerprintError(
            "Fingerprint comparison candidate count exceeds the bounded policy."
        )
    seen: set[int] = set()
    comparisons: list[FingerprintComparison] = []
    for candidate in candidate_list:
        if candidate.file_id == query.file_id:
            continue
        if candidate.file_id in seen:
            raise FingerprintError(
                "Fingerprint candidate files must be unique."
            )
        seen.add(candidate.file_id)
        comparison = compare_content_fingerprints(
            query,
            candidate,
            policy=policy,
        )
        if comparison is not None:
            comparisons.append(comparison)
    return tuple(
        sorted(
            comparisons,
            key=lambda item: (
                item.sort_key,
                -item.right_file_id,
            ),
            reverse=True,
        )
    )

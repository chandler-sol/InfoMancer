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


@dataclass(frozen=True)
class FingerprintAlgorithm:
    key: str
    version: str
    family: FingerprintFamily
    bits_per_sample: int
    max_samples: int = MAX_FINGERPRINT_SAMPLES

    def __post_init__(self) -> None:
        key = str(self.key or "").strip().casefold()
        version = str(self.version or "").strip()
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

    def __post_init__(self) -> None:
        if (
            isinstance(self.timestamp_ms, bool)
            or not isinstance(self.timestamp_ms, int)
            or self.timestamp_ms < 0
        ):
            raise FingerprintError(
                "Fingerprint sample timestamps must be non-negative integers."
            )
        value = str(self.value or "").strip().casefold()
        if not value or any(ch not in "0123456789abcdef" for ch in value):
            raise FingerprintError(
                "Fingerprint samples must be hexadecimal values."
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

        source_kind = str(self.source_kind or "").strip().casefold()
        source_signature = str(self.source_signature or "").strip()
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
    if not isinstance(identity, Mapping) or not isinstance(samples, Sequence):
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
            key=str(algorithm_raw["key"]),
            version=str(algorithm_raw["version"]),
            family=FingerprintFamily(str(algorithm_raw["family"])),
            bits_per_sample=int(algorithm_raw["bits_per_sample"]),
            max_samples=int(algorithm_raw["max_samples"]),
        )
        fingerprint = ContentFingerprint(
            file_id=int(identity["file_id"]),
            file_sha256=str(identity["file_sha256"]),
            runtime_ms=int(identity["runtime_ms"]),
            algorithm=algorithm,
            samples=tuple(
                FingerprintSample(
                    timestamp_ms=int(item["timestamp_ms"]),
                    value=str(item["value"]),
                )
                for item in samples
                if isinstance(item, Mapping)
            ),
            source_kind=str(identity["source_kind"]),
            source_signature=str(identity["source_signature"]),
            parameters=(
                identity.get("parameters")
                if isinstance(identity.get("parameters"), Mapping)
                else {}
            ),
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

    @property
    def sort_key(self) -> tuple[float, float, float, int]:
        return (
            self.median_similarity,
            self.mean_similarity,
            self.coverage,
            self.compared_samples,
        )


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
        or _json_ready(left.parameters) != _json_ready(right.parameters)
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
        ]
        comparison = FingerprintComparison(
            left_file_id=left.file_id,
            right_file_id=right.file_id,
            algorithm_key=left.algorithm.key,
            algorithm_version=left.algorithm.version,
            compared_samples=available,
            alignment_shift=shift,
            coverage=float(available) / float(
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

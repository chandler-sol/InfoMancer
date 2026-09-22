from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable

from .models import MediaIdentityFile


SPEECH_CACHE_VERSION = 2
MAX_NORMAL_SPEECH_WINDOW_MS = 90_000
MAX_NORMAL_SPEECH_TOTAL_MS = 240_000
_MAX_IDENTITY_DEPTH = 16


class SpeechIdentityError(ValueError):
    """Raised when a speech-analysis request cannot be identified safely."""


def _freeze_json_like(
    value: Any,
    label: str,
    *,
    _depth: int = 0,
    _seen: set[int] | None = None,
) -> Any:
    """Snapshot deterministic JSON-like data into immutable containers."""
    if _depth > _MAX_IDENTITY_DEPTH:
        raise SpeechIdentityError(f"{label} is nested too deeply.")

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SpeechIdentityError(f"{label} contains a non-finite number.")
        return value

    seen = _seen if _seen is not None else set()
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            raise SpeechIdentityError(f"{label} contains a recursive mapping.")
        seen.add(identity)
        try:
            frozen: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise SpeechIdentityError(
                        f"{label} mapping keys must be strings."
                    )
                frozen[key] = _freeze_json_like(
                    item,
                    label,
                    _depth=_depth + 1,
                    _seen=seen,
                )
            return MappingProxyType(frozen)
        finally:
            seen.remove(identity)

    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in seen:
            raise SpeechIdentityError(f"{label} contains a recursive sequence.")
        seen.add(identity)
        try:
            return tuple(
                _freeze_json_like(
                    item,
                    label,
                    _depth=_depth + 1,
                    _seen=seen,
                )
                for item in value
            )
        finally:
            seen.remove(identity)

    raise SpeechIdentityError(
        f"{label} contains an unsupported value of type "
        f"{type(value).__name__}."
    )


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    frozen = _freeze_json_like(value, "Speech cache identity")
    try:
        return json.dumps(
            _json_ready(frozen),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise SpeechIdentityError(
            "Speech cache identity could not be serialized deterministically."
        ) from exc


def _normalized_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise SpeechIdentityError(f"{label} must be text.")
    return value.strip()


def _validated_size(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SpeechIdentityError(f"{label} must be a positive integer.")
    return value


@dataclass(frozen=True)
class SpeechBinaryIdentity:
    """Stable identity for one local speech-engine executable."""

    key: str
    version: str
    sha256: str
    size_bytes: int
    source: str = ""
    license_id: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        key = _normalized_text(self.key, "Speech binary key")
        version = _normalized_text(self.version, "Speech binary version")
        digest = _normalized_text(
            self.sha256,
            "Speech binary SHA-256",
        ).casefold()
        size_bytes = _validated_size(
            self.size_bytes,
            "Speech binary size",
        )
        if not key:
            raise SpeechIdentityError("Speech binaries require a stable key.")
        if not version:
            raise SpeechIdentityError("Speech binaries require a stable version.")
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise SpeechIdentityError(
                "Speech binary identity requires a SHA-256 digest."
            )
        if not isinstance(self.details, Mapping):
            raise SpeechIdentityError("Speech binary details must be a mapping.")

        object.__setattr__(self, "key", key)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "sha256", digest)
        object.__setattr__(self, "size_bytes", size_bytes)
        object.__setattr__(
            self,
            "details",
            _freeze_json_like(self.details, "Speech binary details"),
        )

    def cache_identity(self) -> Mapping[str, Any]:
        return {
            "key": self.key.casefold(),
            "version": self.version,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class SpeechModelIdentity:
    """Stable identity for one local speech model artifact."""

    key: str
    version: str
    sha256: str
    size_bytes: int
    source: str = ""
    license_id: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        key = _normalized_text(self.key, "Speech model key")
        version = _normalized_text(self.version, "Speech model version")
        digest = _normalized_text(
            self.sha256,
            "Speech model SHA-256",
        ).casefold()
        size_bytes = _validated_size(
            self.size_bytes,
            "Speech model size",
        )
        if not key:
            raise SpeechIdentityError("Speech models require a stable key.")
        if not version:
            raise SpeechIdentityError("Speech models require a stable version.")
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise SpeechIdentityError(
                "Speech model identity requires a SHA-256 digest."
            )
        if not isinstance(self.details, Mapping):
            raise SpeechIdentityError("Speech model details must be a mapping.")

        object.__setattr__(self, "key", key)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "sha256", digest)
        object.__setattr__(self, "size_bytes", size_bytes)
        object.__setattr__(
            self,
            "details",
            _freeze_json_like(self.details, "Speech model details"),
        )

    def cache_identity(self) -> Mapping[str, Any]:
        return {
            "key": self.key.casefold(),
            "version": self.version,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class SpeechWindow:
    start_ms: int
    end_ms: int
    purpose: str = ""

    def __post_init__(self) -> None:
        if (
            isinstance(self.start_ms, bool)
            or not isinstance(self.start_ms, int)
            or isinstance(self.end_ms, bool)
            or not isinstance(self.end_ms, int)
        ):
            raise SpeechIdentityError(
                "Speech window boundaries must be integer milliseconds."
            )
        purpose = _normalized_text(self.purpose, "Speech window purpose")
        if self.start_ms < 0:
            raise SpeechIdentityError("Speech windows cannot start before zero.")
        if self.end_ms <= self.start_ms:
            raise SpeechIdentityError(
                "Speech windows require an end after their start."
            )
        if self.duration_ms > MAX_NORMAL_SPEECH_WINDOW_MS:
            raise SpeechIdentityError(
                "Normal speech windows cannot exceed 90 seconds."
            )
        object.__setattr__(self, "purpose", purpose)

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def key(self) -> str:
        return f"{self.start_ms}:{self.end_ms}"


@dataclass(frozen=True)
class SpeechRequest:
    media: MediaIdentityFile
    window: SpeechWindow
    model: SpeechModelIdentity
    language: str = ""
    translate: bool = False
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.media, MediaIdentityFile):
            raise SpeechIdentityError(
                "Speech requests require a media identity snapshot."
            )
        if not isinstance(self.window, SpeechWindow):
            raise SpeechIdentityError("Speech requests require a speech window.")
        if not isinstance(self.model, SpeechModelIdentity):
            raise SpeechIdentityError("Speech requests require a speech model.")
        language = _normalized_text(self.language, "Speech language identifier")
        if language and len(language) > 32:
            raise SpeechIdentityError("Speech language identifiers are too long.")
        if not isinstance(self.translate, bool):
            raise SpeechIdentityError("Speech translation mode must be boolean.")
        if not isinstance(self.parameters, Mapping):
            raise SpeechIdentityError("Speech parameters must be a mapping.")

        object.__setattr__(self, "language", language)
        object.__setattr__(
            self,
            "parameters",
            _freeze_json_like(self.parameters, "Speech parameters"),
        )

    def cache_parameters(self) -> Mapping[str, Any]:
        return {
            "language": self.language.casefold(),
            "translate": self.translate,
            "parameters": self.parameters,
        }


@dataclass(frozen=True)
class SpeechTranscript:
    text: str
    language: str = ""
    confidence: float | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise SpeechIdentityError("Speech transcript text must be text.")
        language = _normalized_text(
            self.language,
            "Speech transcript language",
        )
        confidence = self.confidence
        if confidence is not None:
            if (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
            ):
                raise SpeechIdentityError(
                    "Speech transcript confidence must be numeric."
                )
            confidence = float(confidence)
            if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
                raise SpeechIdentityError(
                    "Speech transcript confidence must be between 0 and 1."
                )
        if not isinstance(self.details, Mapping):
            raise SpeechIdentityError("Speech transcript details must be a mapping.")

        object.__setattr__(self, "language", language)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(
            self,
            "details",
            _freeze_json_like(self.details, "Speech transcript details"),
        )


@runtime_checkable
class SpeechEngine(Protocol):
    """Backend-neutral local speech transcription contract."""

    key: str
    version: str

    def available(self) -> bool:
        """Return whether the engine binary/runtime is currently usable."""
        ...

    def binary_identity(self) -> SpeechBinaryIdentity:
        """Return the exact executable identity used for transcription."""
        ...

    def cache_identity(self) -> Mapping[str, Any]:
        """Return deterministic output-affecting runtime configuration."""
        ...

    def transcribe(self, audio_path: str, request: SpeechRequest) -> SpeechTranscript:
        """Transcribe one bounded local audio window without deciding identity."""
        ...


def speech_transcript_cache_key(
    request: SpeechRequest,
    engine: SpeechEngine,
) -> str:
    raw_key = getattr(engine, "key", "")
    raw_version = getattr(engine, "version", "")
    if not isinstance(raw_key, str) or not isinstance(raw_version, str):
        raise SpeechIdentityError(
            "Speech engines require stable text key and version values."
        )
    key = raw_key.strip().casefold()
    version = raw_version.strip()
    if not key or not version:
        raise SpeechIdentityError(
            "Speech engines require stable key and version values."
        )

    try:
        binary_identity = engine.binary_identity()
    except (AttributeError, TypeError, ValueError) as exc:
        raise SpeechIdentityError(
            "Speech engines require an exact binary identity."
        ) from exc
    if not isinstance(binary_identity, SpeechBinaryIdentity):
        raise SpeechIdentityError(
            "Speech engines require an exact binary identity."
        )

    try:
        raw_engine_identity = engine.cache_identity()
    except (AttributeError, TypeError, ValueError) as exc:
        raise SpeechIdentityError(
            "Speech engines require a deterministic cache identity."
        ) from exc
    if not isinstance(raw_engine_identity, Mapping):
        raise SpeechIdentityError(
            "Speech engines require a deterministic cache identity."
        )
    engine_identity = _freeze_json_like(
        raw_engine_identity,
        "Speech engine cache identity",
    )
    if not engine_identity:
        raise SpeechIdentityError(
            "Speech engines require a deterministic cache identity."
        )

    media = request.media
    payload = {
        "cache_version": SPEECH_CACHE_VERSION,
        "engine": {
            "key": key,
            "version": version,
            "binary": binary_identity.cache_identity(),
            "identity": engine_identity,
        },
        "model": request.model.cache_identity(),
        "media": {
            "file_id": int(media.file_id),
            "size_bytes": int(media.size_bytes),
            "modified_at": media.modified_at,
            "sha256": media.sha256 or "",
        },
        "window": {
            "start_ms": request.window.start_ms,
            "end_ms": request.window.end_ms,
            "purpose": request.window.purpose.casefold(),
        },
        "request": request.cache_parameters(),
    }
    return hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()

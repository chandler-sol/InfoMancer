from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Mapping, Protocol, runtime_checkable

from .models import MediaIdentityFile


SPEECH_CACHE_VERSION = 1
MAX_NORMAL_SPEECH_WINDOW_MS = 90_000
MAX_NORMAL_SPEECH_TOTAL_MS = 240_000


class SpeechIdentityError(ValueError):
    """Raised when a speech-analysis request cannot be identified safely."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


@dataclass(frozen=True)
class SpeechModelIdentity:
    """Stable identity for one local speech model artifact."""

    key: str
    version: str
    sha256: str
    source: str = ""
    license_id: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        key = self.key.strip()
        version = self.version.strip()
        digest = self.sha256.strip().casefold()
        if not key:
            raise SpeechIdentityError("Speech models require a stable key.")
        if not version:
            raise SpeechIdentityError("Speech models require a stable version.")
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise SpeechIdentityError(
                "Speech model identity requires a SHA-256 digest."
            )

    def cache_identity(self) -> Mapping[str, Any]:
        return {
            "key": self.key.strip().casefold(),
            "version": self.version.strip(),
            "sha256": self.sha256.strip().casefold(),
        }


@dataclass(frozen=True)
class SpeechWindow:
    start_ms: int
    end_ms: int
    purpose: str = ""

    def __post_init__(self) -> None:
        if int(self.start_ms) < 0:
            raise SpeechIdentityError("Speech windows cannot start before zero.")
        if int(self.end_ms) <= int(self.start_ms):
            raise SpeechIdentityError(
                "Speech windows require an end after their start."
            )
        if self.duration_ms > MAX_NORMAL_SPEECH_WINDOW_MS:
            raise SpeechIdentityError(
                "Normal speech windows cannot exceed 90 seconds."
            )

    @property
    def duration_ms(self) -> int:
        return int(self.end_ms) - int(self.start_ms)

    @property
    def key(self) -> str:
        return f"{int(self.start_ms)}:{int(self.end_ms)}"


@dataclass(frozen=True)
class SpeechRequest:
    media: MediaIdentityFile
    window: SpeechWindow
    model: SpeechModelIdentity
    language: str = ""
    translate: bool = False
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        language = self.language.strip()
        if language and len(language) > 32:
            raise SpeechIdentityError("Speech language identifiers are too long.")

    def cache_parameters(self) -> Mapping[str, Any]:
        return {
            "language": self.language.strip().casefold(),
            "translate": bool(self.translate),
            "parameters": dict(self.parameters),
        }


@dataclass(frozen=True)
class SpeechTranscript:
    text: str
    language: str = ""
    confidence: float | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.confidence is not None and not 0.0 <= float(self.confidence) <= 1.0:
            raise SpeechIdentityError(
                "Speech transcript confidence must be between 0 and 1."
            )


@runtime_checkable
class SpeechEngine(Protocol):
    """Backend-neutral local speech transcription contract."""

    key: str
    version: str

    def available(self) -> bool:
        """Return whether the engine binary/runtime is currently usable."""
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
    key = str(getattr(engine, "key", "") or "").strip().casefold()
    version = str(getattr(engine, "version", "") or "").strip()
    if not key or not version:
        raise SpeechIdentityError(
            "Speech engines require stable key and version values."
        )
    try:
        engine_identity = dict(engine.cache_identity())
    except (AttributeError, TypeError, ValueError) as exc:
        raise SpeechIdentityError(
            "Speech engines require a deterministic cache identity."
        ) from exc
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
            "identity": engine_identity,
        },
        "model": dict(request.model.cache_identity()),
        "media": {
            "file_id": int(media.file_id),
            "size_bytes": int(media.size_bytes),
            "modified_at": media.modified_at,
            "sha256": media.sha256 or "",
        },
        "window": {
            "start_ms": int(request.window.start_ms),
            "end_ms": int(request.window.end_ms),
            "purpose": request.window.purpose.strip().casefold(),
        },
        "request": dict(request.cache_parameters()),
    }
    return hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()

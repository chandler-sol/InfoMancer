from __future__ import annotations

from array import array
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from ..media_info import _quiet_subprocess_options, ffmpeg_executable
from .fingerprint import (
    AUDIO_ENVELOPE_DHASH64_V1,
    ContentFingerprint,
    FingerprintError,
    FingerprintSample,
)
from .fingerprint_local import (
    LocalFingerprintError,
    fingerprint_ffmpeg_identity,
)
from .media_generation import (
    MediaContentLease,
    MediaContentLeaseError,
    media_generation_identity,
)
from .models import MediaIdentityFile


DEFAULT_AUDIO_FINGERPRINT_SAMPLES = 8
MIN_AUDIO_FINGERPRINT_SAMPLES = 6
MAX_AUDIO_FINGERPRINT_SAMPLES = AUDIO_ENVELOPE_DHASH64_V1.max_samples
AUDIO_FINGERPRINT_EXTRACTOR_VERSION = 1
AUDIO_FINGERPRINT_WINDOW_MS = 4_000
AUDIO_FINGERPRINT_SAMPLE_RATE_HZ = 8_000
AUDIO_FINGERPRINT_CHANNELS = 1
AUDIO_FINGERPRINT_SAMPLE_WIDTH_BYTES = 2
_AUDIO_FEATURE_BINS = 33
_DEFAULT_TIMEOUT_SECONDS = 20
_MAX_TIMEOUT_SECONDS = 60


def _canonical_json(value: Any) -> str:
    import json

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def plan_audio_fingerprint_timestamps(
    runtime_ms: int,
    *,
    sample_count: int = DEFAULT_AUDIO_FINGERPRINT_SAMPLES,
) -> tuple[int, ...]:
    """Plan deterministic interior window centers for lightweight audio hashes."""

    if (
        isinstance(runtime_ms, bool)
        or not isinstance(runtime_ms, int)
        or runtime_ms <= AUDIO_FINGERPRINT_WINDOW_MS
    ):
        raise FingerprintError(
            "Audio fingerprint planning requires media longer than one window."
        )
    if (
        isinstance(sample_count, bool)
        or not isinstance(sample_count, int)
        or sample_count < MIN_AUDIO_FINGERPRINT_SAMPLES
        or sample_count > MAX_AUDIO_FINGERPRINT_SAMPLES
    ):
        raise FingerprintError(
            "Audio fingerprint sample count is outside the supported bound."
        )

    half_window = AUDIO_FINGERPRINT_WINDOW_MS // 2
    lower = max(half_window, int(round(runtime_ms * 0.10)))
    upper = min(
        runtime_ms - half_window - 1,
        int(round(runtime_ms * 0.90)),
    )
    if upper <= lower:
        raise FingerprintError(
            "Media runtime is too short for the audio fingerprint plan."
        )

    planned: list[int] = []
    span = upper - lower
    for index in range(sample_count):
        center = lower + int(
            round(((index + 0.5) / sample_count) * span)
        )
        center = max(lower, min(upper, center))
        if planned and center <= planned[-1]:
            center = planned[-1] + 1
        if center > upper:
            raise FingerprintError(
                "Media runtime cannot fit the requested audio fingerprint plan."
            )
        planned.append(center)
    return tuple(planned)


def audio_envelope_dhash64_from_pcm_s16le(payload: bytes) -> str:
    """Hash relative short-term energy and zero-crossing shape into 64 bits."""

    raw = bytes(payload)
    if len(raw) % AUDIO_FINGERPRINT_SAMPLE_WIDTH_BYTES:
        raise FingerprintError(
            "Audio fingerprint PCM must contain complete 16-bit samples."
        )
    samples = array("h")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    if len(samples) < _AUDIO_FEATURE_BINS:
        raise FingerprintError(
            "Audio fingerprint PCM is too short for the feature contract."
        )

    energy: list[int] = []
    zero_crossings: list[int] = []
    count = len(samples)
    for index in range(_AUDIO_FEATURE_BINS):
        start = (index * count) // _AUDIO_FEATURE_BINS
        end = ((index + 1) * count) // _AUDIO_FEATURE_BINS
        if end <= start:
            raise FingerprintError(
                "Audio fingerprint PCM cannot fill every feature bin."
            )
        total = 0
        crossings = 0
        previous = int(samples[start])
        for position in range(start, end):
            value = int(samples[position])
            total += abs(value)
            if position > start and (
                (previous < 0 <= value)
                or (previous >= 0 > value)
            ):
                crossings += 1
            previous = value
        energy.append(total // (end - start))
        zero_crossings.append(crossings)

    value = 0
    for features in (energy, zero_crossings):
        for index in range(_AUDIO_FEATURE_BINS - 1):
            value <<= 1
            if features[index] > features[index + 1]:
                value |= 1
    return f"{value:016x}"


class LocalAudioFingerprintExtractor:
    """Extract bounded perceptual audio signatures from exact leased media."""

    def __init__(
        self,
        media: MediaIdentityFile,
        runtime_ms: int,
        *,
        executable: str | None = None,
        sample_count: int = DEFAULT_AUDIO_FINGERPRINT_SAMPLES,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not isinstance(media, MediaIdentityFile):
            raise FingerprintError(
                "Local audio fingerprinting requires MediaIdentityFile."
            )
        if not media.sha256:
            raise FingerprintError(
                "Local audio fingerprinting requires an exact media SHA-256."
            )
        self.media = media
        self.runtime_ms = int(runtime_ms)
        self.timestamps = plan_audio_fingerprint_timestamps(
            self.runtime_ms,
            sample_count=sample_count,
        )
        requested = str(executable or ffmpeg_executable())
        self.ffmpeg_identity = fingerprint_ffmpeg_identity(requested)
        self.executable = (
            str(self.ffmpeg_identity["path"])
            if self.ffmpeg_identity is not None
            else requested
        )
        self.timeout_seconds = max(
            1,
            min(int(timeout_seconds), _MAX_TIMEOUT_SECONDS),
        )
        self.media_generation = media_generation_identity(media.path)
        self.source_signature = self._source_signature()

    def _source_signature(self) -> str:
        if self.ffmpeg_identity is None or self.media_generation is None:
            return ""
        payload = {
            "extractor_version": AUDIO_FINGERPRINT_EXTRACTOR_VERSION,
            "algorithm": AUDIO_ENVELOPE_DHASH64_V1.identity_payload(),
            "media": {
                "file_id": self.media.file_id,
                "file_sha256": self.media.sha256,
                "runtime_ms": self.runtime_ms,
                "generation": self.media_generation,
            },
            "ffmpeg": self.ffmpeg_identity,
            "timestamps": list(self.timestamps),
            "window_ms": AUDIO_FINGERPRINT_WINDOW_MS,
            "stream_selector": "0:a:0",
            "format": {
                "codec": "pcm_s16le",
                "sample_rate_hz": AUDIO_FINGERPRINT_SAMPLE_RATE_HZ,
                "channels": AUDIO_FINGERPRINT_CHANNELS,
            },
            "features": {
                "bins": _AUDIO_FEATURE_BINS,
                "energy": "mean_abs",
                "zero_crossings": True,
            },
        }
        return hashlib.sha256(
            _canonical_json(payload).encode("utf-8")
        ).hexdigest()

    def available(self) -> bool:
        return bool(
            self.source_signature
            and self.ffmpeg_identity is not None
            and self.media_generation is not None
        )

    def _extract_pcm_window(
        self,
        lease: MediaContentLease,
        center_ms: int,
    ) -> bytes:
        start_ms = max(
            0,
            center_ms - AUDIO_FINGERPRINT_WINDOW_MS // 2,
        )
        expected_bytes = (
            AUDIO_FINGERPRINT_WINDOW_MS
            * AUDIO_FINGERPRINT_SAMPLE_RATE_HZ
            * AUDIO_FINGERPRINT_CHANNELS
            * AUDIO_FINGERPRINT_SAMPLE_WIDTH_BYTES
            // 1000
        )
        try:
            with lease.ffmpeg_input() as (
                input_args,
                lease_subprocess_options,
            ):
                command = [
                    self.executable,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{float(start_ms) / 1000.0:.3f}",
                    *input_args,
                    "-map",
                    "0:a:0",
                    "-t",
                    f"{float(AUDIO_FINGERPRINT_WINDOW_MS) / 1000.0:.3f}",
                    "-ac",
                    str(AUDIO_FINGERPRINT_CHANNELS),
                    "-ar",
                    str(AUDIO_FINGERPRINT_SAMPLE_RATE_HZ),
                    "-c:a",
                    "pcm_s16le",
                    "-f",
                    "s16le",
                    "pipe:1",
                ]
                options = {
                    **_quiet_subprocess_options(),
                    **lease_subprocess_options,
                }
                result = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=self.timeout_seconds,
                    check=False,
                    **options,
                )
        except MediaContentLeaseError as exc:
            raise LocalFingerprintError(
                "The leased media changed during audio fingerprint extraction."
            ) from exc
        except FileNotFoundError as exc:
            raise LocalFingerprintError(
                "FFmpeg is unavailable for audio fingerprint extraction."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise LocalFingerprintError(
                "FFmpeg timed out during audio fingerprint extraction."
            ) from exc
        except OSError as exc:
            raise LocalFingerprintError(
                "InfoMancer could not start FFmpeg for audio fingerprint extraction."
            ) from exc

        if result.returncode:
            raise LocalFingerprintError(
                "FFmpeg could not decode a planned audio fingerprint window."
            )
        raw = bytes(result.stdout or b"")
        if len(raw) != expected_bytes:
            raise LocalFingerprintError(
                "FFmpeg returned an incomplete canonical audio fingerprint window."
            )
        return raw

    def extract(self) -> ContentFingerprint:
        if not self.available():
            raise LocalFingerprintError(
                "Audio fingerprint extraction is unavailable for this media snapshot."
            )
        try:
            with MediaContentLease(
                self.media.path,
                self.media.sha256 or "",
                expected_generation=self.media_generation,
            ) as lease:
                samples = tuple(
                    FingerprintSample(
                        timestamp_ms=center_ms,
                        value=audio_envelope_dhash64_from_pcm_s16le(
                            self._extract_pcm_window(lease, center_ms)
                        ),
                    )
                    for center_ms in self.timestamps
                )
                lease.require_current()
        except (MediaContentLeaseError, FingerprintError) as exc:
            raise LocalFingerprintError(str(exc)) from exc

        if len(samples) != len(self.timestamps):
            raise LocalFingerprintError(
                "Audio fingerprint extraction did not complete every planned window."
            )
        return ContentFingerprint(
            file_id=self.media.file_id,
            file_sha256=self.media.sha256 or "",
            runtime_ms=self.runtime_ms,
            algorithm=AUDIO_ENVELOPE_DHASH64_V1,
            samples=samples,
            source_kind="local_ffmpeg_audio",
            source_signature=self.source_signature,
            parameters={
                "extractor_version": AUDIO_FINGERPRINT_EXTRACTOR_VERSION,
                "sample_count": len(self.timestamps),
                "window_ms": AUDIO_FINGERPRINT_WINDOW_MS,
                "sample_rate_hz": AUDIO_FINGERPRINT_SAMPLE_RATE_HZ,
                "channels": AUDIO_FINGERPRINT_CHANNELS,
                "stream_selector": "0:a:0",
                "feature_bins": _AUDIO_FEATURE_BINS,
            },
        )

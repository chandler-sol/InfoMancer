from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import stat as stat_module
import subprocess
from typing import Any

from ..media_info import _quiet_subprocess_options, ffmpeg_executable
from .fingerprint import (
    ContentFingerprint,
    FingerprintError,
    FingerprintSample,
    VIDEO_DHASH64_V1,
)
from .media_generation import (
    MediaContentLease,
    MediaContentLeaseError,
    media_generation_identity,
)
from .models import MediaIdentityFile


DEFAULT_VIDEO_FINGERPRINT_SAMPLES = 16
MIN_VIDEO_FINGERPRINT_SAMPLES = 6
MAX_VIDEO_FINGERPRINT_SAMPLES = VIDEO_DHASH64_V1.max_samples
VIDEO_FINGERPRINT_EXTRACTOR_VERSION = 1
_RAW_FRAME_BYTES = 9 * 8
_DEFAULT_TIMEOUT_SECONDS = 20
_MAX_TIMEOUT_SECONDS = 60


class LocalFingerprintError(RuntimeError):
    """Local perceptual fingerprint extraction could not complete safely."""


def _canonical_json(value: Any) -> str:
    import json

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def fingerprint_ffmpeg_identity(executable: str) -> dict[str, Any] | None:
    raw = str(executable or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if not (candidate.is_absolute() or candidate.parent != Path(".")):
        resolved = shutil.which(raw)
        if not resolved:
            return None
        candidate = Path(resolved)
    try:
        candidate = candidate.resolve(strict=True)
        stat = candidate.stat()
    except OSError:
        return None
    if not stat_module.S_ISREG(stat.st_mode):
        return None
    if (
        os.name != "nt"
        and not stat.st_mode
        & (stat_module.S_IXUSR | stat_module.S_IXGRP | stat_module.S_IXOTH)
    ):
        return None
    return {
        "path": str(candidate),
        "size_bytes": int(stat.st_size),
        "modified_ns": int(
            getattr(
                stat,
                "st_mtime_ns",
                int(float(stat.st_mtime) * 1_000_000_000),
            )
        ),
        "device_id": int(getattr(stat, "st_dev", 0) or 0) or None,
        "inode_id": int(getattr(stat, "st_ino", 0) or 0) or None,
    }


def plan_video_fingerprint_timestamps(
    runtime_ms: int,
    *,
    sample_count: int = DEFAULT_VIDEO_FINGERPRINT_SAMPLES,
) -> tuple[int, ...]:
    if (
        isinstance(runtime_ms, bool)
        or not isinstance(runtime_ms, int)
        or runtime_ms < 1
    ):
        raise FingerprintError(
            "Video fingerprint planning requires a positive runtime."
        )
    if (
        isinstance(sample_count, bool)
        or not isinstance(sample_count, int)
        or sample_count < MIN_VIDEO_FINGERPRINT_SAMPLES
        or sample_count > MAX_VIDEO_FINGERPRINT_SAMPLES
    ):
        raise FingerprintError(
            "Video fingerprint sample count is outside the supported bound."
        )

    # Avoid credits/opening edges, which are often shared between otherwise
    # different episodes. The deterministic interior lattice also leaves room
    # for the matcher's small sequence alignment shift.
    lower = 0.10
    upper = 0.90
    span = upper - lower
    planned: list[int] = []
    for index in range(sample_count):
        fraction = lower + ((index + 0.5) / sample_count) * span
        timestamp = max(
            0,
            min(runtime_ms - 1, int(round(runtime_ms * fraction))),
        )
        if planned and timestamp <= planned[-1]:
            timestamp = min(runtime_ms - 1, planned[-1] + 1)
        if planned and timestamp <= planned[-1]:
            raise FingerprintError(
                "Media runtime is too short for the requested fingerprint plan."
            )
        planned.append(timestamp)
    return tuple(planned)


def gray9x8_is_informative(payload: bytes) -> bool:
    raw = bytes(payload)
    if len(raw) != _RAW_FRAME_BYTES:
        raise FingerprintError(
            "Video dHash requires exactly one 9x8 grayscale frame."
        )
    return (
        max(raw) - min(raw) >= 8
        and len(set(raw)) >= 4
    )


def dhash64_from_gray9x8(payload: bytes) -> str:
    raw = bytes(payload)
    if len(raw) != _RAW_FRAME_BYTES:
        raise FingerprintError(
            "Video dHash requires exactly one 9x8 grayscale frame."
        )
    value = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            value <<= 1
            if raw[offset + column] > raw[offset + column + 1]:
                value |= 1
    return f"{value:016x}"


class LocalVideoFingerprintExtractor:
    """Extract a bounded sequence of perceptual video hashes from exact media bytes."""

    def __init__(
        self,
        media: MediaIdentityFile,
        runtime_ms: int,
        *,
        executable: str | None = None,
        sample_count: int = DEFAULT_VIDEO_FINGERPRINT_SAMPLES,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not isinstance(media, MediaIdentityFile):
            raise FingerprintError(
                "Local video fingerprinting requires MediaIdentityFile."
            )
        if not media.sha256:
            raise FingerprintError(
                "Local video fingerprinting requires an exact media SHA-256."
            )
        self.media = media
        self.runtime_ms = int(runtime_ms)
        self.timestamps = plan_video_fingerprint_timestamps(
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
            "extractor_version": VIDEO_FINGERPRINT_EXTRACTOR_VERSION,
            "algorithm": VIDEO_DHASH64_V1.identity_payload(),
            "media": {
                "file_id": self.media.file_id,
                "file_sha256": self.media.sha256,
                "runtime_ms": self.runtime_ms,
                "generation": self.media_generation,
            },
            "ffmpeg": self.ffmpeg_identity,
            "timestamps": list(self.timestamps),
            "filter": "scale=9:8:flags=area,format=gray",
            "output": "rawvideo-gray8",
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

    def _extract_raw_frame(
        self,
        lease: MediaContentLease,
        timestamp_ms: int,
    ) -> bytes:
        timestamp_seconds = float(timestamp_ms) / 1000.0
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
                    f"{timestamp_seconds:.3f}",
                    *input_args,
                    "-map",
                    "0:v:0",
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=9:8:flags=area,format=gray",
                    "-pix_fmt",
                    "gray",
                    "-f",
                    "rawvideo",
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
                "The leased media changed during fingerprint extraction."
            ) from exc
        except FileNotFoundError as exc:
            raise LocalFingerprintError(
                "FFmpeg is unavailable for video fingerprint extraction."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise LocalFingerprintError(
                "FFmpeg timed out during video fingerprint extraction."
            ) from exc
        except OSError as exc:
            raise LocalFingerprintError(
                "InfoMancer could not start FFmpeg for video fingerprint extraction."
            ) from exc

        if result.returncode:
            raise LocalFingerprintError(
                "FFmpeg could not decode a planned video fingerprint sample."
            )
        raw = bytes(result.stdout or b"")
        if len(raw) != _RAW_FRAME_BYTES:
            raise LocalFingerprintError(
                "FFmpeg returned an incomplete video fingerprint sample."
            )
        return raw

    def extract(self) -> ContentFingerprint:
        if not self.available():
            raise LocalFingerprintError(
                "Video fingerprint extraction is unavailable for this media snapshot."
            )
        try:
            with MediaContentLease(
                self.media.path,
                self.media.sha256 or "",
                expected_generation=self.media_generation,
            ) as lease:
                samples = tuple(
                    FingerprintSample(
                        timestamp_ms=timestamp_ms,
                        value=dhash64_from_gray9x8(raw_frame),
                        informative=gray9x8_is_informative(raw_frame),
                    )
                    for timestamp_ms in self.timestamps
                    for raw_frame in (
                        self._extract_raw_frame(lease, timestamp_ms),
                    )
                )
                lease.require_current()
        except (MediaContentLeaseError, FingerprintError) as exc:
            raise LocalFingerprintError(str(exc)) from exc

        if len(samples) != len(self.timestamps):
            raise LocalFingerprintError(
                "Video fingerprint extraction did not complete every planned sample."
            )
        return ContentFingerprint(
            file_id=self.media.file_id,
            file_sha256=self.media.sha256 or "",
            runtime_ms=self.runtime_ms,
            algorithm=VIDEO_DHASH64_V1,
            samples=samples,
            source_kind="local_ffmpeg",
            source_signature=self.source_signature,
            parameters={
                "extractor_version": VIDEO_FINGERPRINT_EXTRACTOR_VERSION,
                "sample_count": len(self.timestamps),
                "filter": "scale=9:8:flags=area,format=gray",
            },
            comparison_parameters={
                "sample_count": len(self.timestamps),
                "lattice": "interior-10-90",
                "filter": "scale=9:8:flags=area,format=gray",
                "hash": "horizontal-dhash64",
            },
        )

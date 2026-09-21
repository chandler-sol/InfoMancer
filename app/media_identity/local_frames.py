from __future__ import annotations

from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat as stat_module
import subprocess
from typing import Any

from PIL import Image, UnidentifiedImageError

from ..media_info import _quiet_subprocess_options, ffmpeg_executable
from .external import (
    ExternalCapability,
    ExternalMediaRef,
    ExternalPreviewUnavailable,
    ExternalSourceFailure,
    ExternalSourceStatus,
    PreviewFrameRef,
)
from .models import AnalyzerContext


LOCAL_FRAME_SOURCE_KEY = "local-ffmpeg"
LOCAL_FRAME_SOURCE_VERSION = "1"
_LOCAL_FRAME_COUNT = 40
_MAX_GENERATED_JPEG_BYTES = 8 * 1024 * 1024
_MAX_WIDTH = 1280
_MAX_HEIGHT = 720
_MAX_PIXELS = _MAX_WIDTH * _MAX_HEIGHT
_DEFAULT_TIMEOUT_SECONDS = 20


class LocalFrameError(ValueError):
    """Base error for bounded local-frame generation."""


class LocalFrameSourceFailure(ExternalSourceFailure, LocalFrameError):
    """Local FFmpeg or the cataloged media source could not be trusted."""


class LocalFrameUnavailable(ExternalPreviewUnavailable, LocalFrameError):
    """A requested generated frame was unavailable or unusable."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _media_signature(
    context: AnalyzerContext,
    runtime_seconds: float,
    *,
    device_id: int | None,
    inode_id: int | None,
) -> str:
    payload = {
        "version": LOCAL_FRAME_SOURCE_VERSION,
        "file_id": int(context.media.file_id),
        "path": str(Path(context.media.path)),
        "size_bytes": int(context.media.size_bytes),
        "modified_at": context.media.modified_at,
        "sha256": context.media.sha256 or "",
        "runtime_seconds": round(float(runtime_seconds), 6),
        "device_id": device_id,
        "inode_id": inode_id,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _stat_identity(path: Path) -> tuple[os.stat_result, int | None, int | None] | None:
    try:
        result = path.stat()
    except OSError:
        return None
    if not stat_module.S_ISREG(result.st_mode):
        return None
    device_id = int(getattr(result, "st_dev", 0) or 0) or None
    inode_id = int(getattr(result, "st_ino", 0) or 0) or None
    return result, device_id, inode_id


def _stat_matches(
    path: Path,
    context: AnalyzerContext,
    *,
    device_id: int | None = None,
    inode_id: int | None = None,
) -> bool:
    identity = _stat_identity(path)
    if identity is None:
        return False
    result, current_device, current_inode = identity
    if int(result.st_size) != int(context.media.size_bytes):
        return False
    expected_mtime = context.media.modified_at
    if expected_mtime is not None and float(result.st_mtime) != float(expected_mtime):
        return False
    if device_id is not None and current_device != device_id:
        return False
    if inode_id is not None and current_inode != inode_id:
        return False
    return True


def _ffmpeg_is_available(executable: str) -> bool:
    raw = str(executable or "").strip()
    if not raw:
        return False
    candidate = Path(raw)
    if candidate.is_absolute() or candidate.parent != Path("."):
        return candidate.is_file() and (
            os.name == "nt" or os.access(candidate, os.X_OK)
        )
    return shutil.which(raw) is not None


def _validate_generated_jpeg(payload: bytes) -> bytes:
    if (
        len(payload) < 4
        or len(payload) > _MAX_GENERATED_JPEG_BYTES
        or not payload.startswith(b"\xff\xd8")
        or not payload.endswith(b"\xff\xd9")
    ):
        raise LocalFrameUnavailable(
            "FFmpeg did not return a bounded JPEG preview frame."
        )
    try:
        with Image.open(BytesIO(payload)) as image:
            if image.format != "JPEG":
                raise LocalFrameUnavailable(
                    "Generated preview did not decode as JPEG."
                )
            width, height = image.size
            if (
                width <= 0
                or height <= 0
                or width > _MAX_WIDTH
                or height > _MAX_HEIGHT
                or width * height > _MAX_PIXELS
            ):
                raise LocalFrameUnavailable(
                    "Generated preview dimensions exceeded the Normal frame limit."
                )
            image.load()
    except LocalFrameUnavailable:
        raise
    except (
        Image.DecompressionBombError,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as exc:
        raise LocalFrameUnavailable(
            "Generated preview could not be decoded safely."
        ) from exc
    return payload


class LocalFfmpegFrameSource:
    """Read-only synthetic preview source backed by the cataloged media file."""

    source_key = LOCAL_FRAME_SOURCE_KEY
    version = LOCAL_FRAME_SOURCE_VERSION

    def __init__(
        self,
        context: AnalyzerContext,
        runtime_seconds: float | None,
        *,
        executable: str | None = None,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.context = context
        try:
            runtime = float(runtime_seconds or 0)
        except (TypeError, ValueError):
            runtime = 0.0
        self.runtime_seconds = runtime
        self.executable = str(executable or ffmpeg_executable())
        self.timeout_seconds = max(1, min(int(timeout_seconds), 60))
        path_identity = _stat_identity(Path(context.media.path))
        if path_identity is None:
            self._device_id = None
            self._inode_id = None
        else:
            _, self._device_id, self._inode_id = path_identity
        self._signature = (
            _media_signature(
                context,
                runtime,
                device_id=self._device_id,
                inode_id=self._inode_id,
            )
            if runtime > 0
            else ""
        )

    def status(self) -> ExternalSourceStatus:
        path = Path(self.context.media.path)
        if self.runtime_seconds <= 0:
            return ExternalSourceStatus(
                source_key=self.source_key,
                available=False,
                detail="A positive cataloged runtime is required for generated frames.",
            )
        if not _stat_matches(
            path,
            self.context,
            device_id=self._device_id,
            inode_id=self._inode_id,
        ):
            return ExternalSourceStatus(
                source_key=self.source_key,
                available=False,
                detail="The local media file no longer matches the verified snapshot.",
            )
        if not _ffmpeg_is_available(self.executable):
            return ExternalSourceStatus(
                source_key=self.source_key,
                available=False,
                detail="FFmpeg is not available for generated preview frames.",
            )
        return ExternalSourceStatus(
            source_key=self.source_key,
            available=True,
            capabilities=frozenset({ExternalCapability.PREVIEW_FRAMES}),
            detail="Local FFmpeg frame generation is available for Normal OCR.",
        )

    def resolve_media(self, context: AnalyzerContext) -> ExternalMediaRef | None:
        if context.media.file_id != self.context.media.file_id:
            return None
        if not self.status().available:
            return None
        return ExternalMediaRef(
            source_key=self.source_key,
            item_id=f"file:{int(context.media.file_id)}",
            path=str(context.media.path),
            source_signature=self._signature,
        )

    def preview_frames(self, media: ExternalMediaRef):
        if media.source_signature != self._signature:
            raise LocalFrameSourceFailure(
                "The generated-frame media snapshot changed before enumeration."
            )
        duration_ms = max(1, int(round(self.runtime_seconds * 1000.0)))
        frames: list[PreviewFrameRef] = []
        for index in range(_LOCAL_FRAME_COUNT):
            fraction = (index + 1) / (_LOCAL_FRAME_COUNT + 1)
            timestamp_ms = max(
                0,
                min(duration_ms - 1, int(round(duration_ms * fraction))),
            )
            frames.append(
                PreviewFrameRef(
                    source_key=self.source_key,
                    item_id=media.item_id,
                    timestamp_ms=timestamp_ms,
                    asset_ref=(
                        f"ffmpeg:{LOCAL_FRAME_SOURCE_VERSION}:"
                        f"{timestamp_ms}:{_MAX_WIDTH}x{_MAX_HEIGHT}"
                    ),
                    source_signature=self._signature,
                    width=_MAX_WIDTH,
                    height=_MAX_HEIGHT,
                )
            )
        return tuple(frames)

    def read_preview(self, frame: PreviewFrameRef) -> bytes:
        if frame.source_key != self.source_key:
            raise LocalFrameSourceFailure(
                "Generated preview frame belongs to a different source."
            )
        if frame.source_signature != self._signature:
            raise LocalFrameUnavailable(
                "Generated preview reference is stale for the current media snapshot."
            )
        expected_item_id = f"file:{int(self.context.media.file_id)}"
        if frame.item_id != expected_item_id:
            raise LocalFrameSourceFailure(
                "Generated preview frame belongs to a different media item."
            )
        duration_ms = max(1, int(round(self.runtime_seconds * 1000.0)))
        if int(frame.timestamp_ms) < 0 or int(frame.timestamp_ms) >= duration_ms:
            raise LocalFrameUnavailable(
                "Generated preview timestamp is outside the verified media runtime."
            )
        expected_asset_ref = (
            f"ffmpeg:{LOCAL_FRAME_SOURCE_VERSION}:"
            f"{int(frame.timestamp_ms)}:{_MAX_WIDTH}x{_MAX_HEIGHT}"
        )
        if frame.asset_ref != expected_asset_ref:
            raise LocalFrameSourceFailure(
                "Generated preview reference does not match its extraction policy."
            )
        path = Path(self.context.media.path)
        if not _stat_matches(
            path,
            self.context,
            device_id=self._device_id,
            inode_id=self._inode_id,
        ):
            raise LocalFrameSourceFailure(
                "The local media file changed before FFmpeg frame extraction."
            )

        timestamp_seconds = max(0.0, float(frame.timestamp_ms) / 1000.0)
        command = [
            self.executable,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp_seconds:.3f}",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-vf",
            (
                "scale=w='min(1280,iw)':h='min(720,ih)':"
                "force_original_aspect_ratio=decrease:force_divisible_by=2"
            ),
            "-q:v",
            "4",
            "-fs",
            str(_MAX_GENERATED_JPEG_BYTES),
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        ]
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
                **_quiet_subprocess_options(),
            )
        except FileNotFoundError as exc:
            raise LocalFrameSourceFailure(
                "FFmpeg is unavailable for generated Normal preview frames."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise LocalFrameUnavailable(
                "FFmpeg timed out while extracting a Normal preview frame."
            ) from exc
        except OSError as exc:
            raise LocalFrameSourceFailure(
                "InfoMancer could not start FFmpeg for generated preview frames."
            ) from exc

        if not _stat_matches(
            path,
            self.context,
            device_id=self._device_id,
            inode_id=self._inode_id,
        ):
            raise LocalFrameSourceFailure(
                "The local media file changed during FFmpeg frame extraction."
            )
        if result.returncode:
            raise LocalFrameUnavailable(
                "FFmpeg could not extract the requested preview frame."
            )

        return _validate_generated_jpeg(bytes(result.stdout or b""))

    def subtitles(self, media):
        return ()

    def read_subtitle(self, subtitle):
        raise LocalFrameUnavailable(
            "Local FFmpeg subtitle extraction is not part of Normal OCR."
        )

    def media_metadata(self, media):
        return {}

    def fingerprints(self, media):
        return ()

    def known_identity(self, media):
        return None

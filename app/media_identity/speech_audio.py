from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat as stat_module
import subprocess
import tempfile
from typing import Any, Iterable, Mapping, Sequence
import wave

from ..media_info import _quiet_subprocess_options, ffmpeg_executable
from .models import MediaIdentityFile
from .speech import (
    MAX_NORMAL_SPEECH_TOTAL_MS,
    SpeechAudioIdentity,
    SpeechIdentityError,
    SpeechWindow,
)


SPEECH_AUDIO_POLICY_VERSION = 1
SPEECH_AUDIO_FORMAT_KEY = "wav-pcm-s16le"
SPEECH_AUDIO_SAMPLE_RATE_HZ = 16_000
SPEECH_AUDIO_CHANNELS = 1
SPEECH_AUDIO_SAMPLE_WIDTH_BYTES = 2
MAX_SPEECH_AUDIO_BYTES = 3 * 1024 * 1024
MAX_NORMAL_SPEECH_AUDIO_BYTES = 9 * 1024 * 1024
_MAX_WAV_CONTAINER_OVERHEAD_BYTES = 64 * 1024
DEFAULT_SPEECH_EXTRACTION_TIMEOUT_SECONDS = 60
_MAX_EXTRACTION_TIMEOUT_SECONDS = 120

_LANGUAGE_ALIASES = {
    "en": "eng",
    "eng": "eng",
    "es": "spa",
    "spa": "spa",
    "fr": "fra",
    "fra": "fra",
    "fre": "fra",
    "de": "deu",
    "deu": "deu",
    "ger": "deu",
    "it": "ita",
    "ita": "ita",
    "pt": "por",
    "por": "por",
    "ja": "jpn",
    "jpn": "jpn",
    "ko": "kor",
    "kor": "kor",
    "zh": "zho",
    "zho": "zho",
    "chi": "zho",
    "ru": "rus",
    "rus": "rus",
}


class SpeechAudioError(RuntimeError):
    """Base failure for bounded local speech-audio preparation."""


class SpeechAudioUnavailable(SpeechAudioError):
    """Local audio extraction cannot safely produce a usable artifact."""


class SpeechAudioStaleError(SpeechAudioError):
    """Media, FFmpeg, or a prepared audio artifact changed unexpectedly."""


@dataclass(frozen=True)
class SpeechAudioStream:
    """Normalized catalog identity for one audio stream."""

    index: int
    language: str = "und"
    title: str = ""
    channels: int | None = None
    sample_rate_hz: int | None = None
    default: bool = False
    commentary: bool = False
    visual_impaired: bool = False

    def cache_identity(self) -> Mapping[str, Any]:
        return {
            "index": self.index,
            "language": self.language,
            "title": self.title,
            "channels": self.channels,
            "sample_rate_hz": self.sample_rate_hz,
            "default": self.default,
            "commentary": self.commentary,
            "visual_impaired": self.visual_impaired,
        }


def _strict_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _optional_positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _language_key(value: str) -> str:
    normalized = value.strip().casefold()
    return _LANGUAGE_ALIASES.get(normalized, normalized)


def _flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return False


def _stream_from_mapping(raw: Mapping[str, Any]) -> SpeechAudioStream | None:
    raw_type = raw.get("stream_type", raw.get("type", ""))
    if not isinstance(raw_type, str):
        return None
    stream_type = raw_type.strip().casefold()
    if stream_type != "audio":
        return None

    index = _strict_nonnegative_int(
        raw.get("stream_index", raw.get("index"))
    )
    if index is None:
        return None

    raw_language = raw.get("language")
    language = (
        raw_language.strip().casefold()
        if isinstance(raw_language, str) and raw_language.strip()
        else "und"
    )
    raw_title = raw.get("title")
    title = raw_title.strip() if isinstance(raw_title, str) else ""
    return SpeechAudioStream(
        index=index,
        language=language,
        title=title,
        channels=_optional_positive_int(raw.get("channels")),
        sample_rate_hz=_optional_positive_int(
            raw.get("sample_rate", raw.get("sample_rate_hz"))
        ),
        default=_flag(raw.get("default_flag", raw.get("default"))),
        commentary=_flag(raw.get("commentary")),
        visual_impaired=_flag(raw.get("visual_impaired")),
    )


def select_speech_audio_stream(
    streams: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    *,
    preferred_language: str = "",
) -> SpeechAudioStream:
    """Choose one stable primary dialogue stream from normalized catalog rows."""
    if not isinstance(preferred_language, str):
        raise SpeechAudioUnavailable(
            "Preferred speech language must be text."
        )
    preferred = _language_key(preferred_language)
    candidates: list[SpeechAudioStream] = []
    for raw in streams:
        if not isinstance(raw, Mapping):
            try:
                raw = dict(raw)
            except (TypeError, ValueError):
                continue
        stream = _stream_from_mapping(raw)
        if stream is not None:
            candidates.append(stream)

    if not candidates:
        raise SpeechAudioUnavailable(
            "No usable cataloged audio stream is available for speech analysis."
        )

    # Prefer primary-program audio over commentary or audio-description tracks.
    primary = [
        stream
        for stream in candidates
        if not stream.commentary and not stream.visual_impaired
    ]
    pool = primary or candidates

    if preferred:
        language_matches = [
            stream
            for stream in pool
            if _language_key(stream.language) == preferred
        ]
        if language_matches:
            pool = language_matches

    defaults = [stream for stream in pool if stream.default]
    if defaults:
        pool = defaults

    return min(pool, key=lambda stream: stream.index)


def validate_normal_speech_window_plan(
    windows: Iterable[SpeechWindow],
) -> tuple[SpeechWindow, ...]:
    """Validate the complete bounded Normal transcription plan before extraction."""
    planned = tuple(windows)
    seen: set[tuple[int, int]] = set()
    total_ms = 0

    for window in planned:
        if not isinstance(window, SpeechWindow):
            raise SpeechIdentityError(
                "Normal speech plans require SpeechWindow values."
            )
        identity = (window.start_ms, window.end_ms)
        if identity in seen:
            raise SpeechIdentityError(
                "Normal speech plans cannot repeat the same audio window."
            )
        seen.add(identity)
        total_ms += window.duration_ms

    if total_ms > MAX_NORMAL_SPEECH_TOTAL_MS:
        raise SpeechIdentityError(
            "Normal speech plans cannot exceed 240 seconds in aggregate."
        )
    return planned


def validate_normal_speech_audio_budget(
    records: Iterable[tuple[SpeechWindow, SpeechAudioIdentity]],
) -> tuple[tuple[SpeechWindow, SpeechAudioIdentity], ...]:
    """Validate retained provenance records without keeping temp WAV files alive."""
    prepared = tuple(records)
    validate_normal_speech_window_plan(window for window, _ in prepared)

    total_bytes = 0
    for _, identity in prepared:
        if not isinstance(identity, SpeechAudioIdentity):
            raise SpeechAudioUnavailable(
                "Normal speech audio budgets require SpeechAudioIdentity values."
            )
        if (
            identity.size_bytes > MAX_SPEECH_AUDIO_BYTES
            or identity.format_key != SPEECH_AUDIO_FORMAT_KEY
            or identity.sample_rate_hz != SPEECH_AUDIO_SAMPLE_RATE_HZ
            or identity.channels != SPEECH_AUDIO_CHANNELS
        ):
            raise SpeechAudioUnavailable(
                "Prepared Normal speech audio does not match the bounded canonical format."
            )
        total_bytes += identity.size_bytes

    if total_bytes > MAX_NORMAL_SPEECH_AUDIO_BYTES:
        raise SpeechAudioUnavailable(
            "Prepared Normal speech audio exceeded the aggregate byte limit."
        )
    return prepared


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _stat_identity(
    path: Path,
) -> tuple[os.stat_result, int | None, int | None] | None:
    try:
        result = path.stat()
    except OSError:
        return None
    if not stat_module.S_ISREG(result.st_mode):
        return None
    device_id = int(getattr(result, "st_dev", 0) or 0) or None
    inode_id = int(getattr(result, "st_ino", 0) or 0) or None
    return result, device_id, inode_id


def _media_matches(
    path: Path,
    media: MediaIdentityFile,
    *,
    device_id: int | None,
    inode_id: int | None,
) -> bool:
    identity = _stat_identity(path)
    if identity is None:
        return False
    result, current_device, current_inode = identity
    if int(result.st_size) != int(media.size_bytes):
        return False
    if (
        media.modified_at is not None
        and float(result.st_mtime) != float(media.modified_at)
    ):
        return False
    if device_id is not None and current_device != device_id:
        return False
    if inode_id is not None and current_inode != inode_id:
        return False
    return True


def _ffmpeg_identity(executable: str) -> dict[str, Any] | None:
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
        result = candidate.stat()
    except OSError:
        return None
    if not stat_module.S_ISREG(result.st_mode):
        return None
    if (
        os.name != "nt"
        and not result.st_mode
        & (stat_module.S_IXUSR | stat_module.S_IXGRP | stat_module.S_IXOTH)
    ):
        return None

    return {
        "path": str(candidate),
        "size_bytes": int(result.st_size),
        "modified_at_ns": int(getattr(result, "st_mtime_ns", 0) or 0),
        "device_id": int(getattr(result, "st_dev", 0) or 0) or None,
        "inode_id": int(getattr(result, "st_ino", 0) or 0) or None,
    }


def _source_signature(
    media: MediaIdentityFile,
    stream: SpeechAudioStream,
    window: SpeechWindow,
    *,
    device_id: int | None,
    inode_id: int | None,
    ffmpeg_identity: Mapping[str, Any],
) -> str:
    payload = {
        "policy_version": SPEECH_AUDIO_POLICY_VERSION,
        "media": {
            "file_id": int(media.file_id),
            "path": str(Path(media.path)),
            "size_bytes": int(media.size_bytes),
            "modified_at": media.modified_at,
            "sha256": media.sha256 or "",
            "device_id": device_id,
            "inode_id": inode_id,
        },
        "stream": dict(stream.cache_identity()),
        "window": {
            "start_ms": window.start_ms,
            "end_ms": window.end_ms,
        },
        "format": {
            "format_key": SPEECH_AUDIO_FORMAT_KEY,
            "sample_rate_hz": SPEECH_AUDIO_SAMPLE_RATE_HZ,
            "channels": SPEECH_AUDIO_CHANNELS,
            "sample_width_bytes": SPEECH_AUDIO_SAMPLE_WIDTH_BYTES,
        },
        "ffmpeg": dict(ffmpeg_identity),
    }
    return hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()


def _stat_signature(
    value: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    return (
        stat_module.S_IFMT(value.st_mode),
        int(value.st_size),
        int(getattr(value, "st_mtime_ns", int(value.st_mtime * 1_000_000_000))),
        int(getattr(value, "st_ctime_ns", int(value.st_ctime * 1_000_000_000))),
        int(getattr(value, "st_dev", 0)),
        int(getattr(value, "st_ino", 0)),
    )


def _read_bounded_regular_file(path: Path) -> bytes:
    try:
        initial = path.lstat()
    except OSError as exc:
        raise SpeechAudioStaleError(
            "The prepared speech audio file is no longer available."
        ) from exc
    if (
        stat_module.S_ISLNK(initial.st_mode)
        or not stat_module.S_ISREG(initial.st_mode)
        or initial.st_size <= 0
        or initial.st_size > MAX_SPEECH_AUDIO_BYTES
    ):
        raise SpeechAudioStaleError(
            "The prepared speech audio file is not a bounded regular file."
        )

    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if _stat_signature(initial) != _stat_signature(opened):
            raise SpeechAudioStaleError(
                "The prepared speech audio file changed before validation."
            )

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, MAX_SPEECH_AUDIO_BYTES + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_SPEECH_AUDIO_BYTES:
                raise SpeechAudioStaleError(
                    "The prepared speech audio file exceeded its byte limit."
                )
            chunks.append(chunk)

        after = os.fstat(descriptor)
        if _stat_signature(opened) != _stat_signature(after):
            raise SpeechAudioStaleError(
                "The prepared speech audio file changed during validation."
            )
    except SpeechAudioStaleError:
        raise
    except OSError as exc:
        raise SpeechAudioStaleError(
            "InfoMancer could not validate the prepared speech audio file."
        ) from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass

    try:
        final = path.lstat()
    except OSError as exc:
        raise SpeechAudioStaleError(
            "The prepared speech audio file disappeared during validation."
        ) from exc
    if (
        stat_module.S_ISLNK(final.st_mode)
        or _stat_signature(after) != _stat_signature(final)
    ):
        raise SpeechAudioStaleError(
            "The prepared speech audio path changed during validation."
        )
    return b"".join(chunks)


def _validate_wav_payload(payload: bytes, window: SpeechWindow) -> bytes:
    if not payload or len(payload) > MAX_SPEECH_AUDIO_BYTES:
        raise SpeechAudioUnavailable(
            "FFmpeg did not produce a bounded speech audio artifact."
        )
    try:
        with wave.open(BytesIO(payload), "rb") as reader:
            channels = int(reader.getnchannels())
            sample_width = int(reader.getsampwidth())
            sample_rate = int(reader.getframerate())
            frame_count = int(reader.getnframes())
            compression = str(reader.getcomptype() or "")
            frame_bytes = reader.readframes(frame_count)
    except (EOFError, OSError, wave.Error) as exc:
        raise SpeechAudioUnavailable(
            "FFmpeg speech audio output is not a valid PCM WAV file."
        ) from exc

    expected_frame_bytes = frame_count * channels * sample_width
    if len(frame_bytes) != expected_frame_bytes:
        raise SpeechAudioUnavailable(
            "FFmpeg speech audio output contains truncated PCM frame data."
        )
    if len(payload) > expected_frame_bytes + _MAX_WAV_CONTAINER_OVERHEAD_BYTES:
        raise SpeechAudioUnavailable(
            "FFmpeg speech audio output contains excessive container overhead."
        )

    if (
        channels != SPEECH_AUDIO_CHANNELS
        or sample_width != SPEECH_AUDIO_SAMPLE_WIDTH_BYTES
        or sample_rate != SPEECH_AUDIO_SAMPLE_RATE_HZ
        or compression != "NONE"
        or frame_count <= 0
    ):
        raise SpeechAudioUnavailable(
            "FFmpeg speech audio output does not match the canonical PCM format."
        )

    maximum_frames = (
        window.duration_ms * SPEECH_AUDIO_SAMPLE_RATE_HZ // 1000
    ) + 1
    if frame_count > maximum_frames:
        raise SpeechAudioUnavailable(
            "FFmpeg speech audio output exceeded the requested window duration."
        )
    return payload


class ExtractedSpeechAudio:
    """Owned transient audio artifact that can be revalidated before transcription."""

    def __init__(
        self,
        *,
        temporary_directory: str,
        path: Path,
        identity: SpeechAudioIdentity,
        window: SpeechWindow,
        stream: SpeechAudioStream,
    ) -> None:
        self._temporary_directory = temporary_directory
        self.path = Path(path)
        self.identity = identity
        self.window = window
        self.stream = stream
        self._closed = False

    def validated_path(
        self,
        expected_identity: SpeechAudioIdentity | None = None,
    ) -> str:
        if self._closed:
            raise SpeechAudioStaleError(
                "The prepared speech audio artifact has already been released."
            )
        if (
            expected_identity is not None
            and expected_identity != self.identity
        ):
            raise SpeechAudioStaleError(
                "The prepared speech audio identity does not match the request."
            )
        payload = _read_bounded_regular_file(self.path)
        _validate_wav_payload(payload, self.window)
        if (
            len(payload) != self.identity.size_bytes
            or hashlib.sha256(payload).hexdigest() != self.identity.sha256
        ):
            raise SpeechAudioStaleError(
                "The prepared speech audio bytes no longer match their identity."
            )
        return str(self.path)

    def cleanup(self) -> None:
        if self._closed:
            return
        self._closed = True
        shutil.rmtree(self._temporary_directory, ignore_errors=True)

    def __enter__(self) -> "ExtractedSpeechAudio":
        self.validated_path()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.cleanup()

    def __del__(self) -> None:
        try:
            self.cleanup()
        except Exception:
            pass


class LocalFfmpegSpeechAudioExtractor:
    """Prepare one bounded, canonical local audio window for speech analysis."""

    def __init__(
        self,
        media: MediaIdentityFile,
        streams: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
        *,
        preferred_language: str = "",
        executable: str | None = None,
        timeout_seconds: int = DEFAULT_SPEECH_EXTRACTION_TIMEOUT_SECONDS,
    ) -> None:
        if not isinstance(media, MediaIdentityFile):
            raise SpeechAudioUnavailable(
                "Speech audio extraction requires a media identity snapshot."
            )
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or timeout_seconds <= 0
            or timeout_seconds > _MAX_EXTRACTION_TIMEOUT_SECONDS
        ):
            raise SpeechAudioUnavailable(
                "Speech audio extraction timeout is outside the supported range."
            )

        self.media = media
        self.stream = select_speech_audio_stream(
            streams,
            preferred_language=preferred_language,
        )
        self.timeout_seconds = timeout_seconds

        requested_executable = str(executable or ffmpeg_executable())
        self._ffmpeg_identity = _ffmpeg_identity(requested_executable)
        self.executable = (
            str(self._ffmpeg_identity["path"])
            if self._ffmpeg_identity is not None
            else requested_executable
        )

        path_identity = _stat_identity(Path(media.path))
        if path_identity is None:
            raise SpeechAudioUnavailable(
                "The cataloged media file is unavailable for speech extraction."
            )
        _, self._device_id, self._inode_id = path_identity

    def _require_current_inputs(self) -> Mapping[str, Any]:
        path = Path(self.media.path)
        if not _media_matches(
            path,
            self.media,
            device_id=self._device_id,
            inode_id=self._inode_id,
        ):
            raise SpeechAudioStaleError(
                "The local media file no longer matches the verified snapshot."
            )
        if self._ffmpeg_identity is None:
            raise SpeechAudioUnavailable(
                "FFmpeg is unavailable for speech audio extraction."
            )
        if _ffmpeg_identity(self.executable) != self._ffmpeg_identity:
            raise SpeechAudioStaleError(
                "FFmpeg changed after speech extraction was prepared."
            )
        return self._ffmpeg_identity

    def source_signature(self, window: SpeechWindow) -> str:
        if not isinstance(window, SpeechWindow):
            raise SpeechAudioUnavailable(
                "Speech audio extraction requires a bounded speech window."
            )
        ffmpeg_identity = self._require_current_inputs()
        return _source_signature(
            self.media,
            self.stream,
            window,
            device_id=self._device_id,
            inode_id=self._inode_id,
            ffmpeg_identity=ffmpeg_identity,
        )

    def extract(self, window: SpeechWindow) -> ExtractedSpeechAudio:
        if not isinstance(window, SpeechWindow):
            raise SpeechAudioUnavailable(
                "Speech audio extraction requires a bounded speech window."
            )
        ffmpeg_identity = self._require_current_inputs()
        source_signature = _source_signature(
            self.media,
            self.stream,
            window,
            device_id=self._device_id,
            inode_id=self._inode_id,
            ffmpeg_identity=ffmpeg_identity,
        )

        temporary_directory = tempfile.mkdtemp(prefix="infomancer-speech-")
        output_path = Path(temporary_directory) / "audio.wav"
        path = Path(self.media.path)
        start_seconds = window.start_ms / 1000.0
        duration_seconds = window.duration_ms / 1000.0
        command = [
            self.executable,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-xerror",
            "-ss",
            f"{start_seconds:.3f}",
            "-i",
            str(path),
            "-map",
            f"0:{self.stream.index}",
            "-t",
            f"{duration_seconds:.3f}",
            "-vn",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-ac",
            str(SPEECH_AUDIO_CHANNELS),
            "-ar",
            str(SPEECH_AUDIO_SAMPLE_RATE_HZ),
            "-c:a",
            "pcm_s16le",
            "-fs",
            str(MAX_SPEECH_AUDIO_BYTES),
            "-f",
            "wav",
            "-n",
            str(output_path),
        ]

        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
                **_quiet_subprocess_options(),
            )
        except FileNotFoundError as exc:
            shutil.rmtree(temporary_directory, ignore_errors=True)
            raise SpeechAudioUnavailable(
                "FFmpeg is unavailable for speech audio extraction."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            shutil.rmtree(temporary_directory, ignore_errors=True)
            raise SpeechAudioUnavailable(
                "FFmpeg timed out while preparing speech audio."
            ) from exc
        except OSError as exc:
            shutil.rmtree(temporary_directory, ignore_errors=True)
            raise SpeechAudioUnavailable(
                "InfoMancer could not start FFmpeg for speech audio extraction."
            ) from exc

        try:
            self._require_current_inputs()
            if result.returncode:
                raise SpeechAudioUnavailable(
                    "FFmpeg could not extract the requested speech audio window."
                )

            try:
                payload = _read_bounded_regular_file(output_path)
            except SpeechAudioStaleError as exc:
                raise SpeechAudioUnavailable(
                    "FFmpeg did not produce a bounded regular speech audio file."
                ) from exc
            _validate_wav_payload(payload, window)
            identity = SpeechAudioIdentity(
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
                format_key=SPEECH_AUDIO_FORMAT_KEY,
                sample_rate_hz=SPEECH_AUDIO_SAMPLE_RATE_HZ,
                channels=SPEECH_AUDIO_CHANNELS,
                source_signature=source_signature,
                details={
                    "policy_version": SPEECH_AUDIO_POLICY_VERSION,
                    "stream": dict(self.stream.cache_identity()),
                    "window": {
                        "start_ms": window.start_ms,
                        "end_ms": window.end_ms,
                    },
                    "ffmpeg": dict(ffmpeg_identity),
                },
            )
            return ExtractedSpeechAudio(
                temporary_directory=temporary_directory,
                path=output_path,
                identity=identity,
                window=window,
                stream=self.stream,
            )
        except Exception:
            shutil.rmtree(temporary_directory, ignore_errors=True)
            raise

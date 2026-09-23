from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat as stat_module
import subprocess
import threading
import time
from typing import Mapping

from .managed_speech import (
    ManagedSpeechComponentError,
    ManagedWhisperCppRuntime,
    ManagedWhisperModel,
    WHISPERCPP_VERSION,
)
from .media_identity.speech import (
    SpeechBinaryIdentity,
    SpeechIdentityError,
    SpeechModelIdentity,
    SpeechRequest,
    SpeechTranscript,
)


WHISPERCPP_ENGINE_CACHE_VERSION = 2
DEFAULT_WHISPERCPP_THREADS = 4
DEFAULT_WHISPERCPP_TIMEOUT_SECONDS = 180
MAX_WHISPERCPP_TIMEOUT_SECONDS = 300
MAX_WHISPERCPP_THREADS = 32
MAX_WHISPERCPP_STDOUT_BYTES = 1024 * 1024
MAX_WHISPERCPP_STDERR_BYTES = 256 * 1024
MAX_WHISPERCPP_AUDIO_BYTES = 3 * 1024 * 1024

_LANGUAGE_ALIASES = {
    "english": "en",
    "japanese": "ja",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "portuguese": "pt",
    "chinese": "zh",
    "korean": "ko",
    "mandarin": "zh",
    "cantonese": "yue",
}

# OpenAI Whisper language IDs. Most are ISO 639-1; Hawaiian and Cantonese
# use Whisper's three-letter IDs, while Javanese uses Whisper's historical
# "jw" ID. ISO 639-2/T and bibliographic aliases are normalized below.
_WHISPER_LANGUAGE_CODES = frozenset({
    "en", "zh", "de", "es", "ru", "ko", "fr", "ja", "pt", "tr", "pl",
    "ca", "nl", "ar", "sv", "it", "id", "hi", "fi", "vi", "he", "uk",
    "el", "ms", "cs", "ro", "da", "hu", "ta", "no", "th", "ur", "hr",
    "bg", "lt", "la", "mi", "ml", "cy", "sk", "te", "fa", "lv", "bn",
    "sr", "az", "sl", "kn", "et", "mk", "br", "eu", "is", "hy", "ne",
    "mn", "bs", "kk", "sq", "sw", "gl", "mr", "pa", "si", "km", "sn",
    "yo", "so", "af", "oc", "ka", "be", "tg", "sd", "gu", "am", "yi",
    "lo", "uz", "fo", "ht", "ps", "tk", "nn", "mt", "sa", "lb", "my",
    "bo", "tl", "mg", "as", "tt", "haw", "ln", "ha", "ba", "jw", "su",
    "yue",
})

_ISO_LANGUAGE_ALIASES = {
    "afr": "af", "alb": "sq", "amh": "am", "ara": "ar", "arm": "hy",
    "asm": "as", "aze": "az", "bak": "ba", "baq": "eu", "bel": "be",
    "ben": "bn", "bod": "bo", "bos": "bs", "bre": "br", "bul": "bg",
    "bur": "my", "cat": "ca", "ces": "cs", "chi": "zh", "cym": "cy",
    "cze": "cs", "dan": "da", "deu": "de", "dut": "nl", "ell": "el",
    "eng": "en", "est": "et", "eus": "eu", "fao": "fo", "fas": "fa",
    "fin": "fi", "fra": "fr", "fre": "fr", "geo": "ka", "ger": "de",
    "glg": "gl", "gre": "el", "guj": "gu", "hat": "ht", "hau": "ha",
    "heb": "he", "hin": "hi", "hrv": "hr", "hun": "hu", "hye": "hy",
    "ice": "is", "ind": "id", "isl": "is", "ita": "it", "jav": "jw",
    "jpn": "ja", "jv": "jw", "kan": "kn", "kat": "ka", "kaz": "kk",
    "khm": "km", "kor": "ko", "lao": "lo", "lat": "la", "lav": "lv",
    "lin": "ln", "lit": "lt", "ltz": "lb", "mac": "mk", "mal": "ml",
    "mao": "mi", "mar": "mr", "may": "ms", "mkd": "mk", "mlg": "mg",
    "mlt": "mt", "mon": "mn", "mri": "mi", "msa": "ms", "mya": "my",
    "nep": "ne", "nld": "nl", "nno": "nn", "nor": "no", "oci": "oc",
    "pan": "pa", "per": "fa", "pol": "pl", "por": "pt", "pus": "ps",
    "ron": "ro", "rum": "ro", "rus": "ru", "san": "sa", "sin": "si",
    "slk": "sk", "slo": "sk", "slv": "sl", "sna": "sn", "snd": "sd",
    "som": "so", "spa": "es", "sqi": "sq", "srp": "sr", "sun": "su",
    "swa": "sw", "swe": "sv", "tam": "ta", "tat": "tt", "tel": "te",
    "tgk": "tg", "tgl": "tl", "tha": "th", "tib": "bo", "tuk": "tk",
    "tur": "tr", "ukr": "uk", "und": "auto", "urd": "ur", "uzb": "uz",
    "vie": "vi", "wel": "cy", "yid": "yi", "yor": "yo", "zho": "zh",
}


class WhisperCppSpeechError(RuntimeError):
    """Local whisper.cpp could not safely complete a speech request."""


class _WhisperOutputLimitError(RuntimeError):
    def __init__(self, stream_name: str) -> None:
        super().__init__(stream_name)
        self.stream_name = stream_name


def _run_bounded_process(
    command: list[str],
    *,
    cwd: str,
    env: Mapping[str, str],
    timeout_seconds: float,
    stdout_limit: int = MAX_WHISPERCPP_STDOUT_BYTES,
    stderr_limit: int = MAX_WHISPERCPP_STDERR_BYTES,
) -> subprocess.CompletedProcess[bytes]:
    """Run whisper-cli while bounding pipe memory before the process exits."""
    if stdout_limit <= 0 or stderr_limit <= 0:
        raise ValueError("Whisper output limits must be positive.")

    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **_quiet_subprocess_options(),
        )
    except OSError:
        raise

    if process.stdout is None or process.stderr is None:
        try:
            process.kill()
        except OSError:
            pass
        raise OSError("whisper.cpp pipes were not created.")

    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    overflow = threading.Event()
    overflow_name: list[str] = []
    overflow_lock = threading.Lock()

    def drain(stream, buffer: bytearray, limit: int, name: str) -> None:
        try:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    return
                remaining = limit - len(buffer)
                if remaining <= 0:
                    with overflow_lock:
                        if not overflow_name:
                            overflow_name.append(name)
                    overflow.set()
                    return
                if len(chunk) > remaining:
                    buffer.extend(chunk[:remaining])
                    with overflow_lock:
                        if not overflow_name:
                            overflow_name.append(name)
                    overflow.set()
                    return
                buffer.extend(chunk)
        finally:
            try:
                stream.close()
            except OSError:
                pass

    stdout_thread = threading.Thread(
        target=drain,
        args=(process.stdout, stdout_buffer, stdout_limit, "stdout"),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=drain,
        args=(process.stderr, stderr_buffer, stderr_limit, "stderr"),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    deadline = time.monotonic() + float(timeout_seconds)
    timed_out = False
    try:
        while process.poll() is None:
            if overflow.is_set():
                try:
                    process.kill()
                except OSError:
                    pass
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                try:
                    process.kill()
                except OSError:
                    pass
                break
            time.sleep(min(0.02, remaining))
        try:
            return_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            return_code = process.wait(timeout=5)
    finally:
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)

    if stdout_thread.is_alive() or stderr_thread.is_alive():
        raise WhisperCppSpeechError(
            "InfoMancer could not finish draining bounded whisper.cpp output."
        )
    if timed_out:
        raise subprocess.TimeoutExpired(command, timeout_seconds)
    if overflow.is_set():
        stream_name = overflow_name[0] if overflow_name else "output"
        raise _WhisperOutputLimitError(stream_name)

    return subprocess.CompletedProcess(
        args=command,
        returncode=return_code,
        stdout=bytes(stdout_buffer),
        stderr=bytes(stderr_buffer),
    )


def _stable_stat(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        stat_module.S_IFMT(value.st_mode),
        int(value.st_size),
        int(getattr(value, "st_mtime_ns", int(value.st_mtime * 1_000_000_000))),
        int(getattr(value, "st_dev", 0)),
        int(getattr(value, "st_ino", 0)),
    )


def _verify_audio_path(path: str, request: SpeechRequest) -> Path:
    if (
        request.audio.format_key != "wav-pcm-s16le"
        or request.audio.sample_rate_hz != 16_000
        or request.audio.channels != 1
    ):
        raise WhisperCppSpeechError(
            "whisper.cpp requires InfoMancer's canonical 16 kHz mono PCM WAV audio."
        )
    if (
        request.audio.size_bytes <= 0
        or request.audio.size_bytes > MAX_WHISPERCPP_AUDIO_BYTES
    ):
        raise WhisperCppSpeechError(
            "The prepared speech audio exceeds the whisper.cpp input ceiling."
        )

    candidate = Path(path)
    try:
        initial = candidate.lstat()
    except OSError as exc:
        raise WhisperCppSpeechError(
            "The prepared speech audio is no longer available."
        ) from exc
    if (
        stat_module.S_ISLNK(initial.st_mode)
        or not stat_module.S_ISREG(initial.st_mode)
        or initial.st_size != request.audio.size_bytes
    ):
        raise WhisperCppSpeechError(
            "The prepared speech audio no longer matches its request identity."
        )

    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(candidate, flags)
        opened = os.fstat(descriptor)
        if _stable_stat(initial) != _stable_stat(opened):
            raise WhisperCppSpeechError(
                "The prepared speech audio changed before transcription."
            )
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_WHISPERCPP_AUDIO_BYTES:
                raise WhisperCppSpeechError(
                    "The prepared speech audio exceeded its byte ceiling."
                )
            digest.update(chunk)
        after = os.fstat(descriptor)
        if _stable_stat(opened) != _stable_stat(after):
            raise WhisperCppSpeechError(
                "The prepared speech audio changed during verification."
            )
    except WhisperCppSpeechError:
        raise
    except OSError as exc:
        raise WhisperCppSpeechError(
            "InfoMancer could not verify the prepared speech audio."
        ) from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass

    if total != request.audio.size_bytes or digest.hexdigest() != request.audio.sha256:
        raise WhisperCppSpeechError(
            "The prepared speech audio bytes no longer match the request."
        )
    try:
        final = candidate.lstat()
    except OSError as exc:
        raise WhisperCppSpeechError(
            "The prepared speech audio disappeared before transcription."
        ) from exc
    if (
        stat_module.S_ISLNK(final.st_mode)
        or _stable_stat(after) != _stable_stat(final)
    ):
        raise WhisperCppSpeechError(
            "The prepared speech audio path changed before transcription."
        )
    return candidate.resolve()


def _normalize_language(value: str, multilingual: bool) -> str:
    normalized = value.strip().casefold()
    if not normalized:
        return "auto" if multilingual else "en"
    normalized = _LANGUAGE_ALIASES.get(normalized, normalized)
    normalized = _ISO_LANGUAGE_ALIASES.get(normalized, normalized)
    if normalized == "auto":
        if multilingual:
            return "auto"
        raise WhisperCppSpeechError(
            "The selected English-only Whisper model cannot transcribe another language."
        )
    if normalized not in _WHISPER_LANGUAGE_CODES:
        if multilingual:
            return "auto"
        raise WhisperCppSpeechError(
            "The selected English-only Whisper model cannot transcribe another language."
        )
    if not multilingual and normalized != "en":
        raise WhisperCppSpeechError(
            "The selected English-only Whisper model cannot transcribe another language."
        )
    return normalized


def _quiet_subprocess_options() -> dict[str, object]:
    if os.name != "nt":
        return {}
    options: dict[str, object] = {}
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if create_no_window:
        options["creationflags"] = create_no_window
    startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
    startf_use_showwindow = getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
    sw_hide = getattr(subprocess, "SW_HIDE", 0)
    if startupinfo_type is not None and startf_use_showwindow:
        startupinfo = startupinfo_type()
        startupinfo.dwFlags |= startf_use_showwindow
        startupinfo.wShowWindow = sw_hide
        options["startupinfo"] = startupinfo
    return options


class WhisperCppSpeechEngine:
    """CPU-only whisper.cpp SpeechEngine implementation for targeted windows."""

    key = "whisper.cpp"
    version = f"adapter-{WHISPERCPP_ENGINE_CACHE_VERSION}"

    def __init__(
        self,
        runtime: ManagedWhisperCppRuntime,
        model: ManagedWhisperModel,
        *,
        threads: int = DEFAULT_WHISPERCPP_THREADS,
        timeout_seconds: int = DEFAULT_WHISPERCPP_TIMEOUT_SECONDS,
    ) -> None:
        if (
            isinstance(threads, bool)
            or not isinstance(threads, int)
            or not 1 <= threads <= MAX_WHISPERCPP_THREADS
        ):
            raise SpeechIdentityError(
                f"whisper.cpp threads must be an integer from 1 to {MAX_WHISPERCPP_THREADS}."
            )
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= MAX_WHISPERCPP_TIMEOUT_SECONDS
        ):
            raise SpeechIdentityError(
                f"whisper.cpp timeout must be an integer from 1 to {MAX_WHISPERCPP_TIMEOUT_SECONDS} seconds."
            )
        self.runtime = runtime
        self.model = model
        self.threads = threads
        self.timeout_seconds = timeout_seconds

    def available(self) -> bool:
        try:
            self.runtime.resolve()
            self.model.resolve()
            return True
        except (ManagedSpeechComponentError, OSError):
            return False

    def binary_identity(self) -> SpeechBinaryIdentity:
        return self.runtime.binary_identity()

    def cache_identity(self) -> Mapping[str, object]:
        # The exact executable/runtime-tree identity is carried by
        # SpeechBinaryIdentity in the transcript cache key. Keep this method
        # limited to adapter settings so one cache-key build cannot combine
        # identities from two separate runtime snapshots.
        return {
            "adapter_version": WHISPERCPP_ENGINE_CACHE_VERSION,
            "cpu_only": True,
            "threads": self.threads,
            "stdout_parser": "plain-no-timestamps-v1",
        }

    def transcribe(
        self,
        audio_path: str,
        request: SpeechRequest,
    ) -> SpeechTranscript:
        if not isinstance(request, SpeechRequest):
            raise SpeechIdentityError(
                "whisper.cpp transcription requires a SpeechRequest."
            )
        if request.parameters:
            raise SpeechIdentityError(
                "whisper.cpp received unsupported speech request parameters."
            )

        executable, binary_identity = self.runtime.resolve()
        model_path, model_identity = self.model.resolve()
        if (
            request.model.cache_identity()
            != model_identity.cache_identity()
        ):
            raise WhisperCppSpeechError(
                "The resolved Whisper model does not match the speech request."
            )

        model_details = model_identity.details_payload()
        multilingual = bool(model_details.get("multilingual", False))
        if request.translate and not multilingual:
            raise WhisperCppSpeechError(
                "Whisper translation requires a multilingual model."
            )
        language = _normalize_language(request.language, multilingual)
        verified_audio = _verify_audio_path(audio_path, request)

        command = [
            str(executable),
            "--no-gpu",
            "--no-prints",
            "--no-timestamps",
            "--threads",
            str(self.threads),
            "--model",
            str(model_path),
            "--file",
            str(verified_audio),
            "--language",
            language,
        ]
        if request.translate:
            command.append("--translate")

        try:
            result = _run_bounded_process(
                command,
                cwd=str(executable.parent),
                env=self.runtime.launch_environment(executable),
                timeout_seconds=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise WhisperCppSpeechError(
                "Local whisper.cpp transcription timed out."
            ) from exc
        except _WhisperOutputLimitError as exc:
            if exc.stream_name == "stdout":
                raise WhisperCppSpeechError(
                    "whisper.cpp returned more transcript data than the bounded window permits."
                ) from exc
            raise WhisperCppSpeechError(
                "whisper.cpp returned excessive diagnostic output."
            ) from exc
        except WhisperCppSpeechError:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise WhisperCppSpeechError(
                "InfoMancer could not start local whisper.cpp transcription."
            ) from exc

        stdout = result.stdout or b""
        stderr = result.stderr or b""
        if result.returncode != 0:
            raise WhisperCppSpeechError(
                "Local whisper.cpp could not transcribe the prepared speech window."
            )

        text = stdout.decode("utf-8", errors="replace").strip()
        if not text and stderr.strip():
            raise WhisperCppSpeechError(
                "Local whisper.cpp reported an error without producing a transcript."
            )
        transcript_language = (
            "en"
            if request.translate or not multilingual
            else (language if language != "auto" else "")
        )
        return SpeechTranscript(
            text=text,
            language=transcript_language,
            confidence=None,
            details={
                "engine": self.key,
                "engine_version": self.version,
                "runtime_version": binary_identity.version,
                "binary_sha256": binary_identity.sha256,
                "runtime_tree_sha256": str(
                    binary_identity.details_payload().get(
                        "runtime_tree_sha256",
                        binary_identity.sha256,
                    )
                ),
                "model_key": model_identity.key,
                "model_sha256": model_identity.sha256,
                "threads": self.threads,
                "cpu_only": True,
                "language_requested": request.language,
                "language_argument": language,
                "translated": request.translate,
            },
        )

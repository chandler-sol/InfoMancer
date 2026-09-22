from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat as stat_module
import subprocess
import tarfile
import tempfile
import threading
from typing import BinaryIO, Iterable, Mapping
import urllib.error
import urllib.request
from urllib.parse import urlparse
import zipfile

from .media_identity.speech import (
    SpeechBinaryIdentity,
    SpeechModelIdentity,
)


_COMPONENT_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")

_ARCHIVE_SEGMENT = re.compile(r"^[^\\/:*?\"<>|]+$")


def _safe_relative_path(value: str, label: str) -> Path:
    if not isinstance(value, str):
        raise ManagedSpeechComponentError(f"{label} must be text.")
    normalized = value.strip().replace("\\", "/")
    candidate = PurePosixPath(normalized)
    if (
        not normalized
        or candidate.is_absolute()
        or any(
            part in {"", ".", ".."}
            or not _ARCHIVE_SEGMENT.fullmatch(part)
            for part in candidate.parts
        )
    ):
        raise ManagedSpeechComponentError(
            f"{label} contains unsupported path characters."
        )
    return Path(*candidate.parts)


class ManagedSpeechComponentError(RuntimeError):
    pass


def _safe_segment(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise ManagedSpeechComponentError(
            f"{label} must be text."
        )
    normalized = value.strip()
    if (
        not normalized
        or normalized in {".", ".."}
        or not _COMPONENT_SEGMENT.fullmatch(normalized)
    ):
        raise ManagedSpeechComponentError(
            f"{label} contains unsupported path characters."
        )
    return normalized


def _trusted_root(data_directory: Path) -> Path:
    try:
        return Path(data_directory).resolve()
    except OSError as exc:
        raise ManagedSpeechComponentError(
            "InfoMancer could not resolve its component data directory."
        ) from exc


def _path_is_redirect(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction is not None and is_junction())
    except OSError:
        return True


def _path_is_safe(root: Path, path: Path) -> bool:
    try:
        trusted = root.resolve()
        candidate = path.resolve(strict=False)
        candidate.relative_to(trusted)
    except (OSError, ValueError):
        return False

    current = path
    while True:
        try:
            if _path_is_redirect(current):
                return False
        except OSError:
            return False
        if current == root:
            break
        if current.parent == current:
            return False
        current = current.parent
    return True


def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int, int]:
    # Windows ctime semantics are not stable across path stat and descriptor
    # fstat calls. Content integrity is protected by exact size/hash checks.
    return (
        stat_module.S_IFMT(value.st_mode),
        int(value.st_size),
        int(getattr(value, "st_mtime_ns", int(value.st_mtime * 1_000_000_000))),
        int(getattr(value, "st_dev", 0)),
        int(getattr(value, "st_ino", 0)),
    )


def _same_file_snapshot(
    first: os.stat_result,
    second: os.stat_result,
) -> bool:
    return _stat_signature(first) == _stat_signature(second)


def _sha256_stream(stream: BinaryIO) -> str:
    hasher = hashlib.sha256()
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        hasher.update(chunk)
    return hasher.hexdigest()


def _verified_file(
    root: Path,
    path: Path,
    expected_sha256: str,
    expected_size: int,
    *,
    require_executable: bool,
) -> Path | None:
    if not _path_is_safe(root, path):
        return None

    try:
        initial = path.lstat()
    except OSError:
        return None
    if (
        stat_module.S_ISLNK(initial.st_mode)
        or not stat_module.S_ISREG(initial.st_mode)
        or initial.st_size != expected_size
    ):
        return None
    if (
        require_executable
        and os.name != "nt"
        and not initial.st_mode
        & (stat_module.S_IXUSR | stat_module.S_IXGRP | stat_module.S_IXOTH)
    ):
        return None

    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)

    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat_module.S_ISREG(opened.st_mode)
            or opened.st_size != expected_size
            or not _same_file_snapshot(initial, opened)
        ):
            return None

        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            digest = _sha256_stream(stream)

        after_hash = os.fstat(descriptor)
        if not _same_file_snapshot(opened, after_hash):
            return None
    except OSError:
        return None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass

    if digest != expected_sha256.strip().casefold():
        return None

    try:
        final = path.lstat()
    except OSError:
        return None
    if (
        stat_module.S_ISLNK(final.st_mode)
        or not stat_module.S_ISREG(final.st_mode)
        or final.st_size != expected_size
        or not _same_file_snapshot(after_hash, final)
        or not _path_is_safe(root, path)
    ):
        return None
    if (
        require_executable
        and os.name != "nt"
        and not final.st_mode
        & (stat_module.S_IXUSR | stat_module.S_IXGRP | stat_module.S_IXOTH)
    ):
        return None

    return path


class ManagedSpeechLayout:
    """Safe filesystem layout for separately managed speech binary/model assets."""

    def __init__(self, data_directory: Path) -> None:
        try:
            self.data_directory = Path(data_directory).resolve()
        except OSError as exc:
            raise ManagedSpeechComponentError(
                "InfoMancer could not resolve its speech component data directory."
            ) from exc

    @property
    def components_root(self) -> Path:
        return self.data_directory / "components"

    @property
    def binary_root(self) -> Path:
        return self.components_root / "whispercpp"

    @property
    def model_root(self) -> Path:
        return self.components_root / "whisper-models"

    def binary_directory(self, identity: SpeechBinaryIdentity) -> Path:
        key = _safe_segment(identity.key, "Speech binary key")
        version = _safe_segment(identity.version, "Speech binary version")
        digest = _safe_segment(
            identity.sha256,
            "Speech binary hash",
        )
        return self.binary_root / key / version / digest

    def model_directory(self, identity: SpeechModelIdentity) -> Path:
        key = _safe_segment(identity.key, "Speech model key")
        digest = _safe_segment(
            identity.sha256,
            "Speech model hash",
        )
        return self.model_root / key / digest

    def binary_path(
        self,
        identity: SpeechBinaryIdentity,
        filename: str,
    ) -> Path:
        return self.binary_directory(identity) / _safe_relative_path(
            filename,
            "Speech binary filename",
        )

    def model_path(
        self,
        identity: SpeechModelIdentity,
        filename: str,
    ) -> Path:
        return self.model_directory(identity) / _safe_relative_path(
            filename,
            "Speech model filename",
        )

    def binary_candidate(
        self,
        identity: SpeechBinaryIdentity,
        filename: str,
    ) -> Path | None:
        root = _trusted_root(self.data_directory)
        return _verified_file(
            root,
            self.binary_path(identity, filename),
            identity.sha256,
            identity.size_bytes,
            require_executable=True,
        )

    def model_candidate(
        self,
        identity: SpeechModelIdentity,
        filename: str,
    ) -> Path | None:
        root = _trusted_root(self.data_directory)
        return _verified_file(
            root,
            self.model_path(identity, filename),
            identity.sha256,
            identity.size_bytes,
            require_executable=False,
        )

    def ensure_directories(
        self,
        directories: Iterable[Path],
    ) -> None:
        root = _trusted_root(self.data_directory)
        for directory in directories:
            target = Path(directory)
            if not _path_is_safe(root, target):
                raise ManagedSpeechComponentError(
                    "Managed speech component path leaves the InfoMancer data directory."
                )
            current = root
            try:
                relative = target.resolve(strict=False).relative_to(root)
            except (OSError, ValueError) as exc:
                raise ManagedSpeechComponentError(
                    "Managed speech component path leaves the InfoMancer data directory."
                ) from exc

            for part in relative.parts:
                current = current / part
                try:
                    if _path_is_redirect(current):
                        raise ManagedSpeechComponentError(
                            "Managed speech component path is not a normal directory tree."
                        )
                    if current.exists():
                        if not current.is_dir():
                            raise ManagedSpeechComponentError(
                                "Managed speech component path is not a normal directory tree."
                            )
                        continue
                    try:
                        current.mkdir()
                    except FileExistsError:
                        if _path_is_redirect(current) or not current.is_dir():
                            raise ManagedSpeechComponentError(
                                "Managed speech component path is not a normal directory tree."
                            )
                except ManagedSpeechComponentError:
                    raise
                except OSError as exc:
                    raise ManagedSpeechComponentError(
                        "InfoMancer could not create its managed speech component directories."
                    ) from exc


WHISPERCPP_VERSION = "1.9.4"
WHISPERCPP_BUILD_TAG = "b5130"
WHISPERCPP_COMMIT = "927cfce34f31707e17f2bff35c349632fb9e2c3a"
WHISPERCPP_RELEASE_BASE = (
    "https://github.com/ggml-org/whisper.cpp/releases/download/"
    + WHISPERCPP_BUILD_TAG
)
WHISPERCPP_ASSETS: dict[tuple[str, str], Mapping[str, object]] = {
    ("windows", "x86_64"): {
        "filename": "whisper-bin-x64.zip",
        "format": "zip",
        "size_bytes": 8_573_270,
        "sha256": "f9ec6c52a2e949b62ab51fa21d0d497958f9e41c3010c157c4e42932d5316f3c",
    },
    ("windows", "arm64"): {
        "filename": "whisper-bin-win-cpu-arm64.zip",
        "format": "zip",
        "size_bytes": 4_361_895,
        "sha256": "799543b926ab5b6c2d60cab269a2092e0ae8d27820e9e15429e59de3699546fc",
    },
    ("linux", "x86_64"): {
        "filename": "whisper-bin-ubuntu-x64.tar.gz",
        "format": "tar.gz",
        "size_bytes": 9_793_438,
        "sha256": "53e7fd8b5764edad916b8848dd0af6abb1ff1d3b86c899e79c78652412536c32",
    },
    ("linux", "arm64"): {
        "filename": "whisper-bin-ubuntu-arm64.tar.gz",
        "format": "tar.gz",
        "size_bytes": 4_605_905,
        "sha256": "93532a0e3777f26f041ffa358ee77dd88b1a33a86847c1990745327ff335a5d6",
    },
}

WHISPER_MODEL_REVISION = "f281eb45af861ab5e5297d23694b7d46e090c02c"
WHISPER_MODEL_BASE = (
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/"
    + WHISPER_MODEL_REVISION
)
WHISPER_MODELS: dict[str, Mapping[str, object]] = {
    "base-q5_1": {
        "filename": "ggml-base-q5_1.bin",
        "size_bytes": 59_707_625,
        "sha256": "422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898",
        "multilingual": True,
        "language_scope": "multilingual",
        "original_model": "openai/whisper-base",
        "quantization": "q5_1",
    },
    "base.en-q5_1": {
        "filename": "ggml-base.en-q5_1.bin",
        "size_bytes": 59_721_011,
        "sha256": "4baf70dd0d7c4247ba2b81fafd9c01005ac77c2f9ef064e00dcf195d0e2fdd2f",
        "multilingual": False,
        "language_scope": "english",
        "original_model": "openai/whisper-base.en",
        "quantization": "q5_1",
    },
}
DEFAULT_WHISPER_MODEL = "base-q5_1"

_MAX_RUNTIME_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_RUNTIME_EXPANDED_BYTES = 192 * 1024 * 1024
_MAX_RUNTIME_FILES = 128
_MAX_MODEL_BYTES = 128 * 1024 * 1024
_MAX_COMPONENT_JSON_BYTES = 512 * 1024
_COMPONENT_LOCK = threading.Lock()

WHISPERCPP_LICENSE_TEXT = """MIT License

Copyright (c) 2023-2026 The ggml authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

OPENAI_WHISPER_LICENSE_TEXT = """MIT License

Copyright (c) 2022 OpenAI

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


@dataclass(frozen=True)
class ManagedSpeechRuntimeStatus:
    state: str
    available: bool
    version: str
    path: str
    detail: str
    can_install: bool
    can_remove: bool
    identity: SpeechBinaryIdentity | None = None


@dataclass(frozen=True)
class ManagedSpeechModelStatus:
    state: str
    available: bool
    key: str
    path: str
    detail: str
    can_install: bool
    can_remove: bool
    identity: SpeechModelIdentity


def whispercpp_platform_key() -> tuple[str, str]:
    system = platform.system().casefold()
    machine = platform.machine().casefold()
    if system == "windows":
        os_name = "windows"
    elif system == "darwin":
        os_name = "darwin"
    elif system == "linux":
        os_name = "linux"
    else:
        raise ManagedSpeechComponentError(
            f"Managed whisper.cpp is not available for operating system: {system or 'unknown'}."
        )

    if machine in {"amd64", "x86_64"}:
        architecture = "x86_64"
    elif machine in {"arm64", "aarch64"}:
        architecture = "arm64"
    else:
        raise ManagedSpeechComponentError(
            f"Managed whisper.cpp is not available for architecture: {machine or 'unknown'}."
        )
    return os_name, architecture


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _allowed_download_host(hostname: str | None, kind: str) -> bool:
    host = (hostname or "").casefold()
    if kind == "runtime":
        return host in {"github.com", "release-assets.githubusercontent.com"}
    if kind == "model":
        return (
            host == "huggingface.co"
            or host.endswith(".huggingface.co")
            or host.endswith(".hf.co")
        )
    return False


def _download_pinned(
    url: str,
    *,
    expected_size: int,
    expected_sha256: str,
    maximum_bytes: int,
    kind: str,
) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not _allowed_download_host(parsed.hostname, kind):
        raise ManagedSpeechComponentError(
            "Managed speech downloads must start from an approved pinned source."
        )
    if expected_size <= 0 or expected_size > maximum_bytes:
        raise ManagedSpeechComponentError(
            "Managed speech component metadata exceeds its configured size ceiling."
        )

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "InfoMancer-managed-speech/0.9"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            final = urlparse(response.geturl())
            if final.scheme != "https" or not _allowed_download_host(
                final.hostname,
                kind,
            ):
                raise ManagedSpeechComponentError(
                    "The managed speech download redirected outside approved hosts."
                )
            raw_length = response.headers.get("Content-Length", "").strip()
            if raw_length:
                try:
                    announced = int(raw_length)
                except ValueError as exc:
                    raise ManagedSpeechComponentError(
                        "The managed speech source returned an invalid content length."
                    ) from exc
                if announced > maximum_bytes or announced != expected_size:
                    raise ManagedSpeechComponentError(
                        "The managed speech source returned an unexpected component size."
                    )
            payload = response.read(maximum_bytes + 1)
    except ManagedSpeechComponentError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ManagedSpeechComponentError(
            "InfoMancer could not download the managed speech component."
        ) from exc

    if len(payload) != expected_size:
        raise ManagedSpeechComponentError(
            "The managed speech component did not match its pinned byte size."
        )
    if _sha256_bytes(payload) != expected_sha256:
        raise ManagedSpeechComponentError(
            "The managed speech component failed SHA-256 verification."
        )
    return payload


def _archive_member_path(name: str) -> Path:
    return _safe_relative_path(name, "Speech runtime archive member")


def _extract_runtime_archive(
    payload: bytes,
    archive_format: str,
    destination: Path,
) -> list[Path]:
    extracted: list[Path] = []
    total = 0

    def write_member(relative: Path, source: BinaryIO, size: int) -> None:
        nonlocal total
        if size < 0:
            raise ManagedSpeechComponentError(
                "The whisper.cpp archive contains an invalid member size."
            )
        total += size
        if total > _MAX_RUNTIME_EXPANDED_BYTES:
            raise ManagedSpeechComponentError(
                "The whisper.cpp archive exceeded its expanded size ceiling."
            )
        if len(extracted) >= _MAX_RUNTIME_FILES:
            raise ManagedSpeechComponentError(
                "The whisper.cpp archive contains too many files."
            )
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = source.read(size + 1)
        if len(data) != size:
            raise ManagedSpeechComponentError(
                "The whisper.cpp archive member was truncated."
            )
        target.write_bytes(data)
        extracted.append(target)

    try:
        if archive_format == "zip":
            with zipfile.ZipFile(BytesIO(payload), "r") as archive:
                for info in archive.infolist():
                    relative = _archive_member_path(info.filename)
                    mode = (info.external_attr >> 16) & 0xFFFF
                    if info.flag_bits & 0x1:
                        raise ManagedSpeechComponentError(
                            "Encrypted whisper.cpp archive members are not supported."
                        )
                    if info.is_dir():
                        (destination / relative).mkdir(parents=True, exist_ok=True)
                        continue
                    if mode and stat_module.S_ISLNK(mode):
                        raise ManagedSpeechComponentError(
                            "The whisper.cpp archive cannot contain symbolic links."
                        )
                    with archive.open(info, "r") as source:
                        write_member(relative, source, int(info.file_size))
        elif archive_format == "tar.gz":
            with tarfile.open(fileobj=BytesIO(payload), mode="r:gz") as archive:
                for member in archive.getmembers():
                    relative = _archive_member_path(member.name)
                    if member.isdir():
                        (destination / relative).mkdir(parents=True, exist_ok=True)
                        continue
                    if not member.isfile():
                        raise ManagedSpeechComponentError(
                            "The whisper.cpp archive contains an unsupported link or device."
                        )
                    source = archive.extractfile(member)
                    if source is None:
                        raise ManagedSpeechComponentError(
                            "The whisper.cpp archive member could not be read."
                        )
                    with source:
                        write_member(relative, source, int(member.size))
        else:
            raise ManagedSpeechComponentError(
                "InfoMancer does not recognize the pinned whisper.cpp archive format."
            )
    except ManagedSpeechComponentError:
        raise
    except (OSError, EOFError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise ManagedSpeechComponentError(
            "The whisper.cpp runtime archive could not be unpacked safely."
        ) from exc

    if not extracted:
        raise ManagedSpeechComponentError(
            "The whisper.cpp runtime archive did not contain any files."
        )
    return extracted


def _runtime_cli_name(os_name: str) -> str:
    return "whisper-cli.exe" if os_name == "windows" else "whisper-cli"


def _find_runtime_cli(root: Path, os_name: str) -> Path:
    name = _runtime_cli_name(os_name)
    matches = [
        item
        for item in root.rglob(name)
        if item.is_file() and not _path_is_redirect(item)
    ]
    if len(matches) != 1:
        raise ManagedSpeechComponentError(
            "The pinned whisper.cpp archive did not contain exactly one whisper-cli executable."
        )
    path = matches[0]
    if os_name != "windows":
        path.chmod(
            path.stat().st_mode
            | stat_module.S_IXUSR
            | stat_module.S_IXGRP
            | stat_module.S_IXOTH
        )
    return path


def _runtime_inventory(root: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not path.is_file():
            continue
        if _path_is_redirect(path):
            raise ManagedSpeechComponentError(
                "The managed whisper.cpp runtime contains a redirected file."
            )
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        if size <= 0:
            raise ManagedSpeechComponentError(
                "The managed whisper.cpp runtime contains an empty file."
            )
        with path.open("rb") as stream:
            digest = _sha256_stream(stream)
        records.append(
            {
                "path": relative,
                "size_bytes": int(size),
                "sha256": digest,
            }
        )
    if not records or len(records) > _MAX_RUNTIME_FILES:
        raise ManagedSpeechComponentError(
            "The managed whisper.cpp runtime inventory is invalid."
        )
    return records


def _runtime_tree_sha256(records: Iterable[Mapping[str, object]]) -> str:
    canonical = json.dumps(
        list(records),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


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


def _verify_whisper_cli(path: Path, expected_version: str = "") -> str:
    try:
        result = subprocess.run(
            [str(path), "--version"],
            cwd=str(path.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
            **_quiet_subprocess_options(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ManagedSpeechComponentError(
            "The whisper.cpp executable could not be started."
        ) from exc
    if result.returncode != 0:
        raise ManagedSpeechComponentError(
            "The whisper.cpp executable did not pass its startup self-check."
        )
    output = (
        (result.stdout or b"") + b"\n" + (result.stderr or b"")
    ).decode("utf-8", errors="replace").strip()
    if "whisper" not in output.casefold():
        raise ManagedSpeechComponentError(
            "The speech executable did not identify itself as whisper.cpp."
        )
    if expected_version and expected_version not in output:
        raise ManagedSpeechComponentError(
            "The whisper.cpp executable version did not match the pinned release."
        )
    return output[:500]


def _read_component_json(path: Path) -> Mapping[str, object] | None:
    try:
        if _path_is_redirect(path):
            return None
        info = path.stat()
        if (
            not stat_module.S_ISREG(info.st_mode)
            or info.st_size <= 0
            or info.st_size > _MAX_COMPONENT_JSON_BYTES
        ):
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _model_catalog_identity(model_key: str) -> SpeechModelIdentity:
    entry = WHISPER_MODELS.get(model_key)
    if entry is None:
        raise ManagedSpeechComponentError(
            f"Unknown managed Whisper model: {model_key}."
        )
    filename = str(entry["filename"])
    return SpeechModelIdentity(
        key=f"whisper-{model_key}",
        version=WHISPER_MODEL_REVISION,
        sha256=str(entry["sha256"]),
        size_bytes=int(entry["size_bytes"]),
        source=f"{WHISPER_MODEL_BASE}/{filename}",
        license_id="MIT",
        details={
            "repository": "ggerganov/whisper.cpp",
            "revision": WHISPER_MODEL_REVISION,
            "filename": filename,
            "multilingual": bool(entry["multilingual"]),
            "language_scope": str(entry["language_scope"]),
            "original_model": str(entry["original_model"]),
            "quantization": str(entry["quantization"]),
        },
    )

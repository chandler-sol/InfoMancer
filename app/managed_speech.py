from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat as stat_module
from typing import Iterable

from .media_identity.speech import (
    SpeechBinaryIdentity,
    SpeechIdentityError,
    SpeechModelIdentity,
)


_COMPONENT_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


class ManagedSpeechComponentError(RuntimeError):
    pass


def _safe_segment(value: str, label: str) -> str:
    normalized = str(value or "").strip()
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
            if current.exists() and current.is_symlink():
                return False
        except OSError:
            return False
        if current == root:
            break
        if current.parent == current:
            return False
        current = current.parent
    return True


def _verified_file(
    root: Path,
    path: Path,
    expected_sha256: str,
    *,
    require_executable: bool,
) -> Path | None:
    if not _path_is_safe(root, path):
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    if not stat_module.S_ISREG(stat.st_mode) or path.is_symlink():
        return None
    if require_executable and os.name != "nt" and not os.access(path, os.X_OK):
        return None

    hasher = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError:
        return None
    if hasher.hexdigest() != expected_sha256.strip().casefold():
        return None
    return path


class ManagedSpeechLayout:
    """Safe filesystem layout for separately managed speech binary/model assets."""

    def __init__(self, data_directory: Path) -> None:
        self.data_directory = Path(data_directory)

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
        return self.binary_root / key / version

    def model_directory(self, identity: SpeechModelIdentity) -> Path:
        key = _safe_segment(identity.key, "Speech model key")
        digest = _safe_segment(
            identity.sha256.strip().casefold(),
            "Speech model hash",
        )
        return self.model_root / key / digest

    def binary_path(
        self,
        identity: SpeechBinaryIdentity,
        filename: str,
    ) -> Path:
        name = _safe_segment(filename, "Speech binary filename")
        return self.binary_directory(identity) / name

    def model_path(
        self,
        identity: SpeechModelIdentity,
        filename: str,
    ) -> Path:
        name = _safe_segment(filename, "Speech model filename")
        return self.model_directory(identity) / name

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
                if current.exists():
                    if current.is_symlink() or not current.is_dir():
                        raise ManagedSpeechComponentError(
                            "Managed speech component path is not a normal directory tree."
                        )
                    continue
                current.mkdir()

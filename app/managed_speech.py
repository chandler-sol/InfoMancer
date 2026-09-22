from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat as stat_module
from typing import BinaryIO, Iterable

from .media_identity.speech import (
    SpeechBinaryIdentity,
    SpeechModelIdentity,
)


_COMPONENT_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


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


def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        stat_module.S_IFMT(value.st_mode),
        int(value.st_size),
        int(getattr(value, "st_mtime_ns", int(value.st_mtime * 1_000_000_000))),
        int(getattr(value, "st_ctime_ns", int(value.st_ctime * 1_000_000_000))),
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

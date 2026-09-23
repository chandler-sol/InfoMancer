from __future__ import annotations

import ctypes
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat as stat_module
import threading
from typing import Any, Iterator, Mapping


MEDIA_GENERATION_IDENTITY_VERSION = 1


class MediaContentLeaseError(RuntimeError):
    """Exact media content could not be leased safely for local analysis."""


def media_generation_identity(path: str | Path) -> dict[str, Any] | None:
    """Return cheap filesystem generation metadata for one regular media file."""
    candidate = Path(path)
    try:
        stat_result = candidate.stat()
        if not stat_module.S_ISREG(stat_result.st_mode):
            return None
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None

    return {
        "version": MEDIA_GENERATION_IDENTITY_VERSION,
        "path": str(resolved),
        "size_bytes": int(stat_result.st_size),
        "modified_ns": int(
            getattr(
                stat_result,
                "st_mtime_ns",
                int(float(stat_result.st_mtime) * 1_000_000_000),
            )
        ),
        "change_ns": int(
            getattr(
                stat_result,
                "st_ctime_ns",
                int(float(stat_result.st_ctime) * 1_000_000_000),
            )
        ),
        "device_id": int(getattr(stat_result, "st_dev", 0) or 0) or None,
        "inode_id": int(getattr(stat_result, "st_ino", 0) or 0) or None,
    }


def media_generation_matches(
    path: str | Path,
    expected: Mapping[str, Any],
) -> bool:
    if not isinstance(expected, Mapping):
        return False
    current = media_generation_identity(path)
    if current is None:
        return False
    try:
        version = int(expected.get("version") or 0)
        size_bytes = int(expected.get("size_bytes"))
        modified_ns = int(expected.get("modified_ns"))
        change_ns = int(expected.get("change_ns"))
    except (TypeError, ValueError):
        return False
    if version != MEDIA_GENERATION_IDENTITY_VERSION:
        return False
    if (
        str(expected.get("path") or "") != current["path"]
        or size_bytes != current["size_bytes"]
        or modified_ns != current["modified_ns"]
        or change_ns != current["change_ns"]
    ):
        return False

    for field in ("device_id", "inode_id"):
        expected_value = expected.get(field)
        if expected_value is None:
            continue
        try:
            normalized = int(expected_value)
        except (TypeError, ValueError):
            return False
        if normalized != current[field]:
            return False
    return True


def media_content_sha256(
    path: str | Path,
    *,
    expected_generation: Mapping[str, Any] | None = None,
) -> str | None:
    """Hash exact media bytes while rejecting generation changes during the read."""
    candidate = Path(path)
    generation_before = media_generation_identity(candidate)
    if generation_before is None:
        return None
    if (
        expected_generation is not None
        and dict(expected_generation) != generation_before
    ):
        return None

    try:
        digest = hashlib.sha256()
        with candidate.open("rb") as handle:
            descriptor_before = os.fstat(handle.fileno())
            if not stat_module.S_ISREG(descriptor_before.st_mode):
                return None
            while True:
                chunk = handle.read(4 * 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            descriptor_after = os.fstat(handle.fileno())
    except OSError:
        return None

    fields = ("st_size", "st_mtime_ns", "st_ctime_ns", "st_dev", "st_ino")
    for field in fields:
        if getattr(descriptor_before, field, None) != getattr(
            descriptor_after, field, None
        ):
            return None

    generation_after = media_generation_identity(candidate)
    if generation_after != generation_before:
        return None
    return digest.hexdigest()


def _windows_lease_path(path: Path) -> str:
    resolved = str(path.resolve(strict=True))
    if resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved[2:]
    return "\\\\?\\" + resolved


class MediaContentLease:
    """Hold a stable read lease over exact media bytes used by local analyzers.

    Windows creation time is not a useful in-place content generation counter.
    A read-only CreateFile handle with FILE_SHARE_READ therefore remains open
    while FFmpeg analyzes the file, preventing concurrent write/delete handles.
    POSIX keeps an open descriptor while ctime/inode generation checks detect
    in-place writes or path replacement.
    """

    def __init__(
        self,
        path: str | Path,
        expected_sha256: str,
        *,
        expected_generation: Mapping[str, Any] | None = None,
    ) -> None:
        digest = str(expected_sha256 or "").strip().casefold()
        if (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise MediaContentLeaseError(
                "Local analysis requires the exact media SHA-256 snapshot."
            )
        self.path = Path(path)
        self.expected_sha256 = digest
        self.expected_generation = (
            dict(expected_generation)
            if isinstance(expected_generation, Mapping)
            else None
        )
        self.generation: dict[str, Any] | None = None
        self._descriptor: int | None = None
        self._windows_handle: int | None = None
        self._io_lock = threading.Lock()

    def _acquire_platform_handle(self, resolved: Path) -> None:
        if os.name == "nt":
            from ctypes import wintypes

            create_file = ctypes.windll.kernel32.CreateFileW
            create_file.argtypes = (
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.LPVOID,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            )
            create_file.restype = wintypes.HANDLE
            handle = create_file(
                _windows_lease_path(resolved),
                0x80000000,  # GENERIC_READ
                0x00000001,  # FILE_SHARE_READ only: deny write/delete
                None,
                3,  # OPEN_EXISTING
                0x00000080,  # FILE_ATTRIBUTE_NORMAL
                None,
            )
            invalid = ctypes.c_void_p(-1).value
            raw_handle = int(getattr(handle, "value", handle) or 0)
            if not raw_handle or raw_handle == invalid:
                raise OSError(ctypes.get_last_error(), "CreateFileW failed")
            self._windows_handle = raw_handle
            return

        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        self._descriptor = os.open(resolved, flags)

    @staticmethod
    def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            stat_module.S_IFMT(value.st_mode),
            int(value.st_size),
            int(
                getattr(
                    value,
                    "st_mtime_ns",
                    int(float(value.st_mtime) * 1_000_000_000),
                )
            ),
            int(getattr(value, "st_dev", 0)),
            int(getattr(value, "st_ino", 0)),
        )

    def _hash_posix_descriptor(self) -> str:
        descriptor = self._descriptor
        if descriptor is None:
            raise MediaContentLeaseError(
                "The local media descriptor lease is unavailable."
            )
        with self._io_lock:
            try:
                before = os.fstat(descriptor)
                if not stat_module.S_ISREG(before.st_mode):
                    raise MediaContentLeaseError(
                        "The leased local media input is not a regular file."
                    )
                os.lseek(descriptor, 0, os.SEEK_SET)
                digest = hashlib.sha256()
                while True:
                    chunk = os.read(descriptor, 4 * 1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                after = os.fstat(descriptor)
                os.lseek(descriptor, 0, os.SEEK_SET)
            except MediaContentLeaseError:
                raise
            except OSError as exc:
                raise MediaContentLeaseError(
                    "InfoMancer could not hash the leased local media descriptor."
                ) from exc
            if self._stat_signature(before) != self._stat_signature(after):
                raise MediaContentLeaseError(
                    "The leased local media bytes changed while they were hashed."
                )
            return digest.hexdigest()

    def _hash_windows_handle(self) -> str:
        handle = self._windows_handle
        if handle is None:
            raise MediaContentLeaseError(
                "The local media Windows lease handle is unavailable."
            )
        try:
            import msvcrt
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            duplicate_handle = kernel32.DuplicateHandle
            duplicate_handle.argtypes = (
                wintypes.HANDLE,
                wintypes.HANDLE,
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.HANDLE),
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            )
            duplicate_handle.restype = wintypes.BOOL
            get_current_process = kernel32.GetCurrentProcess
            get_current_process.restype = wintypes.HANDLE
            current_process = get_current_process()
            duplicate = wintypes.HANDLE()
            if not duplicate_handle(
                current_process,
                wintypes.HANDLE(handle),
                current_process,
                ctypes.byref(duplicate),
                0,
                False,
                0x00000002,  # DUPLICATE_SAME_ACCESS
            ):
                raise OSError(ctypes.get_last_error(), "DuplicateHandle failed")
            duplicate_value = int(
                getattr(duplicate, "value", duplicate) or 0
            )
            try:
                descriptor = msvcrt.open_osfhandle(
                    duplicate_value,
                    os.O_RDONLY | getattr(os, "O_BINARY", 0),
                )
            except Exception:
                kernel32.CloseHandle(duplicate)
                raise
            with os.fdopen(descriptor, "rb", closefd=True) as stream:
                before = os.fstat(stream.fileno())
                if not stat_module.S_ISREG(before.st_mode):
                    raise MediaContentLeaseError(
                        "The leased local media input is not a regular file."
                    )
                digest = hashlib.sha256()
                while True:
                    chunk = stream.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                after = os.fstat(stream.fileno())
            if self._stat_signature(before) != self._stat_signature(after):
                raise MediaContentLeaseError(
                    "The leased local media bytes changed while they were hashed."
                )
            return digest.hexdigest()
        except MediaContentLeaseError:
            raise
        except OSError as exc:
            raise MediaContentLeaseError(
                "InfoMancer could not hash the leased local media handle."
            ) from exc

    def _hash_leased_content(self) -> str:
        if os.name == "nt":
            return self._hash_windows_handle()
        return self._hash_posix_descriptor()

    @contextmanager
    def ffmpeg_input(
        self,
    ) -> Iterator[tuple[tuple[str, ...], dict[str, Any]]]:
        """Yield FFmpeg input arguments bound to this exact leased object."""
        self.require_current()
        if os.name == "nt":
            generation = self.generation or {}
            resolved = str(generation.get("path") or "")
            if not resolved or self._windows_handle is None:
                raise MediaContentLeaseError(
                    "The local media Windows lease is unavailable."
                )
            # The lease was opened without FILE_SHARE_WRITE/DELETE. Windows
            # therefore keeps the resolved file hierarchy stable while this
            # handle remains open; use the canonical resolved path rather than
            # the caller-supplied alias.
            yield (("-i", resolved), {})
            self.require_current()
            return

        descriptor = self._descriptor
        if descriptor is None:
            raise MediaContentLeaseError(
                "The local media descriptor lease is unavailable."
            )
        with self._io_lock:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
            except OSError as exc:
                raise MediaContentLeaseError(
                    "The leased local media descriptor is not seekable."
                ) from exc
            try:
                yield (
                    ("-fd", str(descriptor), "-i", "fd:"),
                    {"pass_fds": (descriptor,)},
                )
            finally:
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                except OSError:
                    pass
        self.require_current()

    def acquire(self) -> "MediaContentLease":
        if self.generation is not None:
            return self
        generation = media_generation_identity(self.path)
        if generation is None:
            raise MediaContentLeaseError(
                "The local media file is unavailable for exact content analysis."
            )
        if (
            self.expected_generation is not None
            and generation != self.expected_generation
        ):
            raise MediaContentLeaseError(
                "The local media generation changed before exact content analysis."
            )
        try:
            resolved = Path(str(generation["path"]))
            self._acquire_platform_handle(resolved)
            digest = self._hash_leased_content()
            if digest != self.expected_sha256:
                raise MediaContentLeaseError(
                    "The local media bytes no longer match the sealed SHA-256 snapshot."
                )
            if media_generation_identity(resolved) != generation:
                raise MediaContentLeaseError(
                    "The local media generation changed while its exact bytes were verified."
                )
        except Exception:
            self.close()
            raise
        self.generation = generation
        return self

    def current(self) -> bool:
        return (
            self.generation is not None
            and media_generation_matches(self.path, self.generation)
        )

    def require_current(self) -> None:
        if not self.current():
            raise MediaContentLeaseError(
                "The local media file changed while exact content analysis was active."
            )

    def close(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        handle, self._windows_handle = self._windows_handle, None
        if handle is not None and os.name == "nt":
            try:
                ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
            except Exception:
                pass
        self.generation = None

    def __enter__(self) -> "MediaContentLease":
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

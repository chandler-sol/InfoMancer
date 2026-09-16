from __future__ import annotations

import os
import threading
from pathlib import Path


class RuntimeLeaseError(RuntimeError):
    pass


def runtime_lock_key(database_path: Path) -> tuple[str, str]:
    configured = Path(database_path)
    return (str(configured.parent.resolve(strict=False)), configured.name)


class RuntimeProcessLock:
    """Replacement-stable kernel ownership for one configured database pathname."""

    def __init__(self, database_path: Path):
        configured = Path(database_path)
        parent = configured.parent.resolve(strict=False)
        self.path = parent / f".{configured.name}.runtime.lock"
        self._fd: int | None = None
        self._guard = threading.Lock()

    @property
    def held(self) -> bool:
        with self._guard:
            return self._fd is not None

    def acquire(self) -> bool:
        """Acquire ownership, returning False only when already held by this object."""
        with self._guard:
            if self._fd is not None:
                return False
            try:
                fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            except OSError as exc:
                raise RuntimeLeaseError(
                    "InfoMancer could not secure runtime ownership beside the database. "
                    "Check application-data permissions."
                ) from exc
            try:
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
                if os.name == "nt":
                    import msvcrt

                    if os.fstat(fd).st_size < 1:
                        os.lseek(fd, 0, os.SEEK_SET)
                        os.write(fd, b"\0")
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                os.close(fd)
                raise RuntimeLeaseError(
                    "Another InfoMancer process is already using this database. "
                    "Run exactly one application process/worker per catalog."
                ) from exc
            self._fd = fd
            return True

    def release(self) -> None:
        with self._guard:
            fd = self._fd
            if fd is None:
                return
            self._fd = None
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


_STARTUP_GUARD = threading.Lock()
_STARTUP_LOCKS: dict[tuple[str, str], RuntimeProcessLock] = {}
_ADOPTED_KEYS: set[tuple[str, str]] = set()


def claim_runtime_startup_lock(database_path: Path) -> None:
    """Claim installation ownership before any database initialization can occur."""
    key = runtime_lock_key(database_path)
    with _STARTUP_GUARD:
        if key in _ADOPTED_KEYS or key in _STARTUP_LOCKS:
            return
        lock = RuntimeProcessLock(database_path)
        lock.acquire()
        _STARTUP_LOCKS[key] = lock


def take_runtime_startup_lock(database_path: Path) -> RuntimeProcessLock | None:
    """Transfer the early startup descriptor into RuntimeLease without unlocking it."""
    key = runtime_lock_key(database_path)
    with _STARTUP_GUARD:
        lock = _STARTUP_LOCKS.pop(key, None)
        if lock is not None:
            _ADOPTED_KEYS.add(key)
        return lock


def runtime_lock_released(database_path: Path) -> None:
    """Allow a later runtime in this process to claim ownership after release/failure."""
    key = runtime_lock_key(database_path)
    with _STARTUP_GUARD:
        _ADOPTED_KEYS.discard(key)

from __future__ import annotations

import os
import threading
from pathlib import Path


class RuntimeLeaseError(RuntimeError):
    pass


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

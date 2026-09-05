from __future__ import annotations

import os
import secrets
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from .db import Database


class RuntimeLeaseError(RuntimeError):
    pass


def _desktop_owner() -> str:
    host = socket.gethostname().replace(":", "_")
    return f"desktop:{host}:{os.getpid()}:{secrets.token_urlsafe(12)}"


def _desktop_owner_pid(owner: str) -> int | None:
    parts = str(owner or "").split(":", 3)
    if len(parts) != 4 or parts[0] != "desktop":
        return None
    host = socket.gethostname().replace(":", "_")
    if parts[1] != host:
        return None
    try:
        pid = int(parts[2])
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        open_process.restype = ctypes.c_void_p
        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        get_exit_code.restype = ctypes.c_int
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int

        handle = open_process(process_query_limited_information, 0, pid)
        if not handle:
            # Access denied means the PID exists but is not queryable. Treat that
            # conservatively as live so a lease is never stolen from a real process.
            return ctypes.get_last_error() == 5
        try:
            exit_code = ctypes.c_uint32()
            if not get_exit_code(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == still_active
        finally:
            close_handle(handle)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class RuntimeLease:
    """Fail closed when two InfoMancer processes try to own one catalog."""

    def __init__(
        self, database: Database, *, name: str = "web-runtime",
        ttl_seconds: int = 90, heartbeat_seconds: int = 30,
        owner: str | None = None, on_lost: Callable[[], None] | None = None,
    ):
        self.database = database
        self.name = name
        self.ttl_seconds = max(30, int(ttl_seconds))
        self.heartbeat_seconds = max(10, min(int(heartbeat_seconds), self.ttl_seconds // 2))
        if owner:
            self.owner = owner
        elif os.getenv("INFOMANCER_RUNTIME_CONTEXT", "").strip().casefold() == "desktop":
            # Desktop owns a local catalog through a short-lived bundled core. Put
            # its PID in the lease so a dead one-file worker can be distinguished
            # from a genuinely live second InfoMancer process after a restart.
            self.owner = _desktop_owner()
        else:
            self.owner = secrets.token_urlsafe(24)
        self.on_lost = on_lost or self._terminate_after_lease_loss
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _parse(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    @staticmethod
    def _terminate_after_lease_loss() -> None:
        # Ownership loss means another live process has claimed this catalog.
        # Immediate termination is safer than allowing two schedulers or
        # filesystem-mutating workers to continue against the same database.
        os._exit(70)

    def acquire(self) -> None:
        now = self._now()
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner,heartbeat_at FROM runtime_leases WHERE name=?", (self.name,)
            ).fetchone()
            if row and row["owner"] != self.owner:
                try:
                    fresh = now - self._parse(row["heartbeat_at"]) < timedelta(seconds=self.ttl_seconds)
                except (TypeError, ValueError):
                    fresh = False

                # PyInstaller one-file Windows bundles have a bootloader parent and
                # an application child. Killing only the bootloader can leave the
                # child alive briefly. Desktop leases carry the worker PID so a
                # restart can reclaim a fresh lease as soon as that PID is gone,
                # instead of forcing the user to wait for the 90-second TTL.
                desktop_pid = _desktop_owner_pid(str(row["owner"])) if fresh else None
                if desktop_pid is not None:
                    for _ in range(10):
                        if not _process_is_alive(desktop_pid):
                            fresh = False
                            break
                        time.sleep(0.05)

                if fresh:
                    raise RuntimeLeaseError(
                        "Another InfoMancer process is already using this database. "
                        "Run exactly one application process/worker per catalog."
                    )
            conn.execute(
                """INSERT INTO runtime_leases(name,owner,heartbeat_at) VALUES (?,?,?)
                   ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,
                     heartbeat_at=excluded.heartbeat_at""",
                (self.name, self.owner, now.isoformat()),
            )

    def heartbeat(self) -> None:
        with self.database.connect() as conn:
            updated = conn.execute(
                "UPDATE runtime_leases SET heartbeat_at=? WHERE name=? AND owner=?",
                (self._now().isoformat(), self.name, self.owner),
            ).rowcount
        if not updated:
            raise RuntimeLeaseError("InfoMancer lost ownership of its runtime lease.")

    def start(self) -> None:
        self.acquire()
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def run() -> None:
            while not self._stop.wait(self.heartbeat_seconds):
                try:
                    self.heartbeat()
                except RuntimeLeaseError:
                    self.on_lost()
                    return
                except Exception:
                    # A transient SQLite/storage failure is not proof that
                    # ownership changed. Retry on the next heartbeat instead of
                    # killing a healthy single process.
                    continue

        self._thread = threading.Thread(
            target=run, name="infomancer-runtime-lease", daemon=True,
        )
        self._thread.start()

    def release(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        with self.database.connect() as conn:
            conn.execute(
                "DELETE FROM runtime_leases WHERE name=? AND owner=?",
                (self.name, self.owner),
            )


class JobRegistry:
    """Own process-local task state in one explicit place."""

    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._maps: dict[str, dict] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._events: dict[str, threading.Event] = {}

    def job(self, name: str, initial: dict | None = None) -> dict:
        return self._jobs.setdefault(name, dict(initial or {"status": "idle"}))

    def mapping(self, name: str) -> dict:
        return self._maps.setdefault(name, {})

    def lock(self, name: str) -> threading.Lock:
        return self._locks.setdefault(name, threading.Lock())

    def event(self, name: str) -> threading.Event:
        return self._events.setdefault(name, threading.Event())

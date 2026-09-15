from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator


class MaintenanceGate:
    """Coordinate exclusive maintenance with normal application work.

    The gate is intentionally process-local. InfoMancer's RuntimeLease prevents
    two application processes from owning the same installation data directory.
    Every request or background worker that can touch installation state must hold
    an operation lease for its complete lifetime, including logging and cleanup.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_operations = 0
        self._exclusive_reason = ""

    def try_enter_operation(self) -> bool:
        with self._lock:
            if self._exclusive_reason:
                return False
            self._active_operations += 1
            return True

    def leave_operation(self) -> None:
        with self._lock:
            if self._active_operations <= 0:
                raise RuntimeError("MaintenanceGate operation count underflow")
            self._active_operations -= 1

    @contextmanager
    def operation_lease(self) -> Iterator[bool]:
        """Hold ordinary-work admission until the caller's final state access."""
        admitted = self.try_enter_operation()
        try:
            yield admitted
        finally:
            if admitted:
                self.leave_operation()

    def try_begin_exclusive(self, reason: str) -> bool:
        normalized = str(reason or "maintenance").strip() or "maintenance"
        with self._lock:
            if self._exclusive_reason or self._active_operations:
                return False
            self._exclusive_reason = normalized
            return True

    def end_exclusive(self) -> None:
        with self._lock:
            self._exclusive_reason = ""

    def exclusive_active(self) -> bool:
        with self._lock:
            return bool(self._exclusive_reason)

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "exclusive": bool(self._exclusive_reason),
                "reason": self._exclusive_reason,
                "active_operations": self._active_operations,
            }


APPLICATION_MAINTENANCE_GATE = MaintenanceGate()

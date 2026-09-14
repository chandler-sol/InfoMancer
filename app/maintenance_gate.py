from __future__ import annotations

import threading


class MaintenanceGate:
    """Coordinate exclusive maintenance with normal application work.

    The gate is intentionally process-local. InfoMancer's RuntimeLease already
    prevents two application processes from owning the same installation data
    directory, so the remaining race to solve is inside the active process.

    Normal application requests and short background-job start transitions
    register as active operations. Exclusive maintenance may begin only when
    no other operation is active. Once exclusive mode begins, new operations
    fail closed until maintenance is explicitly ended or the process restarts.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_operations = 0
        self._exclusive_reason = ""

    def try_enter_operation(self) -> bool:
        """Register ordinary work unless exclusive maintenance is active."""
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

    def try_begin_exclusive(self, reason: str) -> bool:
        """Atomically block new work if no ordinary work is still active."""
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


# One process-wide coordination point. RuntimeLease already guarantees a single
# InfoMancer process per catalog, so request, scheduler, and recovery code can use
# this object without adding another database-backed lock protocol.
APPLICATION_MAINTENANCE_GATE = MaintenanceGate()

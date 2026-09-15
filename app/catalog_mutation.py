from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


_LOCKS_GUARD = threading.Lock()
_ROOT_LOCKS: dict[tuple[str, int], threading.RLock] = {}
_TITLE_LOCKS: dict[tuple[str, int], threading.RLock] = {}


def database_identity(path: Path | str) -> str:
    """Return the process-local coordination key for a file-backed database."""
    return os.path.normcase(str(Path(path).resolve(strict=False)))


def connection_database_identity(conn: sqlite3.Connection) -> str:
    """Return the same coordination key from an open SQLite connection."""
    rows = conn.execute("PRAGMA database_list").fetchall()
    for row in rows:
        try:
            name = row["name"]
            filename = row["file"]
        except (IndexError, TypeError):
            name = row[1]
            filename = row[2]
        if name != "main":
            continue
        if filename:
            return database_identity(filename)
        # In-memory databases cannot coordinate with another connection anyway.
        return f"memory:{id(conn)}"
    raise RuntimeError("SQLite main database identity is unavailable.")


def _lock_for(
    registry: dict[tuple[str, int], threading.RLock],
    database_key: str,
    object_id: int,
) -> threading.RLock:
    key = (database_key, int(object_id))
    with _LOCKS_GUARD:
        return registry.setdefault(key, threading.RLock())


@contextmanager
def root_mutation_lock(database_key: str, root_id: int) -> Iterator[None]:
    """Serialize scan/catalog transactions with filesystem mutations for one root."""
    with _lock_for(_ROOT_LOCKS, database_key, root_id):
        yield


@contextmanager
def title_mutation_lock(database_key: str, title_id: int) -> Iterator[None]:
    """Serialize filesystem/catalog mutations for one title in this process."""
    with _lock_for(_TITLE_LOCKS, database_key, title_id):
        yield

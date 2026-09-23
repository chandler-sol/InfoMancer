from __future__ import annotations

import os
from pathlib import Path
import stat as stat_module
from typing import Any, Mapping


MEDIA_GENERATION_IDENTITY_VERSION = 1


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

#!/usr/bin/env python3
"""Fail when shipped Mach-O binaries require a newer macOS than supported."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

_VERSION_RE = re.compile(r"^\s*(?:minos|version)\s+([0-9]+(?:\.[0-9]+){0,2})\s*$", re.MULTILINE)


def _version_tuple(value: str) -> tuple[int, int, int]:
    parts = [int(part) for part in value.split(".")]
    return tuple((parts + [0, 0, 0])[:3])  # type: ignore[return-value]


def _candidate_files(paths: Iterable[Path]) -> Iterable[Path]:
    seen: set[Path] = set()
    for path in paths:
        if path.is_file():
            candidates = [path]
        elif path.is_dir():
            candidates = (item for item in path.rglob("*") if item.is_file())
        else:
            raise FileNotFoundError(path)
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate
            if resolved in seen:
                continue
            seen.add(resolved)
            yield candidate


def _is_macho(path: Path) -> bool:
    result = subprocess.run(
        ["/usr/bin/file", "-b", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and "Mach-O" in result.stdout


def _minimum_versions(path: Path) -> list[str]:
    result = subprocess.run(
        ["xcrun", "vtool", "-show-build", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "vtool could not inspect the binary")
    return _VERSION_RE.findall(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-version", required=True, help="Highest allowed minimum macOS version, e.g. 13.0")
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()

    allowed = _version_tuple(args.max_version)
    inspected = 0
    failures: list[str] = []
    missing_metadata: list[str] = []

    for path in _candidate_files(args.paths):
        if not _is_macho(path):
            continue
        inspected += 1
        try:
            versions = _minimum_versions(path)
        except RuntimeError as error:
            failures.append(f"{path}: {error}")
            continue
        if not versions:
            missing_metadata.append(str(path))
            continue
        print(f"{path}: minimum macOS {', '.join(versions)}")
        too_new = [version for version in versions if _version_tuple(version) > allowed]
        if too_new:
            failures.append(
                f"{path}: requires macOS {', '.join(too_new)}, newer than supported {args.max_version}"
            )

    if inspected == 0:
        print("No Mach-O binaries were found in the requested paths.", file=sys.stderr)
        return 2

    if missing_metadata:
        print("Mach-O files without readable minimum-version metadata:", file=sys.stderr)
        for path in missing_metadata:
            print(f"  {path}", file=sys.stderr)
        failures.extend(f"{path}: no minimum macOS metadata" for path in missing_metadata)

    if failures:
        print("macOS compatibility audit failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print(f"Validated {inspected} Mach-O binaries against macOS {args.max_version} compatibility.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

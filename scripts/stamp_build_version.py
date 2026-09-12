#!/usr/bin/env python3
"""Stamp an InfoMancer build version into packaging sources.

This is a build-workspace operation. CI uses it after qualification so a Dev
artifact can carry a unique version without committing generated version bumps
back to the development branch.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_PATTERN = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def replace_once(path: Path, pattern: str, replacement: str) -> None:
    source = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, source, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"Could not find exactly one version target in {path}.")
    path.write_text(updated, encoding="utf-8")


def stamp_json(path: Path, version: str, *, lock_root: bool = False) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}.")
    value["version"] = version
    if lock_root:
        packages = value.get("packages")
        if not isinstance(packages, dict) or not isinstance(packages.get(""), dict):
            raise ValueError(f"Could not find the root package in {path}.")
        packages[""]["version"] = version
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def stamp(version: str) -> None:
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError("Build version must be a semantic version without a leading v.")

    replace_once(
        ROOT / "app" / "main.py",
        r'^APP_VERSION = "[^"]+"$',
        f'APP_VERSION = "{version}"',
    )
    replace_once(
        ROOT / "desktop" / "sidecar.py",
        r'^DESKTOP_VERSION = "[^"]+"$',
        f'DESKTOP_VERSION = "{version}"',
    )
    replace_once(
        ROOT / "desktop" / "src-tauri" / "Cargo.toml",
        r'^(version\s*=\s*)"[^"]+"$',
        rf'\1"{version}"',
    )
    stamp_json(ROOT / "desktop" / "src-tauri" / "tauri.conf.json", version)
    stamp_json(ROOT / "desktop" / "package.json", version)
    stamp_json(ROOT / "desktop" / "package-lock.json", version, lock_root=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stamp InfoMancer packaging versions")
    parser.add_argument("version")
    args = parser.parse_args()
    try:
        stamp(args.version)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Could not stamp build version: {exc}")
        return 2
    print(args.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

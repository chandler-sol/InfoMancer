#!/usr/bin/env python3
"""Fail-closed npm lockfile audit fallback for registry-side npm CLI failures."""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


BULK_ADVISORY_URL = "https://registry.npmjs.org/-/npm/v1/security/advisories/bulk"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
SEVERITY_ORDER = {
    "info": 0,
    "low": 1,
    "moderate": 2,
    "high": 3,
    "critical": 4,
}


class NpmLockAuditError(RuntimeError):
    pass


def package_name_from_lock_path(path: str, entry: dict[str, Any]) -> str:
    explicit = str(entry.get("name") or "").strip()
    if explicit:
        return explicit
    marker = "node_modules/"
    if marker not in path:
        return ""
    return path.rsplit(marker, 1)[1].strip("/")


def dependency_versions(lock: dict[str, Any]) -> dict[str, list[str]]:
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        raise NpmLockAuditError(
            "package-lock.json does not contain the expected lockfileVersion 2/3 packages map."
        )

    collected: dict[str, set[str]] = {}
    for path, raw_entry in packages.items():
        if not path or not isinstance(raw_entry, dict):
            continue
        name = package_name_from_lock_path(str(path), raw_entry)
        version = str(raw_entry.get("version") or "").strip()
        if not name or not version:
            continue
        collected.setdefault(name, set()).add(version)

    if not collected:
        raise NpmLockAuditError("No versioned npm dependencies were found in package-lock.json.")
    return {
        name: sorted(versions)
        for name, versions in sorted(collected.items())
    }


def read_lockfile(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise NpmLockAuditError(f"Could not read npm lockfile: {path}") from exc
    except json.JSONDecodeError as exc:
        raise NpmLockAuditError(f"npm lockfile is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise NpmLockAuditError("npm lockfile root must be a JSON object.")
    return payload


def _decode_registry_payload(payload: bytes, content_encoding: str = "") -> Any:
    if len(payload) > MAX_RESPONSE_BYTES:
        raise NpmLockAuditError("npm advisory response exceeded the 8 MiB safety limit.")
    if payload.startswith(b"\x1f\x8b") or "gzip" in content_encoding.casefold():
        try:
            payload = gzip.decompress(payload)
        except OSError as exc:
            raise NpmLockAuditError("npm advisory response claimed gzip but could not be decoded.") from exc
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NpmLockAuditError("npm advisory response was not valid JSON.") from exc


def fetch_bulk_advisories(
    dependencies: dict[str, list[str]],
    *,
    timeout: float = 20.0,
) -> dict[str, list[dict[str, Any]]]:
    body = json.dumps(dependencies, sort_keys=True, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        BULK_ADVISORY_URL,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Content-Type": "application/json",
            "User-Agent": "InfoMancer-npm-lock-audit/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, min(timeout, 30.0))) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            encoding = response.headers.get("Content-Encoding", "")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise NpmLockAuditError(f"npm Bulk Advisory endpoint could not be reached: {exc}") from exc

    decoded = _decode_registry_payload(payload, encoding)
    if not isinstance(decoded, dict):
        raise NpmLockAuditError("npm Bulk Advisory endpoint returned an unexpected response.")
    result: dict[str, list[dict[str, Any]]] = {}
    for package, raw_advisories in decoded.items():
        if not isinstance(package, str) or not isinstance(raw_advisories, list):
            raise NpmLockAuditError("npm Bulk Advisory response has an invalid advisory shape.")
        advisories: list[dict[str, Any]] = []
        for raw in raw_advisories:
            if not isinstance(raw, dict):
                raise NpmLockAuditError("npm Bulk Advisory response contains a malformed advisory.")
            severity = str(raw.get("severity") or "").casefold()
            if severity not in SEVERITY_ORDER:
                raise NpmLockAuditError(
                    f"npm advisory for {package} has an unknown severity: {severity or '<missing>'}."
                )
            advisories.append(raw)
        result[package] = advisories
    return result


def blocking_advisories(
    advisories: dict[str, list[dict[str, Any]]],
    threshold: str,
) -> list[tuple[str, dict[str, Any]]]:
    normalized = threshold.casefold()
    if normalized not in SEVERITY_ORDER:
        raise NpmLockAuditError(f"Unsupported audit severity threshold: {threshold}")
    minimum = SEVERITY_ORDER[normalized]
    blocked: list[tuple[str, dict[str, Any]]] = []
    for package in sorted(advisories):
        for advisory in advisories[package]:
            severity = str(advisory.get("severity") or "").casefold()
            if severity not in SEVERITY_ORDER:
                raise NpmLockAuditError(
                    f"npm advisory for {package} has an unknown severity: {severity or '<missing>'}."
                )
            if SEVERITY_ORDER[severity] >= minimum:
                blocked.append((package, advisory))
    return blocked


def audit_lockfile(path: Path, threshold: str = "high") -> list[tuple[str, dict[str, Any]]]:
    dependencies = dependency_versions(read_lockfile(path))
    advisories = fetch_bulk_advisories(dependencies)
    return blocking_advisories(advisories, threshold)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit every versioned dependency in an npm package-lock.json against "
            "the npm Bulk Advisory endpoint."
        )
    )
    parser.add_argument("lockfile", type=Path)
    parser.add_argument(
        "--audit-level",
        choices=tuple(SEVERITY_ORDER),
        default="high",
    )
    args = parser.parse_args(argv)

    try:
        blocked = audit_lockfile(args.lockfile, args.audit_level)
    except NpmLockAuditError as exc:
        print(f"npm lockfile audit could not be completed safely: {exc}", file=sys.stderr)
        return 2

    if blocked:
        print(
            f"npm lockfile audit found {len(blocked)} advisory/advisories at "
            f"{args.audit_level} severity or higher:",
            file=sys.stderr,
        )
        for package, advisory in blocked:
            severity = str(advisory.get("severity") or "").upper()
            title = str(advisory.get("title") or "Unnamed advisory")
            url = str(advisory.get("url") or "")
            print(f"- {package}: {severity}: {title} {url}".rstrip(), file=sys.stderr)
        return 1

    print(
        f"npm lockfile audit passed: no advisories at {args.audit_level} severity or higher."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

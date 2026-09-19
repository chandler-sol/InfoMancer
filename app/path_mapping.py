from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable


_WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


class PathMappingError(ValueError):
    """Raised when a path mapping cannot be proven safe and unambiguous."""


@dataclass(frozen=True)
class ParsedPath:
    anchor: str
    parts: tuple[str, ...]
    windows: bool


@dataclass(frozen=True)
class PathMapping:
    source_key: str
    external_root: str
    local_root: str
    priority: int = 100
    enabled: bool = True

    def __post_init__(self) -> None:
        source_key = str(self.source_key or "").strip().casefold()
        external_root = str(self.external_root or "").strip()
        local_root = str(self.local_root or "").strip()
        if not source_key:
            raise PathMappingError("A path mapping requires a source key.")
        if not external_root:
            raise PathMappingError("A path mapping requires an external root.")
        if not local_root:
            raise PathMappingError("A path mapping requires a local root.")
        parse_absolute_path(external_root)
        local = Path(local_root).expanduser()
        if not local.is_absolute():
            raise PathMappingError("A path mapping local root must be absolute.")
        object.__setattr__(self, "source_key", source_key)
        object.__setattr__(self, "external_root", external_root)
        object.__setattr__(self, "local_root", str(local))
        object.__setattr__(self, "priority", int(self.priority))

    @property
    def external_depth(self) -> int:
        return len(parse_absolute_path(self.external_root).parts)


@dataclass(frozen=True)
class PathTranslation:
    source_key: str
    external_path: str
    local_path: str
    mapping: PathMapping


def parse_absolute_path(value: str) -> ParsedPath:
    """Parse a Windows or POSIX absolute path without using host OS semantics."""
    raw = str(value or "").strip()
    if _WINDOWS_ABSOLUTE.match(raw):
        path = PureWindowsPath(raw)
        anchor = path.anchor
        if not anchor:
            raise PathMappingError("An external media path must be absolute.")
        return ParsedPath(anchor=anchor, parts=tuple(path.parts[1:]), windows=True)

    path = PurePosixPath(raw)
    if not path.is_absolute():
        raise PathMappingError("An external media path must be absolute.")
    return ParsedPath(anchor=path.anchor, parts=tuple(path.parts[1:]), windows=False)


def relative_parts(value: str, root: str) -> tuple[str, ...]:
    """Return component-safe relative parts when value belongs to root."""
    parsed_value = parse_absolute_path(value)
    parsed_root = parse_absolute_path(root)
    if parsed_value.windows != parsed_root.windows:
        raise PathMappingError("The path uses a different style than the mapping root.")

    def comparable(part: str) -> str:
        return part.casefold() if parsed_value.windows else part

    if comparable(parsed_value.anchor) != comparable(parsed_root.anchor):
        raise PathMappingError("The path is outside the mapping root.")
    if len(parsed_value.parts) < len(parsed_root.parts):
        raise PathMappingError("The path is outside the mapping root.")
    for actual, expected in zip(parsed_value.parts, parsed_root.parts):
        if comparable(actual) != comparable(expected):
            raise PathMappingError("The path is outside the mapping root.")
    return parsed_value.parts[len(parsed_root.parts):]


def translate_path(value: str, external_root: str, local_root: str | Path) -> str:
    """Translate one external absolute path into a local absolute path."""
    relative = relative_parts(value, external_root)
    destination = Path(local_root).expanduser()
    if not destination.is_absolute():
        raise PathMappingError("A path mapping local root must be absolute.")
    return str(destination.joinpath(*relative))


class ExternalPathMapper:
    """Component-aware source path translation with deterministic precedence."""

    def __init__(self, mappings: Iterable[PathMapping] = ()) -> None:
        self._mappings = tuple(mapping for mapping in mappings if mapping.enabled)

    def mappings_for(self, source_key: str) -> tuple[PathMapping, ...]:
        key = str(source_key or "").strip().casefold()
        return tuple(mapping for mapping in self._mappings if mapping.source_key == key)

    def translate(self, source_key: str, external_path: str) -> PathTranslation | None:
        key = str(source_key or "").strip().casefold()
        matches: list[tuple[PathMapping, str]] = []
        for mapping in self.mappings_for(key):
            try:
                local_path = translate_path(
                    external_path,
                    mapping.external_root,
                    mapping.local_root,
                )
            except PathMappingError:
                continue
            matches.append((mapping, local_path))

        if not matches:
            return None

        best_priority = min(mapping.priority for mapping, _ in matches)
        priority_matches = [
            (mapping, local_path)
            for mapping, local_path in matches
            if mapping.priority == best_priority
        ]
        best_depth = max(mapping.external_depth for mapping, _ in priority_matches)
        finalists = [
            (mapping, local_path)
            for mapping, local_path in priority_matches
            if mapping.external_depth == best_depth
        ]

        destinations = {local_path for _, local_path in finalists}
        if len(destinations) != 1:
            raise PathMappingError(
                "More than one equally preferred path mapping matches this external path."
            )

        mapping, local_path = finalists[0]
        return PathTranslation(
            source_key=key,
            external_path=str(external_path),
            local_path=local_path,
            mapping=mapping,
        )
